"""api/runs endpoint tests."""

from typing import Any

from tests.api.conftest import text_response


def test_create_run_returns_awaiting_concept_approval(
    api_setup: dict[str, Any],
) -> None:
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
    api_setup: dict[str, Any],
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


# ---------- content-mode (browser picker / Obsidian plugin) ----------


def test_create_run_accepts_notes_content_instead_of_vault_path(
    api_setup: dict[str, Any],
) -> None:
    """Content-mode: notes uploaded directly, no filesystem walk."""
    api_setup["llm"].responses.append(text_response("derived from uploaded notes"))
    response = api_setup["client"].post(
        "/runs",
        json={
            "notes_content": [
                {"path": "vault/idea-1.md", "content": "An idea about retrieval."},
                {"path": "vault/idea-2.md", "content": "An idea about grounding."},
            ]
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "awaiting_concept_approval"
    assert data["pending_payload"]["concept"] == "derived from uploaded notes"
    # vault_path is NOT in context for content-mode runs
    assert "vault_path" not in data["context"]
    assert "notes_content" in data["context"]


def test_create_run_rejects_request_with_neither_field(
    api_setup: dict[str, Any],
) -> None:
    """Pydantic validator rejects empty bodies (and bodies with both fields)."""
    response = api_setup["client"].post("/runs", json={})
    assert response.status_code == 422


def test_create_run_rejects_request_with_both_fields(
    api_setup: dict[str, Any],
) -> None:
    response = api_setup["client"].post(
        "/runs",
        json={
            "vault_path": str(api_setup["vault"]),
            "notes_content": [{"path": "x.md", "content": "y"}],
        },
    )
    assert response.status_code == 422


def test_create_run_content_mode_fails_when_no_documents_provided(
    api_setup: dict[str, Any],
) -> None:
    """Empty list of notes should surface as a run failure (no content to summarize)."""
    response = api_setup["client"].post("/runs", json={"notes_content": []})
    # The Pydantic model allows an empty list (it's still a list); the
    # phase fails internally and we land in a failed state.
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "failed"
    assert "No readable idea documents" in (data["error"] or "")
