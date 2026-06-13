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
