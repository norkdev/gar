"""GET /runs/{id}/activity — the polled progress feed that replaces the SSE
stream behind the Function URL. Drives a real run to a gate (which writes audit
records), then reads them back as human-readable lines."""

import json
from typing import Any

from gar_backend.governance.rbac import AccessContext

from tests.api.conftest import text_response


def _start_run(api_setup: dict[str, Any]) -> str:
    api_setup["llm"].responses.append(text_response("derived concept"))
    create = api_setup["client"].post(
        "/runs", json={"vault_path": str(api_setup["vault"])}
    )
    assert create.status_code == 200
    return create.json()["run_id"]


def test_activity_unknown_run_returns_404(api_setup: dict[str, Any]) -> None:
    assert api_setup["client"].get("/runs/nope/activity").status_code == 404


def test_activity_returns_human_readable_lines(api_setup: dict[str, Any]) -> None:
    run_id = _start_run(api_setup)
    resp = api_setup["client"].get(f"/runs/{run_id}/activity")
    assert resp.status_code == 200
    body = resp.json()
    # Deriving the concept makes at least one llm.complete record.
    assert body["total"] >= 1
    assert len(body["items"]) == body["total"]
    assert any(it["text"] == "Reasoning about the next step" for it in body["items"])
    assert all(
        {"timestamp", "tool", "text", "status"} <= it.keys() for it in body["items"]
    )


def test_activity_since_returns_only_new(api_setup: dict[str, Any]) -> None:
    run_id = _start_run(api_setup)
    total = api_setup["client"].get(f"/runs/{run_id}/activity").json()["total"]
    resp = api_setup["client"].get(f"/runs/{run_id}/activity?since={total}")
    body = resp.json()
    assert body["total"] == total  # full count unchanged
    assert body["items"] == []  # nothing new past what the client has


def test_activity_maps_search_and_grounding_lines(api_setup: dict[str, Any]) -> None:
    """The mapper renders search + grounding records as friendly, source-generic
    lines (no raw note content)."""
    run_id = _start_run(api_setup)
    for line in (
        {
            "run_id": run_id,
            "tenant_id": "default",
            "tool_name": "search_arxiv",
            "input": {"query": "graph neural networks"},
            "output": {"result_count": 12},
            "status": "ok",
            "timestamp": "2026-06-22T00:00:01+00:00",
        },
        {
            "run_id": run_id,
            "tenant_id": "default",
            "tool_name": "grounding.validate",
            "input": {},
            "output": {"is_valid": True, "unknown_count": 0},
            "status": "ok",
            "timestamp": "2026-06-22T00:00:02+00:00",
        },
    ):
        with api_setup["audit_path"].open("a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")

    items = api_setup["client"].get(f"/runs/{run_id}/activity").json()["items"]
    texts = [it["text"] for it in items]
    assert any("Searching arxiv" in t and "12 results" in t for t in texts)
    assert any("graph neural networks" in t for t in texts)
    assert any("grounded in a cited source" in t for t in texts)


def test_activity_requires_ownership(api_setup: dict[str, Any]) -> None:
    """Another user in the same tenant gets 404 (idea-privacy axis, D-202)."""
    run_id = _start_run(api_setup)
    from gar_backend.api.deps import get_access_context
    from gar_backend.main import app

    app.dependency_overrides[get_access_context] = lambda: AccessContext(
        tenant_id="default", user_id="someone-else", role="owner"
    )
    assert api_setup["client"].get(f"/runs/{run_id}/activity").status_code == 404
