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

import asyncio
import json
import time
from dataclasses import dataclass, replace
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
from gar_backend.governance.grounding import GroundingReport, validate as validate_grounding
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
from gar_backend.ideas.reader import UnsupportedFileType, read
from gar_backend.ideas.walker import walk
from gar_backend.reports.linkify import linkify_report
from gar_backend.sources.base import SearchResult
from gar_backend.state.runs import RunStore


@dataclass(frozen=True)
class AgentContext:
    """Dependencies injected into the agent loop. Wired once per run."""

    llm: LLMClient
    registry: ToolRegistry
    audit: AuditLogger
    store: RunStore
    access: AccessContext
    model: str = "claude-sonnet-4-6"
    max_search_iterations: int = 4


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


def create_run(*, run_id: str, tenant_id: str, vault_path: Path) -> RunState:
    """Initialize a fresh RunState with the vault path recorded in context."""
    base = start(run_id, tenant_id)
    return replace(base, context={"vault_path": str(vault_path)})


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
    state: RunState, ctx: AgentContext, *, vault_path: Path
) -> RunState:
    """Read every idea file, summarize into a concept, request approval."""
    documents = []
    for path in walk(vault_path):
        try:
            documents.append(read(path))
        except UnsupportedFileType:
            continue

    if not documents:
        return fail(
            state, error=f"No readable idea documents at {vault_path}"
        )

    base = vault_path.parent if vault_path.is_file() else vault_path
    parts = []
    for doc in documents:
        rel = doc.path.relative_to(base).as_posix()
        parts.append(f"--- {rel} ---\n{doc.content}\n")
    notes_text = "\n".join(parts)

    response = await _audited_complete(
        ctx,
        state.run_id,
        system=DERIVE_CONCEPT_SYSTEM,
        messages=[
            Message(
                role="user",
                content=[{
                    "type": "text",
                    "text": (
                        "Here are the user's private notes (possibly "
                        f"unfinished):\n\n{notes_text}\n\n"
                        "Summarize the core concept these notes describe."
                    ),
                }],
            )
        ],
        tools=[],
    )

    concept = "".join(response.text_blocks).strip()
    if not concept:
        return fail(state, error="LLM returned an empty concept")
    return request_concept_approval(state, concept=concept)


# ------------- phase: search -------------


async def phase_search(state: RunState, ctx: AgentContext) -> RunState:
    """Run the agentic search until the LLM stops requesting tools."""
    concept = state.context.get("concept", "")
    visible = ctx.registry.tools_for(ctx.access)
    agent_tools: list[AgentTool] = [cast(AgentTool, t) for t in visible]
    tool_definitions: list[ToolDefinition] = [t.definition for t in agent_tools]
    tool_by_name = {t.name: t for t in agent_tools}

    messages: list[Message] = [
        Message(
            role="user",
            content=[{
                "type": "text",
                "text": (
                    f"Concept to investigate:\n{concept}\n\n"
                    "Search for related work using the available tools. When "
                    "you have a reasonable shortlist, stop calling tools."
                ),
            }],
        )
    ]
    candidates: list[dict[str, Any]] = []

    for _ in range(ctx.max_search_iterations):
        response = await _audited_complete(
            ctx,
            state.run_id,
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
                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": f"Unknown tool: {tu.name}",
                    "is_error": True,
                })
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
                    candidates.extend(output)
                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(output, default=str),
                })
            except Exception as exc:
                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": f"{type(exc).__name__}: {exc}",
                    "is_error": True,
                })
        messages.append(Message(role="user", content=tool_result_blocks))

    return request_source_selection(
        state, candidates=_dedupe_candidates(candidates)
    )


# ------------- phase: compose report -------------


