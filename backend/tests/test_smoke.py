"""Smoke test: FastAPI app boots and /healthz returns 200."""

from fastapi.testclient import TestClient

from gar_backend.main import app


def test_healthz_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
