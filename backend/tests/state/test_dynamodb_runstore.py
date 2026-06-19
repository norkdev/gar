"""DynamoDbRunStore tests — fully offline via moto (no real AWS)."""

from datetime import UTC, datetime
from typing import Any

import boto3
import pytest
from gar_backend.governance.hitl import RunState, RunStatus
from gar_backend.state.runs import TENANT_INDEX, DynamoDbRunStore
from moto import mock_aws

TABLE = "gar-runs"


def _create_table() -> Any:
    ddb = boto3.resource("dynamodb", region_name="ap-northeast-1")
    ddb.create_table(
        TableName=TABLE,
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "run_id", "KeyType": "HASH"}],
        AttributeDefinitions=[
            {"AttributeName": "run_id", "AttributeType": "S"},
            {"AttributeName": "tenant_id", "AttributeType": "S"},
            {"AttributeName": "updated_at", "AttributeType": "S"},
        ],
        GlobalSecondaryIndexes=[
            {
                "IndexName": TENANT_INDEX,
                "KeySchema": [
                    {"AttributeName": "tenant_id", "KeyType": "HASH"},
                    {"AttributeName": "updated_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    )
    return ddb.Table(TABLE)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    with mock_aws():
        yield DynamoDbRunStore(table=_create_table())


def _state(
    run_id: str = "r1",
    tenant: str = "default",
    *,
    status: RunStatus = RunStatus.AWAITING_SOURCE_SELECTION,
    updated: datetime | None = None,
) -> RunState:
    return RunState(
        run_id=run_id,
        tenant_id=tenant,
        status=status,
        context={
            "concept": "a concept",
            "directions": [{"id": 0, "size": 3, "contains_concept": True}],
        },
        pending_payload={
            "candidates": [{"source_name": "arxiv", "external_id": "1", "support": 2}]
        },
        adopted_source_ids=("arxiv:1", "arxiv:2"),
        error=None,
        updated_at=updated or datetime.now(UTC),
    )


async def test_save_and_get_round_trips(store: DynamoDbRunStore) -> None:
    s = _state()
    await store.save(s)
    got = await store.get("r1")
    assert got is not None
    assert got.run_id == s.run_id
    assert got.tenant_id == s.tenant_id
    assert got.status is RunStatus.AWAITING_SOURCE_SELECTION  # enum preserved
    assert got.context == s.context  # nested dict/list round-trips
    assert got.pending_payload == s.pending_payload
    assert got.adopted_source_ids == ("arxiv:1", "arxiv:2")  # tuple, not list
    assert got.error is None
    assert got.updated_at == s.updated_at  # tz-aware datetime preserved


async def test_get_missing_returns_none(store: DynamoDbRunStore) -> None:
    assert await store.get("nope") is None


async def test_save_overwrites_same_run(store: DynamoDbRunStore) -> None:
    await store.save(_state(status=RunStatus.SEARCHING))
    await store.save(_state(status=RunStatus.COMPLETED))  # same run_id
    got = await store.get("r1")
    assert got is not None and got.status is RunStatus.COMPLETED


async def test_list_for_tenant_orders_newest_first_and_filters(
    store: DynamoDbRunStore,
) -> None:
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    t1 = datetime(2026, 1, 2, tzinfo=UTC)
    t2 = datetime(2026, 1, 3, tzinfo=UTC)
    await store.save(_state("a1", "alice", updated=t0))
    await store.save(_state("a2", "alice", updated=t2))
    await store.save(_state("b1", "bob", updated=t1))
    out = await store.list_for_tenant("alice")
    assert [s.run_id for s in out] == ["a2", "a1"]  # newest first via GSI sort
    assert all(s.tenant_id == "alice" for s in out)  # bob excluded


async def test_list_for_tenant_empty(store: DynamoDbRunStore) -> None:
    assert await store.list_for_tenant("nobody") == []
