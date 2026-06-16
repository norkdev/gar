"""Pydantic I/O models for the MCP tools.

These mirror the shapes the HTTP API already returns (api/runs.py
serialize_state, the gate payloads) so the MCP surface stays in lockstep
with the backend rather than drifting into a parallel schema.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class NoteInput(BaseModel):
    """One idea note uploaded by the client (D-105: content-upload path).

    ``path`` is a display label (e.g. the note's filename), not a path the
    backend reads from disk — the backend never touches the caller's
    filesystem, so the same call works against a local or a remote backend.
    """

    path: str = Field(description="Display label for the note, e.g. its filename.")
    content: str = Field(description="Full Markdown text of the idea note.")


class StartSurveyResult(BaseModel):
    run_id: str
    status: str


class RunSummary(BaseModel):
    run_id: str
    status: str
    updated_at: str


class Candidate(BaseModel):
    """One candidate source at the sources gate, for the client to organize and
    present so the human can choose what to adopt."""

    id: str = Field(
        description="Adopt this source by passing this id to select_sources "
        "(form: source_name:external_id)."
    )
    title: str
    abstract: str | None = Field(
        default=None,
        description="The source's abstract. Present when include_abstracts is on; "
        "the basis for judging relevance.",
    )
    authors: list[str] = Field(default_factory=list)
    published: str | None = None
    url: str | None = None
    support: int = Field(
        default=0,
        description="How many distinct search-query angles surfaced this source. "
        "High support = cross-cutting / foundational; support 1 = a frontier "
        "specific to one angle. Use it to group core vs. extension directions.",
    )
    matched_queries: list[str] = Field(
        default_factory=list,
        description="The query angles that surfaced this source (its provenance).",
    )


class RunStatusResult(BaseModel):
    run_id: str
    status: str
    current_gate: str | None = Field(
        default=None,
        description="Which HITL gate is open (concept | sources | report), or "
        "null if the run is not waiting on a human.",
    )
    activity_summary: str = Field(
        description="Human-readable summary of where the run is and what the "
        "human needs to decide next."
    )
    candidates: list[Candidate] = Field(
        default_factory=list,
        description="Candidate sources to choose from at the sources gate (empty "
        "otherwise). Organize and present these so the human picks what to adopt.",
    )
    candidate_count: int = Field(
        default=0,
        description="Total candidates found. If it exceeds len(candidates), the "
        "list was truncated to max_candidates.",
    )


class GateResult(BaseModel):
    run_id: str
    status: str


class ReportResult(BaseModel):
    run_id: str
    status: str
    markdown: str | None = Field(
        default=None, description="The composed report, or null if none is ready yet."
    )
    citations_valid: bool | None = Field(
        default=None,
        description="Whether every citation resolves to an adopted source. Null "
        "when there was no adopted evidence to validate against.",
    )
    warnings: list[str] = Field(default_factory=list)
