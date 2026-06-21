"""governance/hitl unit tests. Pure state-machine — no I/O, no async."""

import dataclasses

import pytest
from gar_backend.governance.hitl import (
    AWAITING_STATES,
    InvalidTransition,
    RunState,
    RunStatus,
    approve_concept,
    approve_report,
    fail,
    is_awaiting_user,
    is_terminal,
    report_of,
    request_concept_approval,
    request_report_approval,
    request_source_selection,
    revise_concept,
    revise_sources,
    select_sources,
    start,
)


def _drive_to(status: RunStatus) -> RunState:
    """Drive a fresh run through valid transitions to land at `status`."""
    state = start(run_id="r1", tenant_id="default")
    if status is RunStatus.DERIVING_CONCEPT:
        return state
    state = request_concept_approval(state, concept="c")
    if status is RunStatus.AWAITING_CONCEPT_APPROVAL:
        return state
    state = approve_concept(state)
    if status is RunStatus.SEARCHING:
        return state
    state = request_source_selection(state, candidates=[])
    if status is RunStatus.AWAITING_SOURCE_SELECTION:
        return state
    state = select_sources(state, adopted_source_ids=[])
    if status is RunStatus.EVALUATING:
        return state
    state = request_report_approval(state, report="x")
    if status is RunStatus.AWAITING_REPORT_APPROVAL:
        return state
    state = approve_report(state)
    if status is RunStatus.COMPLETED:
        return state
    raise ValueError(f"Cannot drive to {status}")


# ---------- initial state ----------


def test_start_initializes_at_deriving_concept() -> None:
    state = start(run_id="r1", tenant_id="default")
    assert state.run_id == "r1"
    assert state.tenant_id == "default"
    assert state.status is RunStatus.DERIVING_CONCEPT
    assert state.pending_payload == {}
    assert state.context == {}
    assert state.adopted_source_ids == ()
    assert state.error is None


# ---------- gate 1 ----------


def test_request_concept_approval_moves_to_awaiting_concept() -> None:
    state = start("r1", "default")
    new = request_concept_approval(state, concept="my concept")
    assert new.status is RunStatus.AWAITING_CONCEPT_APPROVAL
    assert new.pending_payload == {"concept": "my concept"}


def test_request_concept_approval_from_wrong_state_raises() -> None:
    state = _drive_to(RunStatus.AWAITING_CONCEPT_APPROVAL)
    with pytest.raises(InvalidTransition):
        request_concept_approval(state, concept="y")


def test_approve_concept_advances_and_records_concept_in_context() -> None:
    state = _drive_to(RunStatus.AWAITING_CONCEPT_APPROVAL)
    state = approve_concept(state)
    assert state.status is RunStatus.SEARCHING
    assert state.context == {"concept": "c"}
    assert state.pending_payload == {}


def test_approve_concept_with_edited_concept_overrides_pending() -> None:
    state = start("r1", "default")
    state = request_concept_approval(state, concept="raw")
    state = approve_concept(state, edited_concept="user-edited")
    assert state.context["concept"] == "user-edited"


def test_approve_concept_from_wrong_state_raises() -> None:
    state = start("r1", "default")
    with pytest.raises(InvalidTransition):
        approve_concept(state)


# ---------- gate 2 ----------


def test_request_source_selection_moves_to_awaiting_selection() -> None:
    state = _drive_to(RunStatus.SEARCHING)
    candidates = [{"external_id": "2301.1"}, {"external_id": "2301.2"}]
    state = request_source_selection(state, candidates=candidates)
    assert state.status is RunStatus.AWAITING_SOURCE_SELECTION
    assert state.pending_payload == {"candidates": candidates}


def test_request_source_selection_from_wrong_state_raises() -> None:
    state = start("r1", "default")
    with pytest.raises(InvalidTransition):
        request_source_selection(state, candidates=[])


