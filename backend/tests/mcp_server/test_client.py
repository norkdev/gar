"""GarApiClient tests: endpoint/payload mapping, headers, error translation."""

import json

import httpx
import pytest
from gar_backend.mcp_server.client import GarApiClient, GarApiError

from tests.mcp_server.conftest import make_client, recording_handler


async def test_create_run_posts_notes_content() -> None:
    rec: list[httpx.Request] = []
    client = make_client(
        recording_handler(
            {"run_id": "r1", "status": "awaiting_concept_approval"}, recorder=rec
        )
    )
    out = await client.create_run([{"path": "a.md", "content": "x"}])
    assert out["run_id"] == "r1"
    req = rec[0]
    assert req.method == "POST"
    assert req.url.path == "/runs"
    assert json.loads(req.content) == {
        "notes_content": [{"path": "a.md", "content": "x"}]
    }
    await client.aclose()


async def test_every_request_carries_mcp_client_header() -> None:
    """D-106: the audit log must attribute MCP-driven runs to the mcp surface."""
    rec: list[httpx.Request] = []
    client = make_client(recording_handler([], recorder=rec))
    await client.list_runs()
    assert rec[0].headers["x-gar-client"] == "mcp"
    await client.aclose()


async def test_api_key_sent_as_bearer_when_set() -> None:
    rec: list[httpx.Request] = []
    client = make_client(recording_handler([], recorder=rec), api_key="secret")
    await client.list_runs()
    assert rec[0].headers["authorization"] == "Bearer secret"
    await client.aclose()


async def test_no_authorization_header_without_key() -> None:
    rec: list[httpx.Request] = []
    client = make_client(recording_handler([], recorder=rec))
    await client.list_runs()
    assert "authorization" not in rec[0].headers
    await client.aclose()


async def test_gate_concept_posts_edited_concept() -> None:
    rec: list[httpx.Request] = []
    client = make_client(recording_handler({"status": "searching"}, recorder=rec))
    await client.gate_concept("r1", edited_concept="new concept")
    req = rec[0]
    assert req.url.path == "/runs/r1/gates/concept"
    assert json.loads(req.content) == {"edited_concept": "new concept"}
    await client.aclose()


async def test_gate_sources_posts_adopted_ids() -> None:
    rec: list[httpx.Request] = []
    client = make_client(recording_handler({"status": "evaluating"}, recorder=rec))
    await client.gate_sources("r1", adopted_source_ids=["arxiv:1", "arxiv:2"])
    req = rec[0]
    assert req.url.path == "/runs/r1/gates/sources"
    assert json.loads(req.content) == {"adopted_source_ids": ["arxiv:1", "arxiv:2"]}
    await client.aclose()


async def test_connect_error_becomes_readable_gar_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = GarApiClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(GarApiError) as ei:
        await client.list_runs()
    msg = str(ei.value)
    assert "Cannot reach the GAR backend" in msg
    assert "GAR_API_URL" in msg
    await client.aclose()


async def test_404_translates_to_not_found_message() -> None:
    client = make_client(
        lambda r: httpx.Response(404, json={"detail": "Run x not found"})
    )
    with pytest.raises(GarApiError) as ei:
        await client.get_run("x")
    assert "not found" in str(ei.value).lower()
    await client.aclose()


async def test_409_explains_wrong_state() -> None:
    client = make_client(
        lambda r: httpx.Response(409, json={"detail": "expected awaiting_x"})
    )
    with pytest.raises(GarApiError) as ei:
        await client.gate_concept("r1", edited_concept=None)
    assert "not in the right state" in str(ei.value)
    await client.aclose()


async def test_500_surfaces_status_code() -> None:
    client = make_client(lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(GarApiError) as ei:
        await client.list_runs()
    assert "500" in str(ei.value)
    await client.aclose()
