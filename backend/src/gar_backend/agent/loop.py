"""Agent loop driver — retrieve → reason → judge → repeat, with HITL gates.

Three phase functions, one per workflow segment between gates:
- :func:`phase_derive_concept`  — DERIVING_CONCEPT → AWAITING_CONCEPT_APPROVAL
- :func:`phase_search`          — SEARCHING        → AWAITING_SOURCE_SELECTION
- :func:`phase_compose_report`  — EVALUATING       → AWAITING_REPORT_APPROVAL

:func:`run_until_gate` is the orchestrator: it loads state from the store,
runs phase functions in sequence, and persists state after each. When the
state becomes AWAITING_* or terminal, it returns. The API resumes the loop
by transitioning the state (via hitl.py) and calling ``run_until_gate``
again — this is the durable-state / wait-for-callback pattern (spec §10
seam #4).

Every LLM call goes through :func:`_audited_complete` so the audit log
(governance pillar #3) captures it. Tool dispatch is already audited
inside :func:`gar_backend.agent.tools.dispatch`.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, cast

from gar_backend.agent.llm import (
    LLMClient,
    LLMResponse,
    Message,
    RateLimitError,
    ToolDefinition,
)
from gar_backend.agent.prompts import (
    COMPOSE_REPORT_SYSTEM,
    DERIVE_CONCEPT_SYSTEM,
    SEARCH_SYSTEM,
)
from gar_backend.agent.tools import AgentTool, dispatch
from gar_backend.governance.audit import AuditLogger, AuditRecord
from gar_backend.governance.grounding import GroundingReport
from gar_backend.governance.grounding import validate as validate_grounding
from gar_backend.governance.hitl import (
    InvalidTransition,
    RunState,
    RunStatus,
    fail,
    is_awaiting_user,
    is_terminal,
    request_concept_approval,
    request_report_approval,
    request_source_selection,
    start,
)
from gar_backend.governance.rbac import AccessContext, ToolRegistry
from gar_backend.ideas.reader import IdeaDocument, UnsupportedFileType, read
from gar_backend.ideas.walker import walk
from gar_backend.reports.linkify import linkify_report
from gar_backend.retrieval.rerank import Reranker, _candidate_key, make_reranker
from gar_backend.sources.base import SearchResult
from gar_backend.state.runs import RunStore

# Per-phase model tiers (cost control). Haiku for the cheap, lower-stakes /
# token-heavy phases; Sonnet for the deliverable the human reads.
# - derive: low stakes — the human reviews/edits the concept at gate 1.
# - search: the token-heaviest phase (abstracts accumulate in context), so the
#   biggest savings; recall is partly protected by the breadth prompt, the
#   embedding rerank, and the human's gate-2 selection.
# - compose: the report the human reads — citation discipline, hedged synthesis,
#   the positioning map. Weaker models mangle citations and trigger grounding
#   retries, so spend here.
DEFAULT_DERIVE_MODEL = "claude-haiku-4-5"
DEFAULT_SEARCH_MODEL = "claude-haiku-4-5"
DEFAULT_COMPOSE_MODEL = "claude-sonnet-4-6"


@dataclass(frozen=True)
class ModelPolicy:
    """Which model each phase uses."""

    derive: str = DEFAULT_DERIVE_MODEL
    search: str = DEFAULT_SEARCH_MODEL
    compose: str = DEFAULT_COMPOSE_MODEL


def make_model_policy() -> ModelPolicy:
    """Resolve per-phase models from the environment.

    Defaults: Haiku for derive + search, Sonnet for compose. Override any phase
    with GAR_MODEL_DERIVE / GAR_MODEL_SEARCH / GAR_MODEL_COMPOSE. GAR_THOROUGH
    escalates the recall-sensitive search phase to the compose-tier model for a
    high-stakes survey (an explicit GAR_MODEL_SEARCH still wins)."""
    compose = os.environ.get("GAR_MODEL_COMPOSE", DEFAULT_COMPOSE_MODEL)
    thorough = os.environ.get("GAR_THOROUGH", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    search_default = compose if thorough else DEFAULT_SEARCH_MODEL
    return ModelPolicy(
        derive=os.environ.get("GAR_MODEL_DERIVE", DEFAULT_DERIVE_MODEL),
        search=os.environ.get("GAR_MODEL_SEARCH", search_default),
        compose=compose,
    )


@dataclass(frozen=True)
class AgentContext:
    """Dependencies injected into the agent loop. Wired once per run."""

    llm: LLMClient
    registry: ToolRegistry
    audit: AuditLogger
    store: RunStore
    access: AccessContext
    # Per-phase model tiers (cost control); see make_model_policy.
    models: ModelPolicy = field(default_factory=make_model_policy)
    # Number of search turns the agent gets. Raised from 4 to 6 to favor
    # recall — more rounds let the agent cover more facets / query wordings
    # before stopping (spec §5; recall is the priority for novelty survey).
    max_search_iterations: int = 6
    # Orders the candidate pool by concept-relevance before the sources gate
    # (spec §5 swap point). Selected from the environment: dependency-free BM25
    # by default, or an embedding reranker when GAR_RERANKER=embedding.
    reranker: Reranker = field(default_factory=make_reranker)


# Retry configuration for transient rate-limit errors from the LLM client.
# Tunable via monkeypatch in tests; production defaults assume the per-minute
# Anthropic tier limits, so 30-60s back-off usually clears the window.
RETRY_MAX_ATTEMPTS = 3
RETRY_INITIAL_DELAY_SEC = 30.0
RETRY_MAX_DELAY_SEC = 120.0

# Max attempts at composing the final report when grounding validation
# flags unknown citations. 1 initial + 1 re-prompt = 2 attempts. After
# the cap, the latest report is accepted with a warning in the audit log.
MAX_COMPOSE_ATTEMPTS = 2

# Output-token cap for the report-composition LLM call. Final reports
# regularly exceed the default 4096-token cap (around 12 KB of Markdown),
# which truncates the References section. Sonnet 4.6 supports up to 64K
# output tokens; 16K is a conservative ceiling that fits long surveys
# without inviting runaway costs.
COMPOSE_REPORT_MAX_TOKENS = 16384


def create_run(
    *,
    run_id: str,
    tenant_id: str,
    owner_user_id: str = "local-owner",
    vault_path: Path | None = None,
    notes_content: list[dict[str, str]] | None = None,
) -> RunState:
    """Initialize a fresh RunState.

    Provide exactly one of:

    - ``vault_path`` — for filesystem-backed runs (CLI / unit tests).
      ``state.context["vault_path"]`` is set.
    - ``notes_content`` — for content-upload runs (browser picker /
      future Obsidian plugin). Each item is ``{"path": str, "content": str}``.
      ``state.context["notes_content"]`` is set.

    The presence of one key vs the other in ``context`` is what distinguishes
    "vault mode" from "content mode" downstream.
    """
    if (vault_path is None) == (notes_content is None):
        raise ValueError("provide exactly one of vault_path or notes_content")
    base = start(run_id, tenant_id, owner_user_id)
    context: dict[str, Any] = {}
    if vault_path is not None:
        context["vault_path"] = str(vault_path)
    else:
        # Stored as plain dicts so the state remains JSON-serializable
        # (audit log / future DynamoDB / wait-for-callback payloads).
        assert notes_content is not None
        context["notes_content"] = [
            {"path": n["path"], "content": n["content"]} for n in notes_content
        ]
    return replace(base, context=context)


async def run_until_gate(*, run_id: str, ctx: AgentContext) -> RunState:
    """Drive the agent forward until it hits a HITL gate or terminal state."""
    state = await ctx.store.get(run_id)
    if state is None:
        raise ValueError(f"Unknown run: {run_id}")

    while not is_awaiting_user(state) and not is_terminal(state):
        next_state = await _run_one_phase(state, ctx)
        if next_state is state:
            # No phase handler advanced the state; avoid infinite loop.
            break
        state = next_state
        await ctx.store.save(state)

    return state


async def _run_one_phase(state: RunState, ctx: AgentContext) -> RunState:
    try:
        if state.status is RunStatus.DERIVING_CONCEPT:
            if "notes_content" in state.context:
                documents = [
                    IdeaDocument(path=Path(item["path"]), content=item["content"])
                    for item in state.context["notes_content"]
                ]
                return await phase_derive_concept(state, ctx, documents=documents)
            vault_path = Path(state.context["vault_path"])
            return await phase_derive_concept(state, ctx, vault_path=vault_path)
        if state.status is RunStatus.SEARCHING:
            return await phase_search(state, ctx)
        if state.status is RunStatus.EVALUATING:
            return await phase_compose_report(state, ctx)
        return state
    except InvalidTransition:
        raise
    except Exception as exc:
        return fail(state, error=f"{type(exc).__name__}: {exc}")


# ------------- phase: derive concept -------------


async def phase_derive_concept(
    state: RunState,
    ctx: AgentContext,
    *,
    documents: list[IdeaDocument] | None = None,
    vault_path: Path | None = None,
) -> RunState:
    """Summarize the user's notes into a concept, then request approval.

    Two input modes:

    - ``documents`` — already-loaded notes (browser picker / Obsidian plugin).
      Each document's ``path`` is treated as a display label.
    - ``vault_path`` — walk the filesystem and read every supported file
      (CLI / unit tests). Used when ``documents`` is not provided.
    """
    if documents is None:
        if vault_path is None:
            return fail(state, error="No documents or vault_path provided")
        documents = []
        for path in walk(vault_path):
            try:
                documents.append(read(path))
            except UnsupportedFileType:
                continue

    if not documents:
        location = f" at {vault_path}" if vault_path is not None else ""
        return fail(state, error=f"No readable idea documents{location}")

    # Display path: relative to the vault root when filesystem-backed,
    # already-relative for content mode.
    if vault_path is not None:
        base = vault_path.parent if vault_path.is_file() else vault_path
        display_paths = [doc.path.relative_to(base).as_posix() for doc in documents]
    else:
        display_paths = [doc.path.as_posix() for doc in documents]

    parts = []
    for display_path, doc in zip(display_paths, documents, strict=False):
        parts.append(f"--- {display_path} ---\n{doc.content}\n")
    notes_text = "\n".join(parts)

    response = await _audited_complete(
        ctx,
        state.run_id,
        model=ctx.models.derive,
        system=DERIVE_CONCEPT_SYSTEM,
        messages=[
            Message(
                role="user",
                content=[
                    {
                        "type": "text",
                        "text": (
                            "Here are the user's private notes (possibly "
                            f"unfinished):\n\n{notes_text}\n\n"
                            "Summarize the core concept these notes describe."
                        ),
                    }
                ],
            )
        ],
        tools=[],
    )

    concept = "".join(response.text_blocks).strip()
    if not concept:
        return fail(state, error="LLM returned an empty concept")
    return request_concept_approval(state, concept=concept)


# ------------- phase: search -------------

# Cap on original-note text injected into the search context. Idea notes are
# usually small; this bounds the rare large vault so notes don't dominate the
# prompt or cost. Truncation only loses tail phrases, not whole facets.
NOTES_INJECTION_CAP = 8000


def _original_notes_text(state: RunState, *, cap: int = NOTES_INJECTION_CAP) -> str:
    """Reconstruct the user's original notes for the search phase, both modes.

    Content mode reads ``context['notes_content']``; vault mode re-walks the
    vault. Best-effort: any failure returns an empty string so search still
    runs on the concept alone.
    """
    try:
        if "notes_content" in state.context:
            parts = [
                f"--- {item['path']} ---\n{item['content']}"
                for item in state.context["notes_content"]
            ]
        elif "vault_path" in state.context:
            vault_path = Path(state.context["vault_path"])
            docs: list[str] = []
            for path in walk(vault_path):
                try:
                    doc = read(path)
                except UnsupportedFileType:
                    continue
                docs.append(f"--- {doc.path.as_posix()} ---\n{doc.content}")
            parts = docs
        else:
            return ""
    except Exception:
        return ""
    return "\n\n".join(parts)[:cap]


async def phase_search(state: RunState, ctx: AgentContext) -> RunState:
    """Run the agentic search until the LLM stops requesting tools."""
    concept = state.context.get("concept", "")
    visible = ctx.registry.tools_for(ctx.access)
    agent_tools: list[AgentTool] = [cast(AgentTool, t) for t in visible]
    tool_definitions: list[ToolDefinition] = [t.definition for t in agent_tools]
    tool_by_name = {t.name: t for t in agent_tools}

    # Inject the user's original notes so the agent can mine distinctive
    # technical phrases for literature queries — features lost in the concept
    # summary (spec §5, recall). Bounded so it doesn't dominate the context;
    # the prompt forbids sending raw private notes to web search.
    notes_block = ""
    notes_text = _original_notes_text(state)
    if notes_text:
        notes_block = (
            "\n\nThe user's ORIGINAL NOTES (mine distinctive technical phrases "
            "from these for literature queries; do NOT send raw private content "
            f"to web search):\n{notes_text}"
        )

    messages: list[Message] = [
        Message(
            role="user",
            content=[
                {
                    "type": "text",
                    "text": (
                        f"Concept to investigate:\n{concept}{notes_block}\n\n"
                        "Search broadly for related work using the available "
                        "tools, prioritizing recall across the concept's facets. "
                        "Stop only when the facets are covered and new queries "
                        "stop surfacing relevant work."
                    ),
                }
            ],
        )
    ]
    # Track which query surfaced each candidate (provenance), deduping by
    # source:external_id via `seen`. Cross-query support — how many distinct
    # query angles returned a doc — is exposed at the sources gate so the
    # client can tell foundational, cross-cutting work (high support) from
    # frontier work that only one angle surfaces (v1.3 retrieval-structure
    # slice 1).
    seen: dict[str, dict[str, Any]] = {}
    provenance: dict[str, set[str]] = {}

    for _ in range(ctx.max_search_iterations):
        response = await _audited_complete(
            ctx,
            state.run_id,
            model=ctx.models.search,
            system=SEARCH_SYSTEM,
            messages=messages,
            tools=tool_definitions,
        )
        if not response.tool_uses:
            break

        messages.append(_assistant_message(response))
        tool_result_blocks: list[dict[str, Any]] = []
        for tu in response.tool_uses:
            tool = tool_by_name.get(tu.name)
            if tool is None:
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": f"Unknown tool: {tu.name}",
                        "is_error": True,
                    }
                )
                continue
            try:
                output = await dispatch(
                    tool,
                    tu.input,
                    audit=ctx.audit,
                    run_id=state.run_id,
                    tenant_id=ctx.access.tenant_id,
                )
                if isinstance(output, list):
                    query = str(tu.input.get("query", "")).strip()
                    for doc in output:
                        key = _candidate_key(doc)
                        seen.setdefault(key, doc)
                        if query:
                            provenance.setdefault(key, set()).add(query)
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": json.dumps(output, default=str),
                    }
                )
            except Exception as exc:
                tool_result_blocks.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": f"{type(exc).__name__}: {exc}",
                        "is_error": True,
                    }
                )
        messages.append(Message(role="user", content=tool_result_blocks))

    # Attach cross-query support, then order by concept relevance (BM25).
    # Support is metadata for the client to draw the core/frontier line at
    # presentation time, NOT the sort key — so the most relevant work stays on
    # top and a downstream cap drops the low-relevance tail, not low-support
    # frontier work (v1.3 slice 1, decision: BM25-default sort).
    candidates = [
        {
            **doc,
            "support": len(provenance.get(key, ())),
            "matched_queries": sorted(provenance.get(key, set())),
        }
        for key, doc in seen.items()
    ]
    ranked = ctx.reranker.rank(concept, candidates)

    # Cluster the pool into semantic "directions" for the report's positioning
    # section (slice 3). Only the embedding reranker can do this; BM25 mode and
    # any embedding failure leave directions absent and the report omits the
    # map. Resolve representative ids to titles here, where the pool is in hand,
    # and carry a compact structure forward in context for the compose phase.
    analyze = getattr(ctx.reranker, "analyze_directions", None)
    if analyze is not None:
        result = analyze(concept, ranked)
        if result.directions:
            title_by_id = {_candidate_key(c): c.get("title", "") for c in ranked}
            # Each direction gets a stable id; record which direction each
            # candidate landed in so the sources gate can group the pool (the
            # web UI has no LLM to organize it the way an MCP client does).
            dir_of_candidate: dict[str, int] = {}
            directions = []
            for i, d in enumerate(result.directions):
                directions.append(
                    {
                        "id": i,
                        "representatives": [
                            title_by_id.get(rid, rid) for rid in d.representatives
                        ],
                        "size": len(d.candidate_ids),
                        "contains_concept": d.contains_concept,
                    }
                )
                for cid in d.candidate_ids:
                    dir_of_candidate[cid] = i
            # Annotate in rerank order; a candidate dropped as cluster noise
            # (tiny cluster) gets direction=None and falls to an "other" group.
            for c in ranked:
                c["direction"] = dir_of_candidate.get(_candidate_key(c))
            state = replace(state, context={**state.context, "directions": directions})

    return request_source_selection(state, candidates=ranked)


# ------------- phase: compose report -------------


async def phase_compose_report(state: RunState, ctx: AgentContext) -> RunState:
    """Compose the final report from concept + adopted sources.

    Workflow:
    1. Generate the report via LLM.
    2. If adopted evidence is present, validate citations via grounding.validate.
    3. If any are unknown, re-prompt with feedback (up to MAX_COMPOSE_ATTEMPTS).
    4. After the cap, accept the latest report with a warning in the audit log.
    """
    concept = state.context.get("concept", "")
    adopted_evidence_dicts = state.context.get("adopted_evidence", [])
    adopted_ids = list(state.adopted_source_ids)

    initial_user_text = _build_compose_user_text(
        concept=concept,
        adopted_evidence=adopted_evidence_dicts,
        adopted_ids=adopted_ids,
        directions=state.context.get("directions"),
        notes_text=_original_notes_text(state),
    )
    messages: list[Message] = [
        Message(
            role="user",
            content=[{"type": "text", "text": initial_user_text}],
        )
    ]

    report = ""
    final_validation: GroundingReport | None = None
    for attempt in range(MAX_COMPOSE_ATTEMPTS):
        response = await _audited_complete(
            ctx,
            state.run_id,
            model=ctx.models.compose,
            system=COMPOSE_REPORT_SYSTEM,
            messages=messages,
            tools=[],
            max_tokens=COMPOSE_REPORT_MAX_TOKENS,
        )
        report = "".join(response.text_blocks).strip()
        if not report:
            return fail(state, error="LLM returned an empty report")
        if response.stop_reason == "max_tokens":
            return fail(
                state,
                error=(
                    f"Report composition truncated at max_tokens="
                    f"{COMPOSE_REPORT_MAX_TOKENS} (stop_reason=max_tokens). "
                    "Increase COMPOSE_REPORT_MAX_TOKENS or trim adopted sources."
                ),
            )

        if not adopted_evidence_dicts:
            # No evidence to validate against — accept the first acceptable report.
            break

        validation = _validate_report(report, adopted_evidence_dicts)
        final_validation = validation
        ctx.audit.log(
            AuditRecord(
                run_id=state.run_id,
                tenant_id=ctx.access.tenant_id,
                tool_name="grounding.validate",
                input={
                    "attempt": attempt + 1,
                    "evidence_count": len(adopted_evidence_dicts),
                },
                output={
                    "is_valid": validation.is_valid,
                    "has_citations": validation.has_citations,
                    "citation_count": len(validation.citations),
                    "unknown_count": len(validation.unknown_citations),
                    "unused_evidence_count": len(validation.unused_evidence),
                },
                duration_ms=0.0,
                status="ok",
            )
        )
        if validation.is_valid:
            break
        if attempt + 1 >= MAX_COMPOSE_ATTEMPTS:
            break  # accept latest with warning

        # Build the re-prompt: tell the LLM exactly which citations were
        # unknown and which are valid.
        messages.append(_assistant_message(response))
        unknown_list = ", ".join(c.raw for c in validation.unknown_citations[:10])
        valid_list = ", ".join(
            f"[{e['source_name']}:{e['external_id']}]" for e in adopted_evidence_dicts
        )
        messages.append(
            Message(
                role="user",
                content=[
                    {
                        "type": "text",
                        "text": (
                            "Your previous report contained citations that do NOT "
                            f"appear in the adopted candidate list: {unknown_list}.\n\n"
                            f"The ONLY valid citations are: {valid_list}.\n\n"
                            "Rewrite the entire report. Replace each unknown citation "
                            "with either a valid citation from the list (in the exact "
                            "[source_name:external_id] form) or '(citation not "
                            "available)' if the statement cannot be supported by an "
                            "adopted source."
                        ),
                    }
                ],
            )
        )

    # Linkify citations after validation completes (success or max attempts).
    # No-op when there's no evidence to attach URLs to.
    if adopted_evidence_dicts:
        report = linkify_report(report, adopted_evidence_dicts)
    return request_report_approval(
        state, report=report, validation=_summarize_validation(final_validation)
    )


def _summarize_validation(
    validation: GroundingReport | None,
) -> dict[str, Any] | None:
    """Serialize a grounding result for the report gate's payload.

    Returned to clients (web UI, MCP get_report) so they can show whether the
    report's citations check out. ``None`` when there was no adopted evidence
    to validate against — the report carries no citation guarantees then.
    """
    if validation is None:
        return None
    return {
        "is_valid": validation.is_valid,
        "has_citations": validation.has_citations,
        "unknown_citations": [c.raw for c in validation.unknown_citations],
        "unused_evidence": list(validation.unused_evidence),
    }


def _validate_report(
    report: str, evidence_dicts: list[dict[str, Any]]
) -> GroundingReport:
    """Adapter: validate `report` text against evidence supplied as dicts.

    The agent loop carries evidence as serialized SearchResult dicts in
    ``state.context['adopted_evidence']``. Reconstruct minimal SearchResult
    instances so we can reuse ``grounding.validate`` unchanged.
    """
    evidence = [
        SearchResult(
            source_name=str(e.get("source_name", "")),
            external_id=str(e.get("external_id", "")),
            title="",
            snippet="",
            authors=(),
            published=None,
            url="",
        )
        for e in evidence_dicts
    ]
    return validate_grounding(report, evidence)


# ------------- helpers -------------


def _directions_block(directions: list[dict[str, Any]] | None) -> str:
    """Render the embedding-clustered literature directions for the compose
    prompt (slice 3). The LLM names each cluster from its representative titles
    and writes the positioning section; the CONCEPT-NEAREST marker tells it
    where the user's idea sits among them."""
    if not directions:
        return ""
    lines = [
        "",
        "Literature directions — semantic clusters of the searched pool "
        "(computed from embeddings). Name each direction from its papers and "
        "use them for the positioning section. The cluster marked "
        "[CONCEPT-NEAREST] is where the user's idea sits:",
    ]
    for i, d in enumerate(directions, 1):
        mark = " [CONCEPT-NEAREST]" if d.get("contains_concept") else ""
        lines.append(f"- Direction {i} ({d.get('size', 0)} papers){mark}:")
        for title in d.get("representatives", []):
            lines.append(f"    • {title}")
    return "\n".join(lines)