def test_select_sources_records_adopted_and_moves_to_evaluating() -> None:
    state = _drive_to(RunStatus.AWAITING_SOURCE_SELECTION)
    state = select_sources(state, adopted_source_ids=["2301.1"])
    assert state.status is RunStatus.EVALUATING
    assert state.adopted_source_ids == ("2301.1",)
    # The full pool is retained (for revise_sources / back), not dropped (D-207).
    assert state.pending_payload == {"candidates": []}


def test_select_sources_carries_adopted_records_into_context() -> None:
    """Adopted candidate dicts are preserved so downstream phases (grounding
    validation, report composition) can refer to them."""
    state = start("r1", "default")
    state = request_concept_approval(state, concept="c")
    state = approve_concept(state)
    candidates = [
        {"source_name": "public_src", "external_id": "1.1", "title": "P1"},
        {"source_name": "public_src", "external_id": "2.2", "title": "P2"},
        {"source_name": "public_src", "external_id": "3.3", "title": "P3"},
    ]
    state = request_source_selection(state, candidates=candidates)
    state = select_sources(
        state, adopted_source_ids=["public_src:1.1", "public_src:3.3"]
    )

    adopted = state.context["adopted_evidence"]
    assert [c["external_id"] for c in adopted] == ["1.1", "3.3"]


def test_select_sources_with_no_matches_yields_empty_adopted_evidence() -> None:
    """If adopted_source_ids don't match any candidate, evidence is empty
    (and grounding validation will be skipped downstream)."""
    state = start("r1", "default")
    state = request_concept_approval(state, concept="c")
    state = approve_concept(state)
    state = request_source_selection(
        state, candidates=[{"source_name": "public_src", "external_id": "1.1"}]
    )
    state = select_sources(state, adopted_source_ids=["public_src:9.9"])
    assert state.context["adopted_evidence"] == []


def test_select_sources_from_wrong_state_raises() -> None:
    state = start("r1", "default")
    with pytest.raises(InvalidTransition):
        select_sources(state, adopted_source_ids=["x"])


# ---------- gate 3 ----------


def test_request_report_approval_moves_to_awaiting_report() -> None:
    state = _drive_to(RunStatus.EVALUATING)
    state = request_report_approval(state, report="# Report")
    assert state.status is RunStatus.AWAITING_REPORT_APPROVAL
    assert state.pending_payload == {"report": "# Report"}


def test_request_report_approval_carries_validation_summary() -> None:
    """A client retrieving the report (web UI / MCP get_report) can see
    whether the citations check out."""
    state = _drive_to(RunStatus.EVALUATING)
    summary = {"is_valid": True, "unknown_citations": [], "unused_evidence": []}
    state = request_report_approval(state, report="# Report", validation=summary)
    assert state.pending_payload["report_validation"] == summary


def test_request_report_approval_omits_validation_when_none() -> None:
    state = _drive_to(RunStatus.EVALUATING)
    state = request_report_approval(state, report="# Report", validation=None)
    assert "report_validation" not in state.pending_payload


def test_approve_report_completes_the_run() -> None:
    state = _drive_to(RunStatus.AWAITING_REPORT_APPROVAL)
    state = approve_report(state)
    assert state.status is RunStatus.COMPLETED


def test_approve_report_from_wrong_state_raises() -> None:
    state = start("r1", "default")
    with pytest.raises(InvalidTransition):
        approve_report(state)


# ---------- fail ----------


def test_fail_from_any_state_moves_to_failed_with_error() -> None:
    state = _drive_to(RunStatus.SEARCHING)
    state = fail(state, error="network unreachable")
    assert state.status is RunStatus.FAILED
    assert state.error == "network unreachable"


# ---------- helpers ----------


def test_is_awaiting_user_true_for_awaiting_states() -> None:
    for status in AWAITING_STATES:
        state = _drive_to(status)
        assert is_awaiting_user(state)


def test_is_awaiting_user_false_outside_awaiting_states() -> None:
    state = start("r1", "default")
    assert not is_awaiting_user(state)
    assert not is_awaiting_user(_drive_to(RunStatus.SEARCHING))


def test_is_terminal_true_for_completed_and_failed() -> None:
    assert is_terminal(_drive_to(RunStatus.COMPLETED))
    assert is_terminal(fail(start("r1", "default"), error="x"))


