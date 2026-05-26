"""api/stream (SSE) endpoint tests.

End-to-end: connect to the SSE endpoint after a real run has reached a
gate (AWAITING_CONCEPT_APPROVAL). The stream should emit at minimum the
initial ``state`` event and then a ``done`` event, since the run is
already at an AWAITING_* status.
"""

import json
from pathlib import Path
from typing import Any

from tests.api.conftest import text_response


def _parse_sse(raw: str) -> list[dict[str, Any]]:
    """Parse SSE wire format into a list of {'event', 'data'} dicts."""
    events: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in raw.splitlines():
        if line.startswith("event: "):
            current["event"] = line[len("event: ") :]
        elif line.startswith("data: "):
            current["data"] = json.loads(line[len("data: ") :])
        elif not line.strip():
            if current:
                events.append(current)
                current = {}
    if current:
        events.append(current)
    return events


def test_stream_unknown_run_returns_404(api_setup: dict[str, Any]) -> None:
    response = api_setup["client"].get("/runs/unknown/events")
    assert response.status_code == 404


def test_stream_emits_initial_state_then_done_for_run_at_gate(
    api_setup: dict[str, Any],
) -> None:
    api_setup["llm"].responses.append(text_response("derived concept"))
    create = api_setup["client"].post(
        "/runs", json={"vault_path": str(api_setup["vault"])}
    )
    run_id = create.json()["run_id"]
    # The run is now at awaiting_concept_approval. The SSE stream should
    # emit the initial state and then quickly terminate with `done`.
    with api_setup["client"].stream(
        "GET", f"/runs/{run_id}/events"
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith(
            "text/event-stream"
        )
        raw = "".join(response.iter_text())

    events = _parse_sse(raw)
    types = [e["event"] for e in events]
    assert "state" in types
    assert "done" in types
    assert events[0]["data"]["status"] == "awaiting_concept_approval"
    assert events[-1]["event"] == "done"
    assert events[-1]["data"]["status"] == "awaiting_concept_approval"


def test_stream_emits_audit_events_already_in_log(
    api_setup: dict[str, Any], tmp_path: Path
) -> None:
    """Audit records produced by the agent run are visible in the stream
    once they accumulate past the initial offset and the run is at a gate."""
    api_setup["llm"].responses.append(text_response("derived concept"))
    create = api_setup["client"].post(
        "/runs", json={"vault_path": str(api_setup["vault"])}
    )
    run_id = create.json()["run_id"]

    # Append one extra audit-shaped line that matches the run_id, so the
    # stream's audit-tailing path is exercised after the run is already
    # at a gate (audit log is past the initial offset, and the tailer
    # finds new content on its first poll).
    audit_path = api_setup["audit_path"]
    extra = {
        "schema_version": "1.0",
        "timestamp": "2026-05-26T00:00:00+00:00",
        "run_id": run_id,
        "tenant_id": "default",
        "tool_name": "smoke.synthetic_event",
        "input": {},
        "output": {"note": "injected for SSE test"},
        "duration_ms": 0.0,
        "status": "ok",
        "error": None,
    }
    with audit_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(extra) + "\n")

    with api_setup["client"].stream(
        "GET", f"/runs/{run_id}/events"
    ) as response:
        raw = "".join(response.iter_text())

    events = _parse_sse(raw)
    audit_events = [e for e in events if e["event"] == "audit"]
    assert any(
        e["data"].get("tool_name") == "smoke.synthetic_event"
        for e in audit_events
    )


def test_stream_filters_audit_records_by_run_id(
    api_setup: dict[str, Any],
) -> None:
    """Records for a different run_id must not appear in this run's stream."""
    api_setup["llm"].responses.append(text_response("derived concept"))
    create = api_setup["client"].post(
        "/runs", json={"vault_path": str(api_setup["vault"])}
    )
    run_id = create.json()["run_id"]
    other = {
        "schema_version": "1.0",
        "timestamp": "2026-05-26T00:00:00+00:00",
        "run_id": "OTHER-RUN-ID",
        "tenant_id": "default",
        "tool_name": "should.not.appear",
        "input": {},
        "output": {},
        "duration_ms": 0.0,
        "status": "ok",
        "error": None,
    }
    with api_setup["audit_path"].open("a", encoding="utf-8") as f:
        f.write(json.dumps(other) + "\n")

    with api_setup["client"].stream(
        "GET", f"/runs/{run_id}/events"
    ) as response:
        raw = "".join(response.iter_text())

    events = _parse_sse(raw)
    tool_names = [e["data"].get("tool_name") for e in events if e["event"] == "audit"]
    assert "should.not.appear" not in tool_names
