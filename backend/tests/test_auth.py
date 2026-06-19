"""Cognito JWT verification + router gating (no real Cognito).

Tokens are signed with a locally-generated RSA key; the verifier's
signing-key resolver is injected to return the matching public key, so the
JWKS endpoint is never contacted.
"""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from gar_backend.api import auth
from gar_backend.api.auth import AuthError, CognitoVerifier
from gar_backend.main import app

ISSUER = "https://cognito-idp.ap-northeast-1.amazonaws.com/pool-123"
CLIENT_ID = "m2m-client-abc"
SCOPE = "gar-api/access"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUB = _KEY.public_key()


def _token(**overrides: object) -> str:
    now = int(time.time())
    claims: dict = {
        "iss": ISSUER,
        "sub": "user-sub-1",
        "client_id": CLIENT_ID,
        "scope": SCOPE,
        "token_use": "access",
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return jwt.encode(claims, _KEY, algorithm="RS256")


def _verifier() -> CognitoVerifier:
    return CognitoVerifier(
        ISSUER,
        allowed_client_ids={CLIENT_ID},
        required_scope=SCOPE,
        signing_key_resolver=lambda _token: _PUB,
    )


# --------------------------- CognitoVerifier ---------------------------


def test_valid_token_maps_to_access_context() -> None:
    ctx = _verifier().verify(_token(**{"custom:tenant_id": "acme"}))
    assert ctx.user_id == "user-sub-1"
    assert ctx.tenant_id == "acme"
    assert ctx.role == "owner"


def test_tenant_defaults_when_claim_absent() -> None:
    assert _verifier().verify(_token()).tenant_id == "default"


def test_rejects_wrong_issuer() -> None:
    with pytest.raises(AuthError):
        _verifier().verify(_token(iss="https://evil.example"))


def test_rejects_expired_token() -> None:
    with pytest.raises(AuthError):
        _verifier().verify(_token(exp=int(time.time()) - 10))


def test_rejects_disallowed_client_id() -> None:
    with pytest.raises(AuthError):
        _verifier().verify(_token(client_id="some-other-client"))


def test_rejects_missing_required_scope() -> None:
    with pytest.raises(AuthError):
        _verifier().verify(_token(scope="gar-api/other"))


def test_rejects_bad_signature() -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    forged = jwt.encode(
        {"iss": ISSUER, "sub": "x", "client_id": CLIENT_ID, "scope": SCOPE,
         "exp": int(time.time()) + 60},
        other,
        algorithm="RS256",
    )
    with pytest.raises(AuthError):
        _verifier().verify(forged)


# --------------------- router gating (through the app) ------------------


@pytest.fixture(autouse=True)
def _reset_verifier_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "_verifier", auth._UNSET)


def test_routes_open_when_auth_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "_verifier", None)  # configured → no pool → disabled
    client = TestClient(app)
    assert client.get("/healthz").status_code == 200
    assert client.get("/runs").status_code == 200  # no token required


def test_routes_require_valid_token_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth, "_verifier", _verifier())
    client = TestClient(app)

    assert client.get("/healthz").status_code == 200  # never gated

    assert client.get("/runs").status_code == 401  # no bearer token
    bad = client.get("/runs", headers={"Authorization": "Bearer not-a-jwt"})
    assert bad.status_code == 401

    ok = client.get("/runs", headers={"Authorization": f"Bearer {_token()}"})
    assert ok.status_code == 200
    assert ok.json() == []
