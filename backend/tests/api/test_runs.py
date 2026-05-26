"""api/runs endpoint tests."""

from typing import Any

from tests.api.conftest import text_response


def test_create_run_returns_awaiting_concept_approval(api_setup: dict[str, Any]) -> None:
    api_setup["llm"].responses.append(text_response("derived concept"))
    response = api_setup["client"].post(
        "/runs", json={"vault_path": str(api_setup["vault"])}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "awaiting_concept_approval"
    assert data["pending_payload"]["concept"] == "derived concept"
    assert "run_id" in data
    assert data["tenant_id"] == "default"


def test_create_run_with_nonexistent_vault_returns_400(
    api_setup: dict[str, Any]
) -> None:
    response = api_setup["client"].post(
        "/runs", json={"vault_path": "/does/not/exist/at/all"}
    )
    assert response.status_code == 400


def test_get_run_returns_persisted_state(api_setup: dict[str, Any]) -> None:
    api_setup["llm"].responses.append(text_response("c"))
    create = api_setup["client"].post(
        "/runs", json={"vault_path": str(api_setup["vault"])}
    )
    run_id = create.json()["run_id"]

    response = api_setup["client"].get(f"/runs/{run_id}")
    assert response.status_code == 200
    assert response.json()["run_id"] == run_id


def test_get_unknown_run_returns_404(api_setup: dict[str, Any]) -> None:
    response = api_setup["client"].get("/runs/missing-id")
    assert response.status_code == 404


def test_list_runs_returns_tenant_runs(api_setup: dict[str, Any]) -> None:
    api_setup["llm"].responses.append(text_response("c"))
    create = api_setup["client"].post(
        "/runs", json={"vault_path": str(api_setup["vault"])}
    )
    run_id = create.json()["run_id"]

    response = api_setup["client"].get("/runs")
    assert response.status_code == 200
    runs = response.json()
    assert len(runs) == 1
    assert runs[0]["run_id"] == run_id


def test_list_runs_empty_when_no_runs(api_setup: dict[str, Any]) -> None:
    response = api_setup["client"].get("/runs")
    assert response.status_code == 200
    assert response.json() == []


def test_healthz_still_works(api_setup: dict[str, Any]) -> None:
    """Ensure router registration didn't break the existing healthz."""
    response = api_setup["client"].get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
