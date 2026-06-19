"""main.handler routes worker events to the segment worker, else to Mangum."""

from types import SimpleNamespace
from typing import Any

import pytest
from gar_backend import main
from gar_backend.api.segments import WORKER_EVENT_KEY


def test_worker_event_runs_segment(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    async def _fake_segment(run_id: str, *, client: str | None) -> Any:
        seen["run_id"] = run_id
        seen["client"] = client
        return SimpleNamespace(status=SimpleNamespace(value="searching"))

    monkeypatch.setattr(main, "run_worker_segment", _fake_segment)

    out = main.handler(
        {WORKER_EVENT_KEY: {"run_id": "r1", "client": "mcp"}}, context=None
    )

    assert seen == {"run_id": "r1", "client": "mcp"}
    assert out == {"ok": True, "run_id": "r1", "status": "searching"}


def test_http_event_goes_to_mangum(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, Any] = {}

    def _fake_asgi(event: Any, context: Any) -> str:
        called["event"] = event
        return "asgi-response"

    monkeypatch.setattr(main, "_asgi_handler", _fake_asgi)

    # A Function URL event has no worker key → handed to Mangum untouched.
    event = {"requestContext": {"http": {"method": "GET", "path": "/healthz"}}}
    assert main.handler(event, context=None) == "asgi-response"
    assert called["event"] is event