def _build_compose_user_text(
    *,
    concept: str,
    adopted_evidence: list[dict[str, Any]],
    adopted_ids: list[str],
    directions: list[dict[str, Any]] | None = None,
    notes_text: str = "",
) -> str:
    """Compose the user message for ``phase_compose_report``.

    When ``adopted_evidence`` is non-empty we give the LLM the FULL record
    for each adopted source — id, title, authors, published date, abstract —
    so titles in the rendered report match the citations (the LLM would
    otherwise have to guess titles from ids alone, leading to title /
    citation mismatch). ``notes_text`` is the user's original idea notes, so
    the report's "Referenced idea notes" section can summarize their content
    instead of guessing file names (which compose otherwise never sees).
    """
    notes_block = (
        "\n\nThe user's ORIGINAL IDEA NOTES, each headed by its file name "
        '(--- <file> ---). For the "Referenced idea notes" section, give each '
        "note's file name plus a summary of what it contributes:\n"
        f"{notes_text}"
        if notes_text
        else ""
    )

    if not adopted_evidence:
        return (
            f"Concept:\n{concept}{notes_block}\n\n"
            "Adopted source IDs (from the candidate list at the previous "
            f"gate): {adopted_ids if adopted_ids else 'none'}\n"
            f"{_directions_block(directions)}\n\n"
            "Compose the final report per the structure given in the "
            "system prompt."
        )

    parts: list[str] = [
        f"Concept:\n{concept}{notes_block}\n",
        (
            "Adopted sources for this report — use ONLY these for citations, "
            "and use each source's TITLE, AUTHORS, and ABSTRACT verbatim "
            "(do not invent titles):"
        ),
        "",
    ]
    for e in adopted_evidence:
        source = e.get("source_name", "")
        ext_id = e.get("external_id", "")
        title = e.get("title", "")
        parts.append(f"- [{source}:{ext_id}] — {title}".rstrip(" —"))
        authors = e.get("authors") or []
        if authors:
            shown = ", ".join(authors[:5])
            if len(authors) > 5:
                shown += " et al."
            parts.append(f"  Authors: {shown}")
        published = e.get("published")
        if published:
            parts.append(f"  Published: {str(published)[:10]}")
        snippet = (e.get("snippet") or "").strip()
        if snippet:
            # Cap to keep prompt size predictable; full abstract may not be
            # needed when it's very long.
            cap = 1500
            shown_snippet = snippet if len(snippet) <= cap else snippet[:cap] + "…"
            parts.append(f"  Abstract: {shown_snippet}")
        parts.append("")
    directions_block = _directions_block(directions)
    if directions_block:
        parts.append(directions_block)
    parts.append(
        "Compose the final report per the structure given in the system "
        "prompt. Cite each source using its exact [source_name:external_id] "
        "form, and when describing a source use its actual title and "
        "abstract from the list above."
    )
    return "\n".join(parts)


