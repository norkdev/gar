"""Thin async HTTP client over the GAR backend REST API (plan D-102).

The MCP server is a *client* of the same API the web UI and CLI drive — it
does not import gar_backend's internals. This keeps scale seam #2 (UI never
calls AWS directly; the data plane is one hop from the backend) true for the
MCP surface too: after the AWS migration only the base URL and auth header
change, so the same MCP server runs unmodified against a remote backend.

Every request carries ``X-GAR-Client: mcp`` so the audit log attributes the
run to this surface (D-106).
"""

from __future__ import annotations

import os
import time
from typing import Any, Protocol

import httpx

DEFAULT_API_URL = "http://localhost:8000"
CONNECT_TIMEOUT_SEC = 10.0
# Agent phases run synchronously inside the POSTs in v1.1 (D-104), so a gate
# call can take as long as the LLM + retrieval round-trips. A recall-broad
# search can exceed even this; when it does, the read times out but the backend
# keeps working and the run advances (durable state) — the gate tools turn that
# timeout into a "processing" result the client recovers by polling.
REQUEST_TIMEOUT_SEC = 240.0


class GarApiError(Exception):
    """Backend unreachable or returned an error status.

    The message is written for the MCP client's LLM to read and decide a next
    step (retry, fix arguments, tell the human), not just for a log.
    """


class GarApiTimeout(GarApiError):
    """The backend did not respond within the read timeout.

    Distinct from GarApiError because it is *recoverable*: the synchronous phase
    is still running server-side and the run will advance (durable state). The
    gate tools catch this and report the run as still processing so the client
    polls get_run_status rather than treating it as a failure (D-104)."""


class TokenProvider(Protocol):
    """Supplies a bearer token for the Authorization header."""

    async def token(self) -> str: ...
    async def aclose(self) -> None: ...


class M2MTokenProvider:
    """OAuth2 client-credentials (M2M) token, cached until shortly before expiry
    (D-206). The MCP/CLI exchange client_id/secret at the Cognito token endpoint
    for a short-lived access token the backend verifies like any user token."""

    def __init__(
        self,
        *,
        token_endpoint: str,
        client_id: str,
        client_secret: str,
        scope: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint = token_endpoint
        self._auth = (client_id, client_secret)
        self._scope = scope
        self._http = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(REQUEST_TIMEOUT_SEC, connect=CONNECT_TIMEOUT_SEC),
        )
        self._cached: str | None = None
        self._expires_at = 0.0

    async def token(self) -> str:
        now = time.monotonic()
        if self._cached and now < self._expires_at - 30:  # 30s skew margin
            return self._cached
        data = {"grant_type": "client_credentials"}
        if self._scope:
            data["scope"] = self._scope
        try:
            resp = await self._http.post(self._endpoint, auth=self._auth, data=data)
        except httpx.HTTPError as exc:
            raise GarApiError(
                f"Could not reach the token endpoint {self._endpoint}: {exc}"
            ) from exc
        if resp.status_code >= 400:
            raise GarApiError(
                f"Token endpoint returned {resp.status_code}: {resp.text[:200]}"
            )
        body = resp.json()
        self._cached = body["access_token"]
        self._expires_at = now + float(body.get("expires_in", 3600))
        return self._cached

    async def aclose(self) -> None:
        await self._http.aclose()


def _token_provider_from_env() -> M2MTokenProvider | None:
    endpoint = os.environ.get("GAR_COGNITO_TOKEN_ENDPOINT")
    client_id = os.environ.get("GAR_COGNITO_CLIENT_ID")
    client_secret = os.environ.get("GAR_COGNITO_CLIENT_SECRET")
    if endpoint and client_id and client_secret:
        return M2MTokenProvider(
            token_endpoint=endpoint,
            client_id=client_id,
            client_secret=client_secret,
            scope=os.environ.get("GAR_COGNITO_SCOPE"),
        )
    return None


_USE_ENV = object()


