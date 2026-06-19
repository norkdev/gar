"""Identity at the HTTP boundary — Cognito JWT verification (spec seam #7,
D-203 / D-206).

One auth path for everyone: the backend verifies a Cognito JWT (issuer / JWKS /
signature / expiry) and builds an ``AccessContext`` from its claims. Human users
(browser, later slice) send an ID/access token; machine clients (MCP / CLI) use
the OAuth2 client-credentials (M2M) grant and send the resulting access token.
Both arrive as ``Authorization: Bearer <jwt>``. This replaces the v2.0
``X-GAR-API-Key`` gate — there is no second mechanism.

Disabled when no pool is configured (``GAR_COGNITO_ISSUER`` unset) → local dev
and the test suite run open with a default-owner context, exactly as the key
gate did.
"""

from __future__ import annotations

import os
from typing import Any, Protocol

import jwt
from fastapi import Request

from gar_backend.governance.rbac import AccessContext

# Issuer URL: https://cognito-idp.{region}.amazonaws.com/{userPoolId}. Its
# presence flips auth on.
ISSUER_ENV = "GAR_COGNITO_ISSUER"
# Comma-separated app-client ids allowed to call the API (the M2M client, and
# later the browser client).
CLIENT_IDS_ENV = "GAR_COGNITO_CLIENT_IDS"
# Required OAuth scope on access tokens, e.g. "gar-api/access" (optional).
SCOPE_ENV = "GAR_COGNITO_SCOPE"

# Cognito puts the tenant on a custom attribute when present; absent → default.
TENANT_CLAIM = "custom:tenant_id"


class AuthError(Exception):
    """Token rejected (bad signature / issuer / expiry / client / scope)."""


class SigningKeyResolver(Protocol):
    def __call__(self, token: str) -> Any: ...


class CognitoVerifier:
    """Verifies a Cognito JWT and maps its claims to an AccessContext.

    ``signing_key_resolver`` is injectable so tests sign with a local key
    instead of reaching Cognito's JWKS endpoint; in production it defaults to
    PyJWKClient (fetches + caches the pool's JWKS).
    """

    def __init__(
        self,
        issuer: str,
        *,
        allowed_client_ids: set[str],
        required_scope: str | None = None,
        signing_key_resolver: SigningKeyResolver | None = None,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._allowed = allowed_client_ids
        self._required_scope = required_scope
        self._resolve_key = signing_key_resolver or self._jwks_resolver()

    def _jwks_resolver(self) -> SigningKeyResolver:
        client = jwt.PyJWKClient(f"{self._issuer}/.well-known/jwks.json")
        return lambda token: client.get_signing_key_from_jwt(token).key

    def verify(self, token: str) -> AccessContext:
        try:
            key = self._resolve_key(token)
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self._issuer,
                # Access tokens carry no `aud`; we check `client_id` ourselves.
                options={"verify_aud": False},
            )
        except (jwt.PyJWTError, AuthError) as exc:
            raise AuthError(str(exc)) from exc

        # An access token's client_id is the app client; an id token uses aud.
        client_id = claims.get("client_id") or claims.get("aud")
        if self._allowed and client_id not in self._allowed:
            raise AuthError("client_id not allowed")
        if self._required_scope:
            scopes = str(claims.get("scope") or "").split()
            if self._required_scope not in scopes:
                raise AuthError("missing required scope")

        sub = claims.get("sub")
        if not sub:
            raise AuthError("token has no sub")
        tenant = claims.get(TENANT_CLAIM) or "default"
        return AccessContext(tenant_id=tenant, user_id=sub, role="owner")


_UNSET = object()
_verifier: object | CognitoVerifier | None = _UNSET


def get_verifier() -> CognitoVerifier | None:
    """The verifier, built once from env. None means auth is disabled."""
    global _verifier
    if _verifier is _UNSET:
        issuer = os.environ.get(ISSUER_ENV)
        if not issuer:
            _verifier = None
        else:
            client_ids = {
                c.strip()
                for c in os.environ.get(CLIENT_IDS_ENV, "").split(",")
                if c.strip()
            }
            _verifier = CognitoVerifier(
                issuer,
                allowed_client_ids=client_ids,
                required_scope=os.environ.get(SCOPE_ENV) or None,
            )
    return _verifier  # type: ignore[return-value]


def bearer_token(request: Request) -> str | None:
    """Extract the token from an ``Authorization: Bearer <jwt>`` header."""
    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return None
