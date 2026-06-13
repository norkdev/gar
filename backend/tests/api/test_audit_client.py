"""API-boundary tests for the audit `client` field (D-106).

The X-GAR-Client header identifies the calling surface (web / cli / mcp).
get_request_audit_logger resolves it and binds it to a per-request logger so
every audit record written during the request is attributed to that surface.
"""

import json
from typing import Any

from fastapi import Request
from gar_backend.api.deps import client_from_request

from tests.api.conftest import text_response


def _make_request(headers: dict[str, str]) -> Request:
    """A minimal ASGI scope carrying the given headers."""
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw})


def test_client_from_request_accepts_known_surfaces() -> None:
    for name in ("web", "cli", "mcp"):
        assert client_from_request(_make_request({"X-GAR-Client": name})) == name


def test_client_from_request_missing_header_is_none() -> None:
    assert client_from_request(_make_request({})) is None


def test_client_from_request_unknown_value_is_none() -> None:
    """An arbitrary header value is not written verbatim into the log."""
    assert client_from_request(_make_request({"X-GAR-Client": "evil"})) is None
    assert client_from_request(_make_request({"X-GAR-Client": "WEB"})) is None


def _audit_clients(audit_path: Any) -> list[Any]:
    lines = audit_path.read_text().splitlines()
    return [json.loads(line)["client"] for line in lines]


def test_run_stamps_client_from_header(api_setup: dict[str, Any]) -> None:
    """A run started by the MCP surface has every audit record attributed
    to `mcp` — the concept-derivation LLM call is itself audited."""
    api_setup["llm"].responses.append(text_response("derived concept"))
    response = api_setup["client"].post(
        "/runs",
        json={"vault_path": str(api_setup["vault"])},
        headers={"X-GAR-Client": "mcp"},
    )
    assert response.status_code == 200
    clients = _audit_clients(api_setup["audit_path"])
    assert clients  # at least the llm.complete record
    assert all(c == "mcp" for c in clients)


def test_run_without_header_records_null_client(api_setup: dict[str, Any]) -> None:
    api_setup["llm"].responses.append(text_response("derived concept"))
    response = api_setup["client"].post(
        "/runs", json={"vault_path": str(api_setup["vault"])}
    )
    assert response.status_code == 200
    clients = _audit_clients(api_setup["audit_path"])
    assert clients
    assert all(c is None for c in clients)