class GarApiClient:
    """Async wrapper over the GAR REST API.

    Base URL resolves from ``base_url`` then ``GAR_API_URL`` (default
    ``http://localhost:8000``). Authentication is a Cognito bearer token from a
    ``TokenProvider``: by default the M2M provider built from the environment
    (``GAR_COGNITO_TOKEN_ENDPOINT`` / ``_CLIENT_ID`` / ``_CLIENT_SECRET`` /
    ``_SCOPE``), or None when unset (local backend with auth disabled).
    ``transport`` / ``token_provider`` are injectable so tests drive both with
    ``httpx.MockTransport`` / a stub — no live backend or Cognito required.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        token_provider: TokenProvider | None | object = _USE_ENV,
    ) -> None:
        resolved = base_url or os.environ.get("GAR_API_URL") or DEFAULT_API_URL
        self._base_url = resolved.rstrip("/")
        self._headers = {"X-GAR-Client": "mcp"}
        self._token_provider: TokenProvider | None = (
            _token_provider_from_env()
            if token_provider is _USE_ENV
            else token_provider  # type: ignore[assignment]
        )
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            transport=transport,
            timeout=httpx.Timeout(REQUEST_TIMEOUT_SEC, connect=CONNECT_TIMEOUT_SEC),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
        if self._token_provider is not None:
            await self._token_provider.aclose()

    async def _auth_headers(self) -> dict[str, str]:
        if self._token_provider is None:
            return {}
        return {"Authorization": f"Bearer {await self._token_provider.token()}"}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        headers = {**self._headers, **(await self._auth_headers())}
        try:
            resp = await self._client.request(
                method, path, json=json, params=params, headers=headers
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise GarApiError(
                f"Cannot reach the GAR backend at {self._base_url}. Is it running? "
                f"Set GAR_API_URL if it lives elsewhere. ({exc})"
            ) from exc
        except httpx.ReadTimeout as exc:
            # The backend is still processing — recoverable, not a failure.
            raise GarApiTimeout(
                f"The GAR backend did not respond within "
                f"{REQUEST_TIMEOUT_SEC:.0f}s. The run is likely still running; "
                "poll get_run_status."
            ) from exc
        except httpx.HTTPError as exc:
            raise GarApiError(
                f"Request to the GAR backend at {self._base_url} failed: {exc}"
            ) from exc
        if resp.status_code >= 400:
            raise GarApiError(_format_http_error(method, path, resp))
        return resp.json()

    async def create_run(self, notes: list[dict[str, str]]) -> dict[str, Any]:
        return await self._request("POST", "/runs", json={"notes_content": notes})

    async def list_runs(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/runs")

    async def get_run(self, run_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/runs/{run_id}")

    async def gate_concept(
        self, run_id: str, *, edited_concept: str | None
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/runs/{run_id}/gates/concept",
            json={"edited_concept": edited_concept},
        )

    async def gate_sources(
        self, run_id: str, *, adopted_source_ids: list[str]
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/runs/{run_id}/gates/sources",
            json={"adopted_source_ids": adopted_source_ids},
        )

    async def gate_report(self, run_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/runs/{run_id}/gates/report")


def _format_http_error(method: str, path: str, resp: httpx.Response) -> str:
    detail: str | None
    try:
        body = resp.json()
        detail = body.get("detail") if isinstance(body, dict) else None
    except ValueError:
        detail = resp.text[:200] or None

    base = f"GAR backend returned HTTP {resp.status_code} for {method} {path}"
    if resp.status_code == 404:
        return f"{base}: run not found. {detail or ''}".strip()
    if resp.status_code == 409:
        return (
            f"{base}: the run is not in the right state for this action — the gate "
            f"was already passed or called out of order. {detail or ''}"
        ).strip()
    if resp.status_code == 422:
        return f"{base}: the request arguments were rejected. {detail or ''}".strip()
    return f"{base}. {detail or ''}".strip()
