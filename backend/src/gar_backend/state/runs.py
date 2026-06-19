"""Run lifecycle persistence.

v1: in-memory dict per backend process. The `RunStore` Protocol shape is
async so a DynamoDB-backed implementation can drop in for Phase 1+ without
touching call sites (spec §10 seam #3). Every record carries `tenant_id`
(seam #1); `list_for_tenant` is the multi-tenant filter point — for v1 it
is exercised but always against a single tenant.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from typing import Any, Protocol

from gar_backend.governance.hitl import RunState, RunStatus


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


# ---- DynamoDB-backed store (seam #3 — the AWS / v2 swap) ----

DEFAULT_RUNS_TABLE = "gar-runs"
TENANT_INDEX = "tenant-index"


def _to_item(state: RunState) -> dict[str, Any]:
    """Serialize a RunState to a DynamoDB item.

    The queryable keys (run_id / tenant_id / status / updated_at) are top-level
    attributes; the rest of the state is one JSON document under ``state``.
    Storing the body as JSON sidesteps DynamoDB's native-type rules (floats must
    be Decimal, empty-string / nested-map edge cases) and round-trips losslessly.
    """
    return {
        "run_id": state.run_id,
        "tenant_id": state.tenant_id,
        "status": state.status.value,
        "updated_at": state.updated_at.isoformat(),
        "state": json.dumps(
            {
                "context": state.context,
                "pending_payload": state.pending_payload,
                "adopted_source_ids": list(state.adopted_source_ids),
                "error": state.error,
            },
            default=str,
        ),
    }


def _from_item(item: dict[str, Any]) -> RunState:
    body = json.loads(item["state"])
    return RunState(
        run_id=item["run_id"],
        tenant_id=item["tenant_id"],
        status=RunStatus(item["status"]),
        context=body.get("context") or {},
        pending_payload=body.get("pending_payload") or {},
        adopted_source_ids=tuple(body.get("adopted_source_ids") or ()),
        error=body.get("error"),
        updated_at=datetime.fromisoformat(item["updated_at"]),
    )


class DynamoDbRunStore:
    """RunStore backed by a DynamoDB table — the seam-#3 swap for AWS (v2).

    Table schema: partition key ``run_id``; a GSI ``tenant-index`` (partition
    ``tenant_id``, sort ``updated_at``, ALL projection) backs
    ``list_for_tenant`` newest-first. The CDK DataStack creates the table;
    tests inject a moto-created one.

    boto3 is synchronous, so each call runs in a worker thread to honor the
    async Protocol without blocking the event loop.
    """

    def __init__(self, *, table: Any = None, table_name: str | None = None) -> None:
        if table is None:
            import boto3  # lazy: keep boto3 off the in-memory / CLI import path

            name = table_name or os.environ.get("GAR_RUNS_TABLE", DEFAULT_RUNS_TABLE)
            table = boto3.resource("dynamodb").Table(name)
        self._table = table

    async def save(self, state: RunState) -> None:
        await asyncio.to_thread(self._table.put_item, Item=_to_item(state))

    async def get(self, run_id: str) -> RunState | None:
        resp = await asyncio.to_thread(self._table.get_item, Key={"run_id": run_id})
        item = resp.get("Item")
        return _from_item(item) if item else None

    async def list_for_tenant(self, tenant_id: str) -> list[RunState]:
        from boto3.dynamodb.conditions import Key

        items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {
            "IndexName": TENANT_INDEX,
            "KeyConditionExpression": Key("tenant_id").eq(tenant_id),
            "ScanIndexForward": False,  # updated_at descending → newest first
        }
        while True:
            resp = await asyncio.to_thread(self._table.query, **kwargs)
            items.extend(resp.get("Items", []))
            start = resp.get("LastEvaluatedKey")
            if not start:
                break
            kwargs["ExclusiveStartKey"] = start
        return [_from_item(it) for it in items]
