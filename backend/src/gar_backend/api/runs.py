"""HTTP routes for run lifecycle (start, get, list).

POST /runs       — create a new run, drive the agent to the first gate
GET  /runs       — list runs for the current tenant
GET  /runs/{id}  — fetch one run's state
"""

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from gar_backend.agent.llm import LLMClient
from gar_backend.agent.loop import AgentContext, create_run, run_until_gate
from gar_backend.agent.tools import register_default_tools
from gar_backend.api.deps import (
    get_access_context,
    get_audit_logger,
    get_llm_client,
    get_public_source,
    get_run_store,
)
from gar_backend.governance.audit import AuditLogger
from gar_backend.governance.hitl import RunState
from gar_backend.governance.rbac import AccessContext, ToolRegistry
from gar_backend.ideas.search import IdeasSource
from gar_backend.sources.base import PublicSource
from gar_backend.state.runs import RunStore

router = APIRouter(prefix="/runs", tags=["runs"])


class CreateRunRequest(BaseModel):
    vault_path: str


def serialize_state(state: RunState) -> dict[str, Any]:
    return {
        "run_id": state.run_id,
        "tenant_id": state.tenant_id,
        "status": state.status.value,
        "context": state.context,
        "pending_payload": state.pending_payload,
        "adopted_source_ids": list(state.adopted_source_ids),
        "error": state.error,
        "updated_at": state.updated_at.isoformat(),
    }


def build_agent_context(
    *,
    vault_path: Path,
    store: RunStore,
    audit: AuditLogger,
    llm: LLMClient,
    access: AccessContext,
    public_source: PublicSource,
) -> AgentContext:
    """Wire AgentContext for a given vault. Public so api/gates.py can reuse.

    ``public_source`` is injected (not created here) so a single
    process-wide instance can enforce its provider's rate-limit policy
    across all requests.
    """
    registry = ToolRegistry()
    register_default_tools(
        registry,
        public_source=public_source,
        ideas=IdeasSource(vault_path),
    )
    return AgentContext(
        llm=llm,
        registry=registry,
        audit=audit,
        store=store,
        access=access,
    )


@router.post("")
async def create_run_endpoint(
    req: CreateRunRequest,
    store: RunStore = Depends(get_run_store),
    audit: AuditLogger = Depends(get_audit_logger),
    llm: LLMClient = Depends(get_llm_client),
    access: AccessContext = Depends(get_access_context),
    public_source: PublicSource = Depends(get_public_source),
) -> dict[str, Any]:
    vault_path = Path(req.vault_path)
    if not vault_path.exists():
        raise HTTPException(
            status_code=400, detail=f"vault_path does not exist: {vault_path}"
        )

    run_id = str(uuid.uuid4())
    state = create_run(
        run_id=run_id,
        tenant_id=access.tenant_id,
        vault_path=vault_path,
    )
    await store.save(state)

    ctx = build_agent_context(
        vault_path=vault_path,
        store=store,
        audit=audit,
        llm=llm,
        access=access,
        public_source=public_source,
    )
    final_state = await run_until_gate(run_id=run_id, ctx=ctx)
    return serialize_state(final_state)


@router.get("/{run_id}")
async def get_run_endpoint(
    run_id: str,
    store: RunStore = Depends(get_run_store),
) -> dict[str, Any]:
    state = await store.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return serialize_state(state)


@router.get("")
async def list_runs_endpoint(
    store: RunStore = Depends(get_run_store),
    access: AccessContext = Depends(get_access_context),
) -> list[dict[str, Any]]:
    states = await store.list_for_tenant(access.tenant_id)
    return [serialize_state(s) for s in states]
