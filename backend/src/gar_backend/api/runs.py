"""HTTP routes for run lifecycle (start, get, list).

POST /runs       — create a new run, drive the agent to the first gate
GET  /runs       — list the caller's own runs (tenant + owner, D-202)
GET  /runs/{id}  — fetch one run's state (404 unless the caller owns it)
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, model_validator

from gar_backend.agent.llm import LLMClient
from gar_backend.agent.loop import create_run
from gar_backend.api.access import authorize_run
from gar_backend.api.agent_wiring import build_agent_context, ideas_source_for_state
from gar_backend.api.deps import (
    get_access_context,
    get_client,
    get_llm_client,
    get_public_source,
    get_request_audit_logger,
    get_run_store,
)
from gar_backend.api.segments import SegmentRunner, get_segment_runner
from gar_backend.governance.audit import AuditLogger, AuditRecord
from gar_backend.governance.hitl import (
    RunState,
    RunStatus,
    report_of,
    revise_concept,
    revise_sources,
)
from gar_backend.governance.rbac import AccessContext
from gar_backend.sources.base import PublicSource
from gar_backend.state.runs import RunStore

router = APIRouter(prefix="/runs", tags=["runs"])


class NoteInput(BaseModel):
    """One uploaded idea note. ``path`` is a display label, not a filesystem path."""

    path: str
    content: str


class CreateRunRequest(BaseModel):
    """Start a new run.

    Provide exactly one of:
    - ``vault_path``: a filesystem path the backend can read (local mode).
    - ``notes_content``: the note contents uploaded by the client (picker /
      Obsidian plugin / future remote mode). No filesystem access happens.
    """

    vault_path: str | None = None
    notes_content: list[NoteInput] | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> CreateRunRequest:
        if (self.vault_path is None) == (self.notes_content is None):
            raise ValueError("provide exactly one of vault_path or notes_content")
        return self


def serialize_state(state: RunState) -> dict[str, Any]:
    return {
        "run_id": state.run_id,
        "tenant_id": state.tenant_id,
        "owner_user_id": state.owner_user_id,
        "status": state.status.value,
        "context": state.context,
        "pending_payload": state.pending_payload,
        "adopted_source_ids": list(state.adopted_source_ids),
        "error": state.error,
        "updated_at": state.updated_at.isoformat(),
    }


def serialize_summary(state: RunState) -> dict[str, Any]:
    """Lean projection for the session list (D-204): the concept and a
    has-report flag, but not the report body or the candidate pool — so listing
    many sessions stays light. Fetch one with GET /runs/{id} for the full state."""
    report, _ = report_of(state)
    return {
        "run_id": state.run_id,
        "tenant_id": state.tenant_id,
        "owner_user_id": state.owner_user_id,
        "status": state.status.value,
        "concept": state.context.get("concept"),
        "has_report": report is not None,
        "error": state.error,
        "updated_at": state.updated_at.isoformat(),
    }


@router.post("")
async def create_run_endpoint(
    req: CreateRunRequest,
    store: RunStore = Depends(get_run_store),
    audit: AuditLogger = Depends(get_request_audit_logger),
    llm: LLMClient = Depends(get_llm_client),
    access: AccessContext = Depends(get_access_context),
    public_source: PublicSource = Depends(get_public_source),
    runner: SegmentRunner = Depends(get_segment_runner),
    client: str | None = Depends(get_client),
) -> dict[str, Any]:
    run_id = str(uuid.uuid4())

    if req.vault_path is not None:
        vault_path = Path(req.vault_path)
        if not vault_path.exists():
            raise HTTPException(
                status_code=400, detail=f"vault_path does not exist: {vault_path}"
            )
        state = create_run(
            run_id=run_id,
            tenant_id=access.tenant_id,
            owner_user_id=access.user_id,
            vault_path=vault_path,
        )
    else:
        assert req.notes_content is not None  # validator guarantees
        state = create_run(
            run_id=run_id,
            tenant_id=access.tenant_id,
            owner_user_id=access.user_id,
            notes_content=[
                {"path": n.path, "content": n.content} for n in req.notes_content
            ],
        )

    await store.save(state)

    ctx = build_agent_context(
        ideas=ideas_source_for_state(state),
        store=store,
        audit=audit,
        llm=llm,
        access=access,
        public_source=public_source,
    )
    # The segment runs off the request: schedule it, then return the latest
    # snapshot. The client polls GET /runs/{id} until a gate or terminal state.
    await runner.schedule(run_id, ctx=ctx, client=client)
    latest = await store.get(run_id)
    return serialize_state(latest or state)


@router.get("/{run_id}")
async def get_run_endpoint(
    run_id: str,
    store: RunStore = Depends(get_run_store),
    access: AccessContext = Depends(get_access_context),
) -> dict[str, Any]:
    state = await store.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    authorize_run(state, access)  # 404 if not the caller's run (tenant + owner)
    return serialize_state(state)


@router.get("/{run_id}/report")
async def get_run_report_endpoint(
    run_id: str,
    store: RunStore = Depends(get_run_store),
    access: AccessContext = Depends(get_access_context),
) -> dict[str, Any]:
    """The composed report markdown + validation, at the gate or after
    completion (D-204 — a session keeps its deliverable). 404 if none yet."""
    state = await store.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    authorize_run(state, access)
    report, validation = report_of(state)
    if report is None:
        raise HTTPException(status_code=404, detail="No report for this run yet")
    return {
        "run_id": run_id,
        "status": state.status.value,
        "report": report,
        "report_validation": validation,
    }


# Which reverse transition applies at each gate (D-207). A status not here
# can't go back (the concept gate's "back" is just abandoning the run).
_BACK_TRANSITIONS = {
    RunStatus.AWAITING_SOURCE_SELECTION: revise_concept,  # → concept gate
    RunStatus.AWAITING_REPORT_APPROVAL: revise_sources,  # → sources gate
}


@router.post("/{run_id}/back")
async def back_endpoint(
    run_id: str,
    store: RunStore = Depends(get_run_store),
    access: AccessContext = Depends(get_access_context),
    audit: AuditLogger = Depends(get_request_audit_logger),
) -> dict[str, Any]:
    """Step back one gate (D-207): from the sources gate to revise the concept,
    or from the report gate to re-select sources. 409 if the run is not at a
    gate that can go back. A pure transition — no agent segment runs here; the
    forward re-run happens when the human re-approves the gate."""
    state = await store.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    authorize_run(state, access)
    transition = _BACK_TRANSITIONS.get(state.status)
    if transition is None:
        raise HTTPException(
            status_code=409, detail=f"Cannot go back from status {state.status.value}"
        )
    new_state = transition(state)
    await store.save(new_state)
    audit.log(
        AuditRecord(
            run_id=run_id,
            tenant_id=state.tenant_id,
            tool_name="go_back",
            input={"from": state.status.value, "to": new_state.status.value},
        )
    )
    return serialize_state(new_state)


@router.delete("/{run_id}", status_code=204)
async def delete_run_endpoint(
    run_id: str,
    store: RunStore = Depends(get_run_store),
    access: AccessContext = Depends(get_access_context),
) -> None:
    """Delete a session — purge the run record + its S3 objects (D-204
    right-to-be-forgotten). 404 unless the caller owns it."""
    state = await store.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    authorize_run(state, access)
    await store.delete(run_id)


@router.get("")
async def list_runs_endpoint(
    store: RunStore = Depends(get_run_store),
    access: AccessContext = Depends(get_access_context),
) -> list[dict[str, Any]]:
    # list_for_tenant enforces the isolation axis; filter the idea-privacy axis
    # (the caller's own runs) here. Sharing would relax this filter later.
    states = await store.list_for_tenant(access.tenant_id)
    return [serialize_summary(s) for s in states if s.owner_user_id == access.user_id]
