"""MCP tool definitions and the role-gated registry (D-101, D-103).

The MCP surface exposes run management and the three HITL gates — not GAR's
low-level retrieval tools. That is the point of D-101: if the MCP client could
call ``search_arxiv``/``search_ideas`` directly, its own LLM could run the
retrieval loop itself and bypass grounding validation, the gates, and the
audit log. Exposing the gates instead means the governance layer holds across
the protocol boundary: the MCP client gets a *governed sub-agent*, not a way
around the loop.

Each tool carries a ``min_role``. The registry omits tools above the caller's
role from the schema entirely — structural absence, mirroring
governance/rbac.py, not a refuse-at-call-time check. v1.1 ships no private
(ideas) tools, so every production tool is ``public``; the mechanism is in
place for when ideas search is added to the MCP surface for the ``owner`` role.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from gar_backend.mcp_server.client import GarApiClient, GarApiError
from gar_backend.mcp_server.models import (
    Candidate,
    GateResult,
    NoteInput,
    ReportResult,
    RunStatusResult,
    RunSummary,
    StartSurveyResult,
)

ROLE_RANK: dict[str, int] = {"public": 0, "owner": 1}

# Default cap on candidates returned by get_run_status. The sources gate is the
# key human decision, so the default is generous — the client LLM can organize a
# long list. Overridable per call (max_candidates arg) and per deployment
# (GAR_MCP_MAX_CANDIDATES). Abstracts are included by default; a token-conscious
# caller opts out with include_abstracts=False.
DEFAULT_MAX_CANDIDATES = 100


def _env_max_candidates() -> int:
    raw = os.environ.get("GAR_MCP_MAX_CANDIDATES")
    if raw is None:
        return DEFAULT_MAX_CANDIDATES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_CANDIDATES
    return value if value > 0 else DEFAULT_MAX_CANDIDATES


# Appended to every gate tool's description. The last mile of governance lives
# in the MCP client's behavior, so the description is where we carry the rule.
_GATE_NOTE = (
    " GOVERNANCE: this passes a human-in-the-loop gate. Call it only after a "
    "human has reviewed the material and explicitly decided — never auto-approve."
)

_GATE_FOR_STATUS: dict[str, str] = {
    "awaiting_concept_approval": "concept",
    "awaiting_source_selection": "sources",
    "awaiting_report_approval": "report",
}


@dataclass(frozen=True)
class McpTool:
    """A registrable MCP tool plus the minimum role that may see it."""

    name: str
    description: str
    fn: Callable[..., Awaitable[Any]]
    min_role: str = "public"


def tools_for_role(tools: list[McpTool], role: str) -> list[McpTool]:
    """Filter to the tools visible to ``role`` (D-103 structural absence).

    Unknown roles fall back to ``public``; a tool with an unknown ``min_role``
    is treated as owner-only (fail closed)."""
    ceiling = ROLE_RANK.get(role, ROLE_RANK["public"])
    return [
        t for t in tools if ROLE_RANK.get(t.min_role, ROLE_RANK["owner"]) <= ceiling
    ]


def _to_candidate(c: dict[str, Any], *, include_abstracts: bool) -> Candidate:
    """Map a stored candidate dict (serialized SearchResult) to a Candidate."""
    return Candidate(
        id=f"{c.get('source_name', '')}:{c.get('external_id', '')}",
        title=c.get("title", ""),
        abstract=(c.get("snippet") or None) if include_abstracts else None,
        authors=list(c.get("authors") or []),
        published=c.get("published"),
        url=c.get("url") or None,
    )


def _summarize_activity(data: dict[str, Any], *, total: int, shown: int) -> str:
    status = data.get("status", "")
    payload = data.get("pending_payload", {})
    if status == "awaiting_concept_approval":
        concept = payload.get("concept", "")
        return (
            "Concept derived; awaiting human review at the concept gate. "
            f"Derived concept: {concept}"
        )
    if status == "awaiting_source_selection":
        trunc = "" if shown >= total else f"; showing {shown} (raise max_candidates)"
        return (
            f"Search complete; {total} candidate(s) found{trunc}. Awaiting human "
            "selection at the sources gate. The candidates are in the `candidates` "
            "field; adopt by id via select_sources."
        )
    if status == "awaiting_report_approval":
        return (
            "Report composed; awaiting human approval at the report gate. Call "
            "get_report to retrieve it for the human to review before approving."
        )
    if status == "completed":
        return "Run complete and approved."
    if status == "failed":
        return f"Run failed: {data.get('error') or 'unknown error'}."
    return f"Run in progress (status={status})."


def make_tools(client: GarApiClient) -> list[McpTool]:
    """Build the production tool set, each bound to ``client``."""

    async def start_survey(notes: list[NoteInput]) -> StartSurveyResult:
        """Start a literature survey from your idea notes. Pass the notes'
        contents directly (the backend never reads your filesystem). Returns a
        run_id; the run then waits at the concept gate — poll get_run_status."""
        if not notes:
            raise GarApiError("start_survey requires at least one note.")
        data = await client.create_run([n.model_dump() for n in notes])
        return StartSurveyResult(run_id=data["run_id"], status=data["status"])

    async def list_runs() -> list[RunSummary]:
        """List this tenant's runs with their current status."""
        rows = await client.list_runs()
        return [
            RunSummary(
                run_id=r["run_id"], status=r["status"], updated_at=r["updated_at"]
            )
            for r in rows
        ]

    env_max = _env_max_candidates()

    async def get_run_status(
        run_id: str,
        max_candidates: int = env_max,
        include_abstracts: bool = True,
    ) -> RunStatusResult:
        """Check a run's status and what the human needs to decide next.
        current_gate names the open gate (concept | sources | report). At the
        sources gate the `candidates` field holds the candidate sources (with
        abstracts by default) for you to organize and present; candidate_count
        is the total found. Lower max_candidates or set include_abstracts=False
        to reduce tokens."""
        data = await client.get_run(run_id)
        status = data["status"]
        raw = (
            data.get("pending_payload", {}).get("candidates", [])
            if status == "awaiting_source_selection"
            else []
        )
        limit = max_candidates if max_candidates > 0 else env_max
        shown = raw[:limit]
        candidates = [
            _to_candidate(c, include_abstracts=include_abstracts) for c in shown
        ]
        return RunStatusResult(
            run_id=run_id,
            status=status,
            current_gate=_GATE_FOR_STATUS.get(status),
            activity_summary=_summarize_activity(
                data, total=len(raw), shown=len(shown)
            ),
            candidates=candidates,
            candidate_count=len(raw),
        )

    async def review_concept(
        run_id: str,
        action: Literal["approve", "edit"],
        edited_concept: str | None = None,
    ) -> GateResult:
        """Gate 1: approve the derived concept as-is, or replace it with an
        edited version (action='edit' requires edited_concept). The run then
        searches and advances to the sources gate."""
        if action == "edit":
            if not edited_concept or not edited_concept.strip():
                raise GarApiError("action='edit' requires a non-empty edited_concept.")
            edited = edited_concept
        else:
            edited = None
        data = await client.gate_concept(run_id, edited_concept=edited)
        return GateResult(run_id=run_id, status=data["status"])

    async def select_sources(run_id: str, adopted_ids: list[str]) -> GateResult:
        """Gate 2: choose which candidate sources to adopt, by id
        (source_name:external_id from get_run_status). An empty list adopts
        none. The run then composes the report and advances to the report
        gate."""
        data = await client.gate_sources(run_id, adopted_source_ids=adopted_ids)
        return GateResult(run_id=run_id, status=data["status"])

    async def approve_report(
        run_id: str,
        action: Literal["approve", "reject"],
        feedback: str | None = None,
    ) -> GateResult:
        """Gate 3: approve the final report, completing the run. Retrieve and
        review it with get_report first."""
        if action == "reject":
            raise GarApiError(
                "Report rejection is not supported in v1.1: the report gate only "
                "supports approval. To discard, abandon the run; to proceed, call "
                "approve_report with action='approve'."
            )
        data = await client.gate_report(run_id)
        return GateResult(run_id=run_id, status=data["status"])

    async def get_report(run_id: str) -> ReportResult:
        """Fetch the composed report (Markdown) plus its citation-validity
        summary. Persisting the report is the client's responsibility (D-105).
        Available once the run reaches the report gate."""
        data = await client.get_run(run_id)
        status = data["status"]
        payload = data.get("pending_payload", {})
        markdown = payload.get("report")
        if markdown is None:
            raise GarApiError(
                f"No report is available for run {run_id} (status={status}). A "
                "report exists only at the report gate "
                "(status=awaiting_report_approval); drive the run there first."
            )
        summary = payload.get("report_validation")
        citations_valid: bool | None = None
        warnings: list[str] = []
        if summary is not None:
            citations_valid = summary.get("is_valid")
            unknown = summary.get("unknown_citations", [])
            unused = summary.get("unused_evidence", [])
            if unknown:
                warnings.append(
                    f"{len(unknown)} citation(s) not found in adopted sources: "
                    f"{', '.join(unknown[:10])}"
                )
            if unused:
                warnings.append(
                    f"{len(unused)} adopted source(s) not cited in the report."
                )
        return ReportResult(
            run_id=run_id,
            status=status,
            markdown=markdown,
            citations_valid=citations_valid,
            warnings=warnings,
        )

    return [
        McpTool("start_survey", _doc(start_survey), start_survey),
        McpTool("list_runs", _doc(list_runs), list_runs),
        McpTool("get_run_status", _doc(get_run_status), get_run_status),
        McpTool("review_concept", _doc(review_concept) + _GATE_NOTE, review_concept),
        McpTool("select_sources", _doc(select_sources) + _GATE_NOTE, select_sources),
        McpTool("approve_report", _doc(approve_report) + _GATE_NOTE, approve_report),
        McpTool("get_report", _doc(get_report), get_report),
    ]


def _doc(fn: Callable[..., Any]) -> str:
    """Collapse a function's docstring into a one-paragraph tool description."""
    return " ".join((fn.__doc__ or "").split())
