"""api/gates endpoint tests — drive a full run through the 3 HITL gates."""

from pathlib import Path
from typing import Any

from tests.api.conftest import text_response


def _start_run(api_setup: dict[str, Any]) -> str:
    api_setup["llm"].responses.append(text_response("derived concept"))
    response = api_setup["client"].post(
        "/runs", json={"vault_path": str(api_setup["vault"])}
    )
    return response.json()["run_id"]


def test_approve_concept_advances_to_source_selection(
    api_setup: dict[str, Any],
) -> None:
    run_id = _start_run(api_setup)
    api_setup["llm"].responses.append(text_response("done searching"))

    response = api_setup["client"].post(f"/runs/{run_id}/gates/concept", json={})
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "awaiting_source_selection"


def test_approve_concept_with_edit_stores_edited_text(
    api_setup: dict[str, Any],
) -> None:
    run_id = _start_run(api_setup)
    api_setup["llm"].responses.append(text_response("done"))

    api_setup["client"].post(
        f"/runs/{run_id}/gates/concept",
        json={"edited_concept": "user-rewritten concept"},
    )
    get_resp = api_setup["client"].get(f"/runs/{run_id}")
    assert get_resp.json()["context"]["concept"] == "user-rewritten concept"


def test_approve_concept_on_unknown_run_returns_404(api_setup: dict[str, Any]) -> None:
    response = api_setup["client"].post("/runs/missing/gates/concept", json={})
    assert response.status_code == 404


def test_approve_concept_on_wrong_status_returns_409(api_setup: dict[str, Any]) -> None:
    """Repeated concept approval after the first one advances should fail."""
    run_id = _start_run(api_setup)
    api_setup["llm"].responses.append(text_response("done"))
    api_setup["client"].post(f"/runs/{run_id}/gates/concept", json={})

    second = api_setup["client"].post(f"/runs/{run_id}/gates/concept", json={})
    assert second.status_code == 409


def test_select_sources_advances_and_records_adopted(api_setup: dict[str, Any]) -> None:
    run_id = _start_run(api_setup)
    api_setup["llm"].responses.extend(
        [
            text_response("done searching"),
            text_response("# Report draft"),
        ]
    )
    api_setup["client"].post(f"/runs/{run_id}/gates/concept", json={})

    response = api_setup["client"].post(
        f"/runs/{run_id}/gates/sources",
        json={"adopted_source_ids": ["public_src:1.1", "public_src:2.2"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "awaiting_report_approval"
    assert data["adopted_source_ids"] == ["public_src:1.1", "public_src:2.2"]
    assert "# Report draft" in data["pending_payload"]["report"]


def test_select_sources_on_wrong_status_returns_409(api_setup: dict[str, Any]) -> None:
    run_id = _start_run(api_setup)
    response = api_setup["client"].post(
        f"/runs/{run_id}/gates/sources",
        json={"adopted_source_ids": []},
    )
    assert response.status_code == 409


def test_approve_report_completes_and_saves_to_disk(api_setup: dict[str, Any]) -> None:
    run_id = _start_run(api_setup)
    api_setup["llm"].responses.extend(
        [
            text_response("done searching"),
            text_response("# Final Report Body"),
        ]
    )
    api_setup["client"].post(f"/runs/{run_id}/gates/concept", json={})
    api_setup["client"].post(
        f"/runs/{run_id}/gates/sources",
        json={"adopted_source_ids": []},
    )

    response = api_setup["client"].post(f"/runs/{run_id}/gates/report")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert "saved_path" in data

    saved = Path(data["saved_path"])
    assert saved.exists()
    assert "# Final Report Body" in saved.read_text()

    # .ignore should now list the saved file
    ignore = api_setup["vault"] / ".ignore"
    assert ignore.exists()
    assert saved.name in ignore.read_text()


def test_approve_report_on_wrong_status_returns_409(api_setup: dict[str, Any]) -> None:
    run_id = _start_run(api_setup)
    response = api_setup["client"].post(f"/runs/{run_id}/gates/report")
    assert response.status_code == 409
