"""POST /runs/{id}/back — reverse-gate navigation (D-207), owner-scoped."""

import asyncio
from typing import Any

from gar_backend.governance.hitl import RunState, RunStatus

_CANDS = [{"source_name": "arxiv", "external_id": "1", "title": "P1"}]


def _save(api_setup: dict[str, Any], state: RunState) -> None:
    asyncio.run(api_setup["store"].save(state))


def test_back_from_sources_gate_reopens_concept(api_setup: dict[str, Any]) -> None:
    _save(
        api_setup,
        RunState(
            run_id="b1",
            tenant_id="default",
            owner_user_id="local-owner",
            status=RunStatus.AWAITING_SOURCE_SELECTION,
            context={"concept": "my concept", "directions": [{"id": 0}]},
            pending_payload={"candidates": _CANDS},
        ),
    )
    resp = api_setup["client"].post("/runs/b1/back")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "awaiting_concept_approval"
    assert data["pending_payload"]["concept"] == "my concept"


def test_back_from_report_gate_restores_candidates(api_setup: dict[str, Any]) -> None:
    _save(
        api_setup,
        RunState(
            run_id="b2",
            tenant_id="default",
            owner_user_id="local-owner",
            status=RunStatus.AWAITING_REPORT_APPROVAL,
            context={"concept": "c"},
            pending_payload={"report": "# R", "candidates": _CANDS},
        ),
    )
    resp = api_setup["client"].post("/runs/b2/back")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "awaiting_source_selection"
    assert data["pending_payload"]["candidates"] == _CANDS  # same papers
    assert "report" not in data["pending_payload"]


def test_back_from_wrong_status_is_409(api_setup: dict[str, Any]) -> None:
    _save(
        api_setup,
        RunState(
            run_id="b3",
            tenant_id="default",
            owner_user_id="local-owner",
            status=RunStatus.AWAITING_CONCEPT_APPROVAL,
            pending_payload={"concept": "c"},
        ),
    )
    assert api_setup["client"].post("/runs/b3/back").status_code == 409


def test_back_cross_owner_is_404(api_setup: dict[str, Any]) -> None:
    _save(
        api_setup,
        RunState(
            run_id="b4",
            tenant_id="default",
            owner_user_id="someone-else",
            status=RunStatus.AWAITING_SOURCE_SELECTION,
            context={"concept": "c"},
            pending_payload={"candidates": _CANDS},
        ),
    )
    assert api_setup["client"].post("/runs/b4/back").status_code == 404


def test_back_unknown_run_is_404(api_setup: dict[str, Any]) -> None:
    assert api_setup["client"].post("/runs/missing/back").status_code == 404
