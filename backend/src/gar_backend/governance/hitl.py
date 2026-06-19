"""HITL gate state machine. Three gates: concept review / source selection / final report.

Gates are **durable state**, not in-process awaits — see spec §9 and §10
seam #4. The agent stops at an AWAITING_* state and the runtime persists
the RunState. When the UI returns user input, a transition function moves
the state forward; the agent then resumes from the new state.

These functions are pure: they take a RunState and return a new RunState.
Storage (DynamoDB in production, in-memory in v1 tests) lives in
`state/runs.py` / `state/checkpoints.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class RunStatus(StrEnum):
    DERIVING_CONCEPT = "deriving_concept"
    AWAITING_CONCEPT_APPROVAL = "awaiting_concept_approval"
    SEARCHING = "searching"
    AWAITING_SOURCE_SELECTION = "awaiting_source_selection"
    EVALUATING = "evaluating"
    AWAITING_REPORT_APPROVAL = "awaiting_report_approval"
    COMPLETED = "completed"
    FAILED = "failed"


AWAITING_STATES: frozenset[RunStatus] = frozenset(
    {
        RunStatus.AWAITING_CONCEPT_APPROVAL,
        RunStatus.AWAITING_SOURCE_SELECTION,
        RunStatus.AWAITING_REPORT_APPROVAL,
    }
)

TERMINAL_STATES: frozenset[RunStatus] = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
    }
)


@dataclass(frozen=True)
class RunState:
    """Durable snapshot of one agent run.

    `context` carries forward across transitions (e.g., the agreed concept
    text after gate 1). `pending_payload` holds the data the *current*
    awaiting state is asking the user to confirm or edit; it is cleared at
    each forward transition.
    """

    run_id: str
    tenant_id: str
    status: RunStatus
    # Two boundaries (D-202): tenant_id = isolation; owner_user_id = idea-privacy
    # (whose run this is). Defaults so non-API constructors (tests) stay terse;
    # the API sets it from the verified caller. Carried across transitions.
    owner_user_id: str = "local-owner"
    context: dict[str, Any] = field(default_factory=dict)
    pending_payload: dict[str, Any] = field(default_factory=dict)
    adopted_source_ids: tuple[str, ...] = ()
    error: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class InvalidTransition(RuntimeError):
    """Raised when a transition is requested from a state that doesn't allow it."""


def start(run_id: str, tenant_id: str, owner_user_id: str = "local-owner") -> RunState:
    """Initial state — the agent will begin deriving a concept."""
    return RunState(
        run_id=run_id,
        tenant_id=tenant_id,
        status=RunStatus.DERIVING_CONCEPT,
        owner_user_id=owner_user_id,
    )


def request_concept_approval(state: RunState, *, concept: str) -> RunState:
    """Agent finished deriving; ask the user to approve / edit the concept."""
    _require(state, RunStatus.DERIVING_CONCEPT)
    return _advance(
        state,
        status=RunStatus.AWAITING_CONCEPT_APPROVAL,
        pending_payload={"concept": concept},
    )


def approve_concept(state: RunState, *, edited_concept: str | None = None) -> RunState:
    """Gate 1 approval. Pass `edited_concept` if the user edited the text."""
    _require(state, RunStatus.AWAITING_CONCEPT_APPROVAL)
    final = (
        edited_concept
        if edited_concept is not None
        else state.pending_payload.get("concept")
    )
    return _advance(
        state,
        status=RunStatus.SEARCHING,
        context={**state.context, "concept": final},
        pending_payload={},
    )


def request_source_selection(
    state: RunState, *, candidates: list[dict[str, Any]]
) -> RunState:
    """Agent finished searching; ask the user which sources to adopt."""
    _require(state, RunStatus.SEARCHING)
    return _advance(
        state,
        status=RunStatus.AWAITING_SOURCE_SELECTION,
        pending_payload={"candidates": candidates},
    )


def select_sources(state: RunState, *, adopted_source_ids: list[str]) -> RunState:
    """Gate 2 approval. Records which sources the user adopted.

    Adopted IDs are expected in the composite ``source_name:external_id``
    form so they can be matched against the candidate records held in
    ``pending_payload``. Matched candidate dicts are carried forward into
    ``context['adopted_evidence']`` so downstream phases (e.g., grounding
    validation) can reference them.
    """
    _require(state, RunStatus.AWAITING_SOURCE_SELECTION)
    candidates = state.pending_payload.get("candidates", [])
    adopted_set = set(adopted_source_ids)
    adopted_evidence = [
        c
        for c in candidates
        if f"{c.get('source_name', '')}:{c.get('external_id', '')}" in adopted_set
    ]
    return _advance(
        state,
        status=RunStatus.EVALUATING,
        context={**state.context, "adopted_evidence": adopted_evidence},
        adopted_source_ids=tuple(adopted_source_ids),
        pending_payload={},
    )


def request_report_approval(
    state: RunState, *, report: str, validation: dict[str, Any] | None = None
) -> RunState:
    """Agent finished evaluating; ask the user to approve / save the report.

    ``validation`` is the grounding summary for this report (citation
    validity, any unknown / unused citations). It is carried in the gate
    payload so a client retrieving the report — the web UI or the MCP
    ``get_report`` tool — can show whether the citations check out. Omitted
    when there was no adopted evidence to validate against.
    """
    _require(state, RunStatus.EVALUATING)
    payload: dict[str, Any] = {"report": report}
    if validation is not None:
        payload["report_validation"] = validation
    return _advance(
        state,
        status=RunStatus.AWAITING_REPORT_APPROVAL,
        pending_payload=payload,
    )


def approve_report(state: RunState) -> RunState:
    """Gate 3 approval. The run is now COMPLETED."""
    _require(state, RunStatus.AWAITING_REPORT_APPROVAL)
    return _advance(
        state,
        status=RunStatus.COMPLETED,
        pending_payload={},
    )


def fail(state: RunState, *, error: str) -> RunState:
    """Move to FAILED from any state."""
    return _advance(state, status=RunStatus.FAILED, error=error)


def is_awaiting_user(state: RunState) -> bool:
    return state.status in AWAITING_STATES


def is_terminal(state: RunState) -> bool:
    return state.status in TERMINAL_STATES


def _require(state: RunState, expected: RunStatus) -> None:
    if state.status is not expected:
        raise InvalidTransition(f"Expected status {expected}, got {state.status}")


def _advance(state: RunState, **changes: Any) -> RunState:
    return replace(state, updated_at=datetime.now(UTC), **changes)
