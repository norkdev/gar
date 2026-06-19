"""API-key gate at the HTTP boundary (spec §10 seam #7).

v1 left this as a pass-through. v2.0 enforces a shared API key so the Function
URL can drop IAM signing (auth_type NONE) and still not be open to the world —
the MCP server and browser send the key in a header instead of SigV4-signing.

When no key is configured (``GAR_API_KEY`` / ``GAR_API_KEY_SECRET_ARN`` both
unset) the gate is **disabled** — local dev and the test suite run open, as
before. On Lambda the key is set, so it is enforced. ``/healthz`` is never
gated (it is defined on the app, not the guarded routers).

Per-user identity (Cognito) is a later phase; this is a single shared app key,
not a user credential — which is why it travels in ``X-GAR-API-Key`` rather
than ``Authorization`` (kept free for those tokens).
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from gar_backend.secrets import resolve_api_key

# Clients send the key here. Distinct from X-GAR-Client (which is informational,
# for the audit log) — this one is checked.
API_KEY_HEADER = "X-GAR-API-Key"

_UNSET = object()
_configured_key: object | str | None = _UNSET


def get_configured_api_key() -> str | None:
    """The expected API key, resolved once. None means the gate is disabled."""
    global _configured_key
    if _configured_key is _UNSET:
        _configured_key = resolve_api_key()
    return _configured_key  # type: ignore[return-value]


async def require_api_key(
    x_gar_api_key: str | None = Header(default=None),
) -> None:
    """Router dependency: 401 unless the request carries the configured key.

    No-ops when no key is configured (disabled). Uses a constant-time compare
    so a wrong key can't be guessed by timing.
    """
    expected = get_configured_api_key()
    if expected is None:
        return
    if not x_gar_api_key or not hmac.compare_digest(x_gar_api_key, expected):
        raise HTTPException(status_code=401, detail="invalid or missing API key")
