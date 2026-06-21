"""Session endpoints (D-204): report download, delete, lean list — owner-scoped.

Runs are saved straight into the in-memory store so the tests don't drive a
full LLM survey; the endpoints under test are independent of how a run got
there."""

import asyncio
from typing import Any

from gar_backend.governance.hitl import RunState, RunStatus


def _save(api_setup: dict[str, Any], state: RunState) -> None:
    asyncio.run(api_setup["store"].save(state))


def _completed(
    run_id: str = "s1", owner: str = "local-owner", report: str = "# Survey"
) -> RunState:
    return RunState(
        run_id=run_id,
        tenant_id="default",
        owner_user_id=owner,
        status=RunStatus.COMPLETED,
        context={"concept": "c", "report": report},
    )


# --------------------------- report download ---------------------------


def test_get_report_after_completion(api_setup: dict[str, Any]) -> None:
    _save(api_setup, _completed(report="# Done\nbody"))
    resp = api_setup["client"].get("/runs/s1/report")
    assert resp.status_code == 200
    assert resp.json()["report"] == "# Done\nbody"


def test_get_report_at_the_gate(api_setup: dict[str, Any]) -> None:
    _save(
        api_setup,
        RunState(
            run_id="s2",
            tenant_id="default",
            owner_user_id="local-owner",
            status=RunStatus.AWAITING_REPORT_APPROVAL,
            pending_payload={"report": "# Draft"},
        ),
    )
    resp = api_setup["client"].get("/runs/s2/report")
    assert resp.status_code == 200
    assert resp.json()["report"] == "# Draft"


def test_get_report_404_when_none_yet(api_setup: dict[str, Any]) -> None:
    _save(
        api_setup,
        RunState(
            run_id="s3",
            tenant_id="default",
            owner_user_id="local-owner",
            status=RunStatus.DERIVING_CONCEPT,
        ),
    )
    assert api_setup["client"].get("/runs/s3/report").status_code == 404


def test_get_report_cross_owner_is_404(api_setup: dict[str, Any]) -> None:
    _save(api_setup, _completed(run_id="s4", owner="someone-else"))
    assert api_setup["client"].get("/runs/s4/report").status_code == 404


# --------------------------- delete ---------------------------


def test_delete_removes_the_run(api_setup: dict[str, Any]) -> None:
    client = api_setup["client"]
    _save(api_setup, _completed(run_id="s5"))
    assert client.delete("/runs/s5").status_code == 204
    assert client.get("/runs/s5").status_code == 404


def test_delete_cross_owner_is_404_and_keeps_the_run(api_setup: dict[str, Any]) -> None:
    client = api_setup["client"]
    _save(api_setup, _completed(run_id="s6", owner="other"))
    assert client.delete("/runs/s6").status_code == 404
    # untouched in the store (the real owner still has it)
    assert asyncio.run(api_setup["store"].get("s6")) is not None


def test_delete_unknown_is_404(api_setup: dict[str, Any]) -> None:
    assert api_setup["client"].delete("/runs/missing").status_code == 404


# --------------------------- lean list ---------------------------


def test_list_returns_lean_session_summaries(api_setup: dict[str, Any]) -> None:
    _save(api_setup, _completed(run_id="s7", report="# A long report body"))
    listed = api_setup["client"].get("/runs").json()
    assert len(listed) == 1
    item = listed[0]
    assert item["run_id"] == "s7"
    assert item["concept"] == "c"
    assert item["has_report"] is True
    # the heavy bits are NOT in the list projection
    assert "report" not in item
    assert "context" not in item
    assert "pending_payload" not in item
