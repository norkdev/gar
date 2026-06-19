"""authorize_run — the two-axis access check (D-202)."""

import pytest
from fastapi import HTTPException
from gar_backend.api.access import authorize_run
from gar_backend.governance.hitl import RunState, RunStatus
from gar_backend.governance.rbac import AccessContext


def _state(*, tenant: str = "t1", owner: str = "u1") -> RunState:
    return RunState(
        run_id="r1",
        tenant_id=tenant,
        owner_user_id=owner,
        status=RunStatus.DERIVING_CONCEPT,
    )


def test_owner_and_tenant_match_returns_state() -> None:
    state = _state()
    assert authorize_run(state, AccessContext(tenant_id="t1", user_id="u1")) is state


def test_owner_mismatch_is_404() -> None:
    with pytest.raises(HTTPException) as exc:
        authorize_run(_state(owner="u1"), AccessContext(tenant_id="t1", user_id="u2"))
    assert exc.value.status_code == 404  # not 403 — don't reveal existence


def test_tenant_mismatch_is_404() -> None:
    with pytest.raises(HTTPException) as exc:
        authorize_run(_state(tenant="t1"), AccessContext(tenant_id="t2", user_id="u1"))
    assert exc.value.status_code == 404
