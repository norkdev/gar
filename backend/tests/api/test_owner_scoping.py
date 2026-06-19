"""Per-user run scoping through the API (D-202): owner set on create, and
cross-owner access is 404'd / filtered out."""

from typing import Any

from gar_backend.api.deps import get_access_context
from gar_backend.governance.rbac import AccessContext
from gar_backend.main import app

from tests.api.conftest import text_response


def _as_user(user_id: str) -> None:
    app.dependency_overrides[get_access_context] = lambda: AccessContext(
        tenant_id="default", user_id=user_id, role="owner"
    )


def _create_as(api_setup: dict[str, Any], user_id: str) -> str:
    _as_user(user_id)
    api_setup["llm"].responses.append(text_response("a concept"))
    resp = api_setup["client"].post(
        "/runs", json={"vault_path": str(api_setup["vault"])}
    )
    assert resp.status_code == 200
    return resp.json()["run_id"]


def test_create_sets_owner_from_caller(api_setup: dict[str, Any]) -> None:
    _as_user("alice")
    api_setup["llm"].responses.append(text_response("c"))
    resp = api_setup["client"].post(
        "/runs", json={"vault_path": str(api_setup["vault"])}
    )
    assert resp.json()["owner_user_id"] == "alice"


def test_get_and_list_are_owner_scoped(api_setup: dict[str, Any]) -> None:
    client = api_setup["client"]
    run_id = _create_as(api_setup, "alice")

    # Bob — same tenant, different user — sees nothing of Alice's.
    _as_user("bob")
    assert client.get(f"/runs/{run_id}").status_code == 404
    assert client.get("/runs").json() == []

    # Alice still sees her own.
    _as_user("alice")
    assert client.get(f"/runs/{run_id}").status_code == 200
    assert [r["run_id"] for r in client.get("/runs").json()] == [run_id]


def test_gate_rejects_non_owner(api_setup: dict[str, Any]) -> None:
    client = api_setup["client"]
    run_id = _create_as(api_setup, "alice")

    _as_user("bob")
    # 404 (ownership) fires before the transition check — Bob can't even tell
    # what state the run is in.
    assert client.post(f"/runs/{run_id}/gates/concept", json={}).status_code == 404
