"""API-key gate: require_api_key behavior + router enforcement."""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from gar_backend.api import auth
from gar_backend.api.auth import API_KEY_HEADER
from gar_backend.main import app

# --------------------------- unit: the dependency ---------------------------


async def test_disabled_when_no_key_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_configured_api_key", lambda: None)
    await auth.require_api_key(x_gar_api_key=None)  # no raise → open


async def test_missing_key_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_configured_api_key", lambda: "s3cret")
    with pytest.raises(HTTPException) as exc:
        await auth.require_api_key(x_gar_api_key=None)
    assert exc.value.status_code == 401


async def test_wrong_key_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_configured_api_key", lambda: "s3cret")
    with pytest.raises(HTTPException):
        await auth.require_api_key(x_gar_api_key="nope")


async def test_correct_key_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "get_configured_api_key", lambda: "s3cret")
    await auth.require_api_key(x_gar_api_key="s3cret")  # no raise


def test_get_configured_key_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth, "_configured_key", auth._UNSET)
    monkeypatch.setenv("GAR_API_KEY", "abc")
    assert auth.get_configured_api_key() == "abc"


# --------------------- integration: through the app -------------------------


def test_routes_gated_but_healthz_open(monkeypatch: pytest.MonkeyPatch) -> None:
    # require_api_key reads this module global directly (not via Depends).
    monkeypatch.setattr(auth, "get_configured_api_key", lambda: "topsecret")
    client = TestClient(app)

    assert client.get("/healthz").status_code == 200  # never gated

    assert client.get("/runs").status_code == 401  # gated route, no key
    assert client.get("/runs", headers={API_KEY_HEADER: "wrong"}).status_code == 401
    # Correct key passes the gate; the list endpoint then returns its (empty) list.
    ok = client.get("/runs", headers={API_KEY_HEADER: "topsecret"})
    assert ok.status_code == 200
    assert ok.json() == []
