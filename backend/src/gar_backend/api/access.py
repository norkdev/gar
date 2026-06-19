"""Two-axis access check on a run (D-202).

Every run/gate/stream access passes through here: a caller may touch a run only
if it is in their tenant (isolation — the hard wall) *and* they own it
(idea-privacy). A mismatch on either axis raises **404, not 403**, so the
endpoint never reveals that a run exists to someone outside its tenant or owner
(D-201). Sharing — relaxing the ownership axis via an explicit grant — is a
later seam; today ownership is strict.
"""

from __future__ import annotations

from fastapi import HTTPException

from gar_backend.governance.hitl import RunState
from gar_backend.governance.rbac import AccessContext


def authorize_run(state: RunState, access: AccessContext) -> RunState:
    """Return ``state`` if ``access`` may use it; otherwise raise 404."""
    if state.tenant_id != access.tenant_id or state.owner_user_id != access.user_id:
        raise HTTPException(status_code=404, detail="Run not found")
    return state
