"""make_run_store env selection (no AWS calls — construction only)."""

import pytest
from gar_backend.state.runs import DynamoDbRunStore, InMemoryRunStore, make_run_store


def test_defaults_to_in_memory() -> None:
    # conftest clears GAR_RUNS_TABLE → nothing configured → in-memory.
    assert isinstance(make_run_store(), InMemoryRunStore)


def test_selects_dynamodb_when_table_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GAR_RUNS_TABLE", "gar-runs")
    monkeypatch.setenv(
        "AWS_DEFAULT_REGION", "ap-northeast-1"
    )  # resource needs a region
    assert isinstance(make_run_store(), DynamoDbRunStore)
