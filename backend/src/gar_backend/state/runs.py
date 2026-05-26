"""Run lifecycle persistence.

v1: in-memory dict per backend process. The `RunStore` Protocol shape is
async so a DynamoDB-backed implementation can drop in for Phase 1+ without
touching call sites (spec §10 seam #3). Every record carries `tenant_id`
(seam #1); `list_for_tenant` is the multi-tenant filter point — for v1 it
is exercised but always against a single tenant.
"""

from typing import Protocol

from gar_backend.governance.hitl import RunState


class RunStore(Protocol):
    """Persistence layer for RunState."""

    async def save(self, state: RunState) -> None: ...
    async def get(self, run_id: str) -> RunState | None: ...
    async def list_for_tenant(self, tenant_id: str) -> list[RunState]: ...


class InMemoryRunStore:
    """v1 in-memory implementation. Per-process, lost on restart.

    For Phase 1+ multi-Lambda deployments, replace with a DynamoDB-backed
    implementation of the same Protocol.
    """

    def __init__(self) -> None:
        self._store: dict[str, RunState] = {}

    async def save(self, state: RunState) -> None:
        self._store[state.run_id] = state

    async def get(self, run_id: str) -> RunState | None:
        return self._store.get(run_id)

    async def list_for_tenant(self, tenant_id: str) -> list[RunState]:
        return sorted(
            (s for s in self._store.values() if s.tenant_id == tenant_id),
            key=lambda s: s.updated_at,
            reverse=True,
        )
