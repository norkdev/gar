"""GarApiClient tests: endpoint/payload mapping, headers, error translation."""

import json

import httpx
import pytest
from gar_backend.mcp_server.client import GarApiClient, GarApiError, GarApiTimeout

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


async def test_no_authorization_header_without_a_token_provider() -> None:
    rec: list[httpx.Request] = []
    client = make_client(recording_handler([], recorder=rec))  # no provider
    await client.list_runs()
    assert "authorization" not in rec[0].headers
    await client.aclose()


async def test_bearer_token_sent_when_token_provider_set() -> None:
    rec: list[httpx.Request] = []

    class _StubProvider:
        async def token(self) -> str:
            return "jwt-abc"

        async def aclose(self) -> None:
            pass

    client = make_client(recording_handler([], recorder=rec), token_provider=_StubProvider())
    await client.list_runs()
    assert rec[0].headers["authorization"] == "Bearer jwt-abc"
    await client.aclose()


async def test_m2m_provider_fetches_then_caches() -> None:
    from gar_backend.mcp_server.client import M2MTokenProvider

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})

    provider = M2MTokenProvider(
        token_endpoint="https://cognito.example/oauth2/token",
        client_id="cid",
        client_secret="sec",
        scope="gar-api/access",
        transport=httpx.MockTransport(handler),
    )
    assert await provider.token() == "tok-1"
    assert await provider.token() == "tok-1"
    assert len(calls) == 1  # second call served from cache

    sent = calls[0]
    assert sent.method == "POST"
    assert sent.headers["authorization"].startswith("Basic ")  # client_id:secret
    body = sent.content.decode()
    assert "grant_type=client_credentials" in body
    await provider.aclose()


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


async def test_read_timeout_becomes_recoverable_timeout() -> None:
    """A read timeout means the backend is still working, not that it failed —
    surfaced as the recoverable GarApiTimeout (D-104)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("read timed out")

    client = GarApiClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(GarApiTimeout) as ei:
        await client.gate_concept("r1", edited_concept=None)
    assert "poll get_run_status" in str(ei.value)
    await client.aclose()


async def test_connect_timeout_is_unreachable_not_recoverable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connect timed out")

    client = GarApiClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(GarApiError) as ei:
        await client.list_runs()
    assert not isinstance(ei.value, GarApiTimeout)
    assert "Cannot reach" in str(ei.value)
    await client.aclose()


async def test_500_surfaces_status_code() -> None:
    client = make_client(lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(GarApiError) as ei:
        await client.list_runs()
    assert "500" in str(ei.value)
    await client.aclose()