async def phase_compose_report(
    state: RunState, ctx: AgentContext
) -> RunState:
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
    )
    messages: list[Message] = [
        Message(
            role="user",
            content=[{"type": "text", "text": initial_user_text}],
        )
    ]

    report = ""
    last_report_validation: GroundingReport | None = None
    for attempt in range(MAX_COMPOSE_ATTEMPTS):
        response = await _audited_complete(
            ctx,
            state.run_id,
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
        last_report_validation = validation
        ctx.audit.log(AuditRecord(
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
        ))
        if validation.is_valid:
            break
        if attempt + 1 >= MAX_COMPOSE_ATTEMPTS:
            break  # accept latest with warning

        # Build the re-prompt: tell the LLM exactly which citations were
        # unknown and which are valid.
        messages.append(_assistant_message(response))
        unknown_list = ", ".join(c.raw for c in validation.unknown_citations[:10])
        valid_list = ", ".join(
            f"[{e['source_name']}:{e['external_id']}]"
            for e in adopted_evidence_dicts
        )
        messages.append(Message(
            role="user",
            content=[{
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
            }],
        ))

    # Linkify citations after validation completes (success or max attempts).
    # No-op when there's no evidence to attach URLs to.
    if adopted_evidence_dicts:
        report = linkify_report(report, adopted_evidence_dicts)
    return request_report_approval(state, report=report)


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


def _build_compose_user_text(
    *,
    concept: str,
    adopted_evidence: list[dict[str, Any]],
    adopted_ids: list[str],
) -> str:
    """Compose the user message for ``phase_compose_report``.

    When ``adopted_evidence`` is non-empty we give the LLM the FULL record
    for each adopted source — id, title, authors, published date, abstract —
    so titles in the rendered report match the citations (the LLM would
    otherwise have to guess titles from ids alone, leading to title /
    citation mismatch).
    """
    if not adopted_evidence:
        return (
            f"Concept:\n{concept}\n\n"
            "Adopted source IDs (from the candidate list at the previous "
            f"gate): {adopted_ids if adopted_ids else 'none'}\n\n"
            "Compose the final report per the structure given in the "
            "system prompt."
        )

    parts: list[str] = [
        f"Concept:\n{concept}\n",
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
            shown_snippet = (
                snippet if len(snippet) <= cap else snippet[: cap] + "…"
            )
            parts.append(f"  Abstract: {shown_snippet}")
        parts.append("")
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
        content.append({
            "type": "tool_use",
            "id": tu.id,
            "name": tu.name,
            "input": tu.input,
        })
    return Message(role="assistant", content=content)


def _dedupe_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for c in candidates:
        key = (c.get("source_name", ""), c.get("external_id", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


async def _audited_complete(
    ctx: AgentContext,
    run_id: str,
    *,
    system: str,
    messages: list[Message],
    tools: list[ToolDefinition],
    max_tokens: int = 4096,
) -> LLMResponse:
    """Call the LLM with audit logging and retry-on-rate-limit.

    Each attempt — success or failure — produces an audit record. On
    RateLimitError we sleep (using the provider's retry-after if given,
    else exponential back-off) and try again, up to RETRY_MAX_ATTEMPTS.
    Other exceptions are logged once and re-raised immediately.
    """
    base_input = {
        "model": ctx.model,
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
                model=ctx.model,
                max_tokens=max_tokens,
            )
        except RateLimitError as exc:
            ctx.audit.log(AuditRecord(
                run_id=run_id,
                tenant_id=ctx.access.tenant_id,
                tool_name="llm.complete",
                input={**base_input, "attempt": attempt + 1},
                duration_ms=(time.perf_counter() - start_time) * 1000,
                status="error",
                error=f"RateLimitError: {exc}",
            ))
            if attempt + 1 >= RETRY_MAX_ATTEMPTS:
                raise
            delay = exc.retry_after if exc.retry_after is not None else (
                RETRY_INITIAL_DELAY_SEC * (2**attempt)
            )
            await asyncio.sleep(min(delay, RETRY_MAX_DELAY_SEC))
            continue
        except Exception as exc:
            ctx.audit.log(AuditRecord(
                run_id=run_id,
                tenant_id=ctx.access.tenant_id,
                tool_name="llm.complete",
                input={**base_input, "attempt": attempt + 1},
                duration_ms=(time.perf_counter() - start_time) * 1000,
                status="error",
                error=f"{type(exc).__name__}: {exc}",
            ))
            raise

        ctx.audit.log(AuditRecord(
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
        ))
        return response

    raise RuntimeError("retry loop fell through (should not happen)")