def test_is_terminal_false_for_running_states() -> None:
    assert not is_terminal(start("r1", "default"))
    assert not is_terminal(_drive_to(RunStatus.SEARCHING))


# ---------- invariants ----------


def test_run_state_is_frozen() -> None:
    state = start("r1", "default")
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.status = RunStatus.COMPLETED  # type: ignore[misc]


def test_updated_at_advances_or_equals_on_transition() -> None:
    state = start("r1", "default")
    original = state.updated_at
    state = request_concept_approval(state, concept="c")
    assert state.updated_at >= original


def test_tenant_id_is_carried_through_transitions() -> None:
    state = start("r1", "acme-corp")
    state = request_concept_approval(state, concept="c")
    state = approve_concept(state)
    state = request_source_selection(state, candidates=[])
    state = select_sources(state, adopted_source_ids=[])
    assert state.tenant_id == "acme-corp"


# ---------- report retention (D-204) ----------


def test_approve_report_retains_report_in_context() -> None:
    gate = _drive_to(RunStatus.AWAITING_REPORT_APPROVAL)
    assert gate.pending_payload["report"] == "x"  # at the gate, in pending
    completed = approve_report(gate)
    assert completed.status is RunStatus.COMPLETED
    assert completed.pending_payload == {}  # transient payload cleared
    assert completed.context["report"] == "x"  # but the deliverable survives


def test_report_of_reads_gate_then_completed() -> None:
    gate = _drive_to(RunStatus.AWAITING_REPORT_APPROVAL)
    assert report_of(gate) == ("x", None)
    assert report_of(approve_report(gate)) == ("x", None)


def test_report_of_none_before_a_report_exists() -> None:
    assert report_of(_drive_to(RunStatus.AWAITING_CONCEPT_APPROVAL)) == (None, None)


# ---------- back-navigation (D-207) ----------


def _sources_gate_with_pool() -> tuple[RunState, list]:
    state = start("r1", "default")
    state = request_concept_approval(state, concept="c")
    state = approve_concept(state)
    cands = [
        {"source_name": "arxiv", "external_id": "1", "title": "P1"},
        {"source_name": "arxiv", "external_id": "2", "title": "P2"},
    ]
    state = request_source_selection(state, candidates=cands)
    return state, cands


def test_pool_retained_through_report_gate_then_dropped() -> None:
    state, cands = _sources_gate_with_pool()
    state = select_sources(state, adopted_source_ids=["arxiv:1"])
    assert state.pending_payload["candidates"] == cands  # retained at EVALUATING
    state = request_report_approval(state, report="# R")
    assert state.pending_payload["candidates"] == cands  # retained at the report gate
    assert state.pending_payload["report"] == "# R"
    state = approve_report(state)
    assert "candidates" not in state.pending_payload  # dropped on completion
    assert state.context["report"] == "# R"  # report kept (light session)


def test_revise_concept_reopens_concept_and_clears_search() -> None:
    state, _ = _sources_gate_with_pool()
    state = dataclasses.replace(
        state, context={**state.context, "directions": [{"id": 0}]}
    )
    back = revise_concept(state)
    assert back.status is RunStatus.AWAITING_CONCEPT_APPROVAL
    assert back.pending_payload == {"concept": "c"}
    assert "directions" not in back.context  # search artifacts cleared
    assert "adopted_evidence" not in back.context
    assert back.adopted_source_ids == ()


def test_revise_sources_restores_pool_and_drops_report() -> None:
    state, cands = _sources_gate_with_pool()
    state = select_sources(state, adopted_source_ids=["arxiv:1"])
    state = request_report_approval(state, report="# R")
    back = revise_sources(state)
    assert back.status is RunStatus.AWAITING_SOURCE_SELECTION
    assert back.pending_payload == {"candidates": cands}  # same papers, report gone


def test_revise_from_wrong_state_raises() -> None:
    with pytest.raises(InvalidTransition):
        revise_concept(start("r1", "default"))
    with pytest.raises(InvalidTransition):
        revise_sources(start("r1", "default"))
