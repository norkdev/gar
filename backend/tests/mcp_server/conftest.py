"""Shared helpers for mcp_server tests.

All offline: the GarApiClient is driven by an httpx.MockTransport, so no live
backend (and no Cognito) is needed (plan §2.3).
"""

from collections.abc import Callable
from typing import Any

import httpx
from gar_backend.mcp_server.client import GarApiClient, TokenProvider
from gar_backend.mcp_server.tools import McpTool, make_tools

Handler = Callable[[httpx.Request], httpx.Response]


def make_client(
    handler: Handler, *, token_provider: TokenProvider | None = None
) -> GarApiClient:
    """A GarApiClient whose requests are answered by ``handler``. No token
    provider by default → no Authorization header (local / unauth backend)."""
    return GarApiClient(
        base_url="http://test",
        transport=httpx.MockTransport(handler),
        token_provider=token_provider,
    )


def recording_handler(
    response_json: Any, *, status: int = 200, recorder: list[httpx.Request]
) -> Handler:
    """A handler that records each request and returns a canned JSON response."""

    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        return httpx.Response(status, json=response_json)

    return handler


def constant_handler(response_json: Any, *, status: int = 200) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=response_json)

    return handler


def tools_by_name(client: GarApiClient) -> dict[str, McpTool]:
    return {t.name: t for t in make_tools(client)}
