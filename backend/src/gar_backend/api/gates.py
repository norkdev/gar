"""HTTP routes for HITL gate responses.

POST /runs/{id}/gates/concept   — gate 1: approve (optionally edit) the concept
POST /runs/{id}/gates/sources   — gate 2: select adopted sources
POST /runs/{id}/gates/report    — gate 3: approve final report (saves to disk)

After each gate transition, the agent loop resumes until the next gate or
terminal state. Errors:
- 404 if the run does not exist
- 409 if the run is not in the expected status for the gate
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from gar_backend.agent.llm import LLMClient
from gar_backend.api.agent_wiring import build_agent_context, ideas_source_for_state
from gar_backend.api.deps import (
    get_access_context,
    get_client,
    get_llm_client,
    get_public_source,
    get_request_audit_logger,
    get_run_store,
)
from gar_backend.api.runs import serialize_state
from gar_backend.api.segments import SegmentRunner, get_segment_runner
from gar_backend.governance.audit import AuditLogger
from gar_backend.governance.hitl import (
    InvalidTransition,
    approve_concept,
    approve_report,
    select_sources,
)
from gar_backend.governance.rbac import AccessContext
from gar_backend.reports.builder import save_report
from gar_backend.sources.base import PublicSource
from gar_backend.state.runs import RunStore

router = APIRouter(prefix="/runs/{run_id}/gates", tags=["gates"])


class ApproveConceptRequest(BaseModel):
    edited_concept: str | None = None


class SelectSourcesRequest(BaseModel):
    adopted_source_ids: list[str]


async def _resume(
    *,
    run_id: str,
    store: RunStore,
    audit: AuditLogger,
    llm: LLMClient,
    access: AccessContext,
    public_source: PublicSource,
    runner: SegmentRunner,
    client: str | None,
) -> dict[str, Any]:
    state = await store.get(run_id)
    if state is None:
        raise HTTPException(404, f"Run {run_id} disappeared during transition")
    ctx = build_agent_context(
        ideas=ideas_source_for_state(state),
        store=store,
        audit=audit,
        llm=llm,
        access=access,
        public_source=public_source,
    )
    # Schedule the next segment off the request; the client polls for the gate.
    await runner.schedule(run_id, ctx=ctx, client=client)
    latest = await store.get(run_id)
    return serialize_state(latest or state)


@router.post("/concept")
async def approve_concept_endpoint(
    run_id: str,
    req: ApproveConceptRequest,
    store: RunStore = Depends(get_run_store),
    audit: AuditLogger = Depends(get_request_audit_logger),
    llm: LLMClient = Depends(get_llm_client),
    access: AccessContext = Depends(get_access_context),
    public_source: PublicSource = Depends(get_public_source),
    runner: SegmentRunner = Depends(get_segment_runner),
    client: str | None = Depends(get_client),
) -> dict[str, Any]:
    state = await store.get(run_id)
    if state is None:
        raise HTTPException(404, f"Run {run_id} not found")
    try:
        new_state = approve_concept(state, edited_concept=req.edited_concept)
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc)) from exc
    await store.save(new_state)
    return await _resume(
        run_id=run_id,
        store=store,
        audit=audit,
        llm=llm,
        access=access,
        public_source=public_source,
        runner=runner,
        client=client,
    )


@router.post("/sources")
async def select_sources_endpoint(
    run_id: str,
    req: SelectSourcesRequest,
    store: RunStore = Depends(get_run_store),
    audit: AuditLogger = Depends(get_request_audit_logger),
    llm: LLMClient = Depends(get_llm_client),
    access: AccessContext = Depends(get_access_context),
    public_source: PublicSource = Depends(get_public_source),
    runner: SegmentRunner = Depends(get_segment_runner),
    client: str | None = Depends(get_client),
) -> dict[str, Any]:
    state = await store.get(run_id)
    if state is None:
        raise HTTPException(404, f"Run {run_id} not found")
    try:
        new_state = select_sources(state, adopted_source_ids=req.adopted_source_ids)
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc)) from exc
    await store.save(new_state)
    return await _resume(
        run_id=run_id,
        store=store,
        audit=audit,
        llm=llm,
        access=access,
        public_source=public_source,
        runner=runner,
        client=client,
    )


@router.post("/report")
async def approve_report_endpoint(
    run_id: str,
    store: RunStore = Depends(get_run_store),
    access: AccessContext = Depends(get_access_context),
) -> dict[str, Any]:
    state = await store.get(run_id)
    if state is None:
        raise HTTPException(404, f"Run {run_id} not found")
    try:
        new_state = approve_report(state)
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc)) from exc

    await store.save(new_state)
    response = serialize_state(new_state)

    # Vault mode: save the report to disk and append the filename to
    # .ignore so re-runs skip it. Content mode: the client is responsible
    # for persisting (Copy / Download buttons in the UI).
    if "vault_path" in state.context:
        report_content = state.pending_payload.get("report", "")
        vault_path = Path(state.context["vault_path"])
        saved_path = save_report(content=report_content, vault_path=vault_path)
        response["saved_path"] = str(saved_path)

    return response
