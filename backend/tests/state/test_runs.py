"""state/runs unit tests."""

from datetime import timedelta

from gar_backend.governance.hitl import RunStatus, start
from gar_backend.state.runs import InMemoryRunStore


async def test_save_then_get_returns_the_state() -> None:
    store = InMemoryRunStore()
    state = start(run_id="r1", tenant_id="default")
    await store.save(state)
    assert await store.get("r1") == state


async def test_get_unknown_run_id_returns_none() -> None:
    store = InMemoryRunStore()
    assert await store.get("does-not-exist") is None


async def test_save_updates_existing_run() -> None:
    store = InMemoryRunStore()
    state = start(run_id="r1", tenant_id="default")
    await store.save(state)
    # Manually replace status to simulate a transition having happened
    from dataclasses import replace

    updated = replace(state, status=RunStatus.SEARCHING)
    await store.save(updated)
    fetched = await store.get("r1")
    assert fetched is not None
    assert fetched.status is RunStatus.SEARCHING


async def test_list_for_tenant_returns_only_matching_tenant() -> None:
    store = InMemoryRunStore()
    await store.save(start("r1", "acme"))
    await store.save(start("r2", "acme"))
    await store.save(start("r3", "other-corp"))
    results = await store.list_for_tenant("acme")
    assert {s.run_id for s in results} == {"r1", "r2"}


async def test_list_for_tenant_empty_when_no_matches() -> None:
    store = InMemoryRunStore()
    await store.save(start("r1", "acme"))
    assert await store.list_for_tenant("other") == []


async def test_list_for_tenant_returns_empty_for_empty_store() -> None:
    assert await InMemoryRunStore().list_for_tenant("any") == []


async def test_list_for_tenant_sorted_by_updated_at_descending() -> None:
    from dataclasses import replace
    from datetime import datetime, timezone

    store = InMemoryRunStore()
    base = datetime(2026, 5, 24, tzinfo=timezone.utc)

    older = replace(start("r1", "acme"), updated_at=base)
    newer = replace(start("r2", "acme"), updated_at=base + timedelta(hours=1))
    middle = replace(start("r3", "acme"), updated_at=base + timedelta(minutes=30))

    # Insertion order shuffled vs expected output
    await store.save(older)
    await store.save(newer)
    await store.save(middle)

    results = await store.list_for_tenant("acme")
    assert [s.run_id for s in results] == ["r2", "r3", "r1"]


async def test_separate_stores_are_isolated() -> None:
    a = InMemoryRunStore()
    b = InMemoryRunStore()
    await a.save(start("r1", "tenant"))
    assert await b.get("r1") is None


async def test_tenant_id_is_preserved_in_stored_state() -> None:
    """Spec §10 seam #1: tenant_id stays attached on every record."""
    store = InMemoryRunStore()
    await store.save(start("r1", "acme-corp"))
    fetched = await store.get("r1")
    assert fetched is not None
    assert fetched.tenant_id == "acme-corp"
