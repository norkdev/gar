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


# Attribute on the DynamoDB item recording the S3 key of an offloaded pool.
_POOL_ATTR = "candidates_key"


def _state_body(state: RunState, *, drop_candidates: bool) -> dict[str, Any]:
    """The non-key part of a RunState, as a JSON-able dict. When the candidate
    pool is offloaded to S3, it is dropped here and pointed to by _POOL_ATTR."""
    pending = state.pending_payload
    if drop_candidates and "candidates" in pending:
        pending = {k: v for k, v in pending.items() if k != "candidates"}
    return {
        "context": state.context,
        "pending_payload": pending,
        "adopted_source_ids": list(state.adopted_source_ids),
        "error": state.error,
    }


class DynamoDbRunStore:
    """RunStore backed by DynamoDB, with the candidate pool offloaded to S3.

    Table schema: partition key ``run_id``; a GSI ``tenant-index`` (partition
    ``tenant_id``, sort ``updated_at``, ALL projection) backs ``list_for_tenant``
    newest-first. RunState is stored as a JSON document under ``state`` plus the
    queryable keys as top-level attributes — sidesteps DynamoDB's native-type
    rules (floats must be Decimal, empty-string / nested-map edge cases) and
    round-trips losslessly.

    **Pool offload (plan §10 D-204).** The sources-gate candidate pool (~300
    abstracts) can exceed DynamoDB's 400 KB item limit and is *working data*, not
    the deliverable. When ``bucket`` is configured, the pool is written to S3
    (``<tenant>/<run_id>/candidates.json``) and replaced in the item by a
    ``candidates_key`` pointer; ``get`` rehydrates it, ``list_for_tenant`` does
    not (a list needs summaries, not pools). With no bucket the pool stays inline
    (fine below 400 KB), so the store also works without S3 in dev/tests.

    boto3 is synchronous, so calls run in a worker thread to honor the async
    Protocol without blocking the event loop.
    """

    def __init__(
        self,
        *,
        table: Any = None,
        table_name: str | None = None,
        bucket: str | None = None,
        s3: Any = None,
    ) -> None:
        if table is None:
            import boto3  # lazy: keep boto3 off the in-memory / CLI import path

            name = table_name or os.environ.get("GAR_RUNS_TABLE", DEFAULT_RUNS_TABLE)
            table = boto3.resource("dynamodb").Table(name)
        self._table = table
        self._bucket = bucket or os.environ.get("GAR_STATE_BUCKET")
        self._s3 = s3

    def _s3_client(self) -> Any:
        if self._s3 is None:
            import boto3

            self._s3 = boto3.client("s3")
        return self._s3

    @staticmethod
    def _pool_key(state: RunState) -> str:
        return f"{state.tenant_id}/{state.run_id}/candidates.json"

    async def save(self, state: RunState) -> None:
        candidates = state.pending_payload.get("candidates")
        offload = bool(self._bucket and candidates)
        item: dict[str, Any] = {
            "run_id": state.run_id,
            "tenant_id": state.tenant_id,
            "status": state.status.value,
            "updated_at": state.updated_at.isoformat(),
            "state": json.dumps(
                _state_body(state, drop_candidates=offload), default=str
            ),
        }
        if offload:
            key = self._pool_key(state)
            await asyncio.to_thread(
                self._s3_client().put_object,
                Bucket=self._bucket,
                Key=key,
                Body=json.dumps(candidates, default=str).encode(),
            )
            item[_POOL_ATTR] = key
        await asyncio.to_thread(self._table.put_item, Item=item)

    async def get(self, run_id: str) -> RunState | None:
        resp = await asyncio.to_thread(self._table.get_item, Key={"run_id": run_id})
        item = resp.get("Item")
        if not item:
            return None
        return await self._hydrate(item, with_pool=True)

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
        # A list is summaries — don't fetch each run's pool back from S3.
        return [await self._hydrate(it, with_pool=False) for it in items]

    async def _hydrate(self, item: dict[str, Any], *, with_pool: bool) -> RunState:
        body = json.loads(item["state"])
        pending = body.get("pending_payload") or {}
        key = item.get(_POOL_ATTR)
        if key and with_pool and self._bucket:
            resp = await asyncio.to_thread(
                self._s3_client().get_object, Bucket=self._bucket, Key=key
            )
            raw = await asyncio.to_thread(resp["Body"].read)
            pending = {**pending, "candidates": json.loads(raw)}
        return RunState(
            run_id=item["run_id"],
            tenant_id=item["tenant_id"],
            status=RunStatus(item["status"]),
            context=body.get("context") or {},
            pending_payload=pending,
            adopted_source_ids=tuple(body.get("adopted_source_ids") or ()),
            error=body.get("error"),
            updated_at=datetime.fromisoformat(item["updated_at"]),
        )