def _assistant_message(response: LLMResponse) -> Message:
    content: list[dict[str, Any]] = []
    for text in response.text_blocks:
        content.append({"type": "text", "text": text})
    for tu in response.tool_uses:
        content.append(
            {
                "type": "tool_use",
                "id": tu.id,
                "name": tu.name,
                "input": tu.input,
            }
        )
    return Message(role="assistant", content=content)


async def _audited_complete(
    ctx: AgentContext,
    run_id: str,
    *,
    model: str,
    system: str,
    messages: list[Message],
    tools: list[ToolDefinition],
    max_tokens: int = 4096,
) -> LLMResponse:
    """Call the LLM with audit logging and retry-on-rate-limit.

    ``model`` is the per-phase tier (see ModelPolicy). Each attempt — success
    or failure — produces an audit record that records the model used, so a
    run's trace shows which tier ran each call. On RateLimitError we sleep
    (using the provider's retry-after if given, else exponential back-off) and
    try again, up to RETRY_MAX_ATTEMPTS. Other exceptions are logged once and
    re-raised immediately.
    """
    base_input = {
        "model": model,
        "message_count": len(messages),
        "tool_count": len(tools),
        "max_tokens": max_tokens,
    }

    for attempt in range(RETRY_MAX_ATTEMPTS):
        start_time = time.perf_counter()
        try:
            response = await ctx.llm.complete(
                system=system,
                messages=messages,
                tools=tools,
                model=model,
                max_tokens=max_tokens,
            )
        except RateLimitError as exc:
            ctx.audit.log(
                AuditRecord(
                    run_id=run_id,
                    tenant_id=ctx.access.tenant_id,
                    tool_name="llm.complete",
                    input={**base_input, "attempt": attempt + 1},
                    duration_ms=(time.perf_counter() - start_time) * 1000,
                    status="error",
                    error=f"RateLimitError: {exc}",
                )
            )
            if attempt + 1 >= RETRY_MAX_ATTEMPTS:
                raise
            delay = (
                exc.retry_after
                if exc.retry_after is not None
                else (RETRY_INITIAL_DELAY_SEC * (2**attempt))
            )
            await asyncio.sleep(min(delay, RETRY_MAX_DELAY_SEC))
            continue
        except Exception as exc:
            ctx.audit.log(
                AuditRecord(
                    run_id=run_id,
                    tenant_id=ctx.access.tenant_id,
                    tool_name="llm.complete",
                    input={**base_input, "attempt": attempt + 1},
                    duration_ms=(time.perf_counter() - start_time) * 1000,
                    status="error",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            raise

        ctx.audit.log(
            AuditRecord(
                run_id=run_id,
                tenant_id=ctx.access.tenant_id,
                tool_name="llm.complete",
                input={**base_input, "attempt": attempt + 1},
                output={
                    "text_blocks": len(response.text_blocks),
                    "tool_uses": len(response.tool_uses),
                    "stop_reason": response.stop_reason,
                },
                duration_ms=(time.perf_counter() - start_time) * 1000,
                status="ok",
            )
        )
        return response

    raise RuntimeError("retry loop fell through (should not happen)")
