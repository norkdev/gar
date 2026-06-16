"""agent/loop integration tests with stubbed LLM and stub source."""

import json
from pathlib import Path
from typing import Any

import pytest
from gar_backend.agent import loop
from gar_backend.agent.llm import LLMResponse, Message, RateLimitError, ToolUse
from gar_backend.agent.loop import (
    AgentContext,
    ModelPolicy,
    _audited_complete,
    create_run,
    make_model_policy,
    phase_compose_report,
    phase_derive_concept,
    phase_search,
    run_until_gate,
)
from gar_backend.agent.tools import register_default_tools
from gar_backend.governance.audit import AuditLogger, FileAuditSink
from gar_backend.governance.hitl import (
    RunStatus,
    approve_concept,
    request_concept_approval,
    request_source_selection,
    select_sources,
)
from gar_backend.governance.rbac import AccessContext, ToolRegistry
from gar_backend.sources.base import SearchResult
from gar_backend.state.runs import InMemoryRunStore

# ---------- helpers ----------


class StubLLM:
    """LLM that returns canned responses in order."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[Any],
        model: str,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self.calls.append(
            {
                "system": system,
                "messages": messages,
                "tools": tools,
                "model": model,
                "max_tokens": max_tokens,
            }
        )
        if not self._responses:
            raise RuntimeError("StubLLM out of canned responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakePublicSource:
    """Stand-in for any ``PublicSource`` implementation."""

    name = "test_source"
    tool_name = "search_test_source"
    tool_description = "Fake public source used in loop integration tests."

    def __init__(self, results: list[SearchResult] | None = None) -> None:
        self._results = results or []
        self.last_query: str | None = None

    async def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        self.last_query = query
        return list(self._results)


def _text(text: str) -> LLMResponse:
    return LLMResponse(text_blocks=(text,), tool_uses=(), stop_reason="end_turn")


def _tool_use(tu_id: str, name: str, input_: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        text_blocks=(),
        tool_uses=(ToolUse(id=tu_id, name=name, input=input_),),
        stop_reason="tool_use",
    )


def _build_ctx(
    tmp_path: Path,
    llm: Any,
    *,
    registry: ToolRegistry | None = None,
    max_search_iterations: int = 10,
    models: ModelPolicy | None = None,
) -> AgentContext:
    return AgentContext(
        llm=llm,
        registry=registry or ToolRegistry(),
        audit=AuditLogger(FileAuditSink(tmp_path / "audit.jsonl")),
        store=InMemoryRunStore(),
        access=AccessContext(tenant_id="default", role="owner"),
        models=models or ModelPolicy("claude-test", "claude-test", "claude-test"),
        max_search_iterations=max_search_iterations,
    )


def _state_at_searching(tmp_path: Path) -> Any:
    state = create_run(run_id="r1", tenant_id="default", vault_path=tmp_path)
    state = request_concept_approval(state, concept="widget concept")
    state = approve_concept(state)
    return state


def _state_at_evaluating(tmp_path: Path, adopted: list[str] | None = None) -> Any:
    state = _state_at_searching(tmp_path)
    state = request_source_selection(state, candidates=[])
    state = select_sources(state, adopted_source_ids=adopted or [])
    return state


# ---------- create_run ----------


def test_create_run_records_vault_path_in_context(tmp_path: Path) -> None:
    state = create_run(run_id="r1", tenant_id="default", vault_path=tmp_path)
    assert state.status is RunStatus.DERIVING_CONCEPT
    assert state.context["vault_path"] == str(tmp_path)


# ---------- phase_derive_concept ----------


async def test_derive_concept_summarizes_and_requests_approval(
    tmp_path: Path,
) -> None:
    (tmp_path / "idea.md").write_text("Half-formed idea about widgets.")
    llm = StubLLM([_text("Concept: a widget system.")])
    ctx = _build_ctx(tmp_path, llm)

    state = create_run(run_id="r1", tenant_id="default", vault_path=tmp_path)
    new = await phase_derive_concept(state, ctx, vault_path=tmp_path)

    assert new.status is RunStatus.AWAITING_CONCEPT_APPROVAL
    assert new.pending_payload["concept"] == "Concept: a widget system."
    assert len(llm.calls) == 1


async def test_derive_concept_empty_vault_fails_without_calling_llm(
    tmp_path: Path,
) -> None:
    llm = StubLLM([])
    ctx = _build_ctx(tmp_path, llm)
    state = create_run(run_id="r1", tenant_id="default", vault_path=tmp_path)
    new = await phase_derive_concept(state, ctx, vault_path=tmp_path)
    assert new.status is RunStatus.FAILED
    assert llm.calls == []


async def test_derive_concept_empty_llm_response_fails(tmp_path: Path) -> None:
    (tmp_path / "idea.md").write_text("notes")
    llm = StubLLM([_text("   ")])
    ctx = _build_ctx(tmp_path, llm)
    state = create_run(run_id="r1", tenant_id="default", vault_path=tmp_path)
    new = await phase_derive_concept(state, ctx, vault_path=tmp_path)
    assert new.status is RunStatus.FAILED


async def test_derive_concept_includes_relative_paths_in_user_message(
    tmp_path: Path,
) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.md").write_text("deep content")
    (tmp_path / "top.md").write_text("top content")
    llm = StubLLM([_text("concept")])
    ctx = _build_ctx(tmp_path, llm)

    state = create_run(run_id="r1", tenant_id="default", vault_path=tmp_path)
    await phase_derive_concept(state, ctx, vault_path=tmp_path)

    user_text = llm.calls[0]["messages"][0].content[0]["text"]
    assert "sub/deep.md" in user_text
    assert "top.md" in user_text


# ---------- phase_search ----------


async def test_search_finishes_when_llm_does_not_call_tools(
    tmp_path: Path,
) -> None:
    llm = StubLLM([_text("Done.")])
    ctx = _build_ctx(tmp_path, llm)

    state = _state_at_searching(tmp_path)
    new = await phase_search(state, ctx)

    assert new.status is RunStatus.AWAITING_SOURCE_SELECTION
    assert new.pending_payload["candidates"] == []
    assert len(llm.calls) == 1


# ---------- recall: original-note injection + breadth (spec §5) ----------


async def test_search_injects_original_notes_vault_mode(tmp_path: Path) -> None:
    """The search phase mines distinctive phrases from the raw notes, not just
    the summarized concept (spec §5)."""
    (tmp_path / "idea.md").write_text("SUBPROFILE confidence threshold gossip")
    llm = StubLLM([_text("Done.")])
    ctx = _build_ctx(tmp_path, llm)

    state = _state_at_searching(tmp_path)
    await phase_search(state, ctx)

    user_text = llm.calls[0]["messages"][0].content[0]["text"]
    assert "ORIGINAL NOTES" in user_text
    assert "SUBPROFILE confidence threshold gossip" in user_text


async def test_search_injects_original_notes_content_mode(tmp_path: Path) -> None:
    llm = StubLLM([_text("Done.")])
    ctx = _build_ctx(tmp_path, llm)
    state = create_run(
        run_id="r1",
        tenant_id="default",
        notes_content=[{"path": "a.md", "content": "NEIGHBOR relevance scoring"}],
    )
    state = request_concept_approval(state, concept="c")
    state = approve_concept(state)

    await phase_search(state, ctx)

    user_text = llm.calls[0]["messages"][0].content[0]["text"]
    assert "NEIGHBOR relevance scoring" in user_text


def test_original_notes_text_caps_length() -> None:
    big = "x" * 20000
    state = create_run(
        run_id="r1",
        tenant_id="default",
        notes_content=[{"path": "a.md", "content": big}],
    )
    assert len(loop._original_notes_text(state, cap=100)) == 100


def test_original_notes_text_empty_without_notes(tmp_path: Path) -> None:
    """Missing/unreadable notes degrade to empty so search still runs."""
    state = create_run(
        run_id="r1", tenant_id="default", vault_path=tmp_path / "nonexistent"
    )
    assert loop._original_notes_text(state) == ""


def test_max_search_iterations_default_is_6() -> None:
    """Raised from 4 to favor recall."""
    field = AgentContext.__dataclass_fields__["max_search_iterations"]
    assert field.default == 6


def test_search_prompt_prioritizes_recall() -> None:
    from gar_backend.agent.prompts import SEARCH_SYSTEM

    assert "RECALL" in SEARCH_SYSTEM
    assert "facet" in SEARCH_SYSTEM.lower()
    # the old precision-capping instruction is gone
    assert "5-20 candidates" not in SEARCH_SYSTEM


def test_public_search_default_max_results_raised() -> None:
    from gar_backend.agent.tools import _PUBLIC_SEARCH_INPUT_SCHEMA

    assert _PUBLIC_SEARCH_INPUT_SCHEMA["properties"]["max_results"]["default"] == 15


async def test_search_collects_candidates_via_tool_dispatch(
    tmp_path: Path,
) -> None:
    fake = _FakePublicSource(
        results=[
            SearchResult("test_source", "T-1", "P1", "abs", (), None, "http://x"),
            SearchResult("test_source", "T-2", "P2", "abs", (), None, "http://y"),
        ]
    )
    registry = ToolRegistry()
    register_default_tools(registry, public_source=fake)  # type: ignore[arg-type]

    llm = StubLLM(
        [
            _tool_use("tu1", fake.tool_name, {"query": "widgets"}),
            _text("Done with shortlist."),
        ]
    )
    ctx = _build_ctx(tmp_path, llm, registry=registry)

    state = _state_at_searching(tmp_path)
    new = await phase_search(state, ctx)

    assert new.status is RunStatus.AWAITING_SOURCE_SELECTION
    candidates = new.pending_payload["candidates"]
    assert [c["external_id"] for c in candidates] == ["T-1", "T-2"]
    assert fake.last_query == "widgets"


async def test_search_dedupes_candidates_by_source_and_id(
    tmp_path: Path,
) -> None:
    duplicate = SearchResult("test_source", "same.1", "P", "x", (), None, "http://x")
    fake = _FakePublicSource(results=[duplicate, duplicate])
    registry = ToolRegistry()
    register_default_tools(registry, public_source=fake)  # type: ignore[arg-type]

    llm = StubLLM(
        [
            _tool_use("tu1", fake.tool_name, {"query": "q1"}),
            _tool_use("tu2", fake.tool_name, {"query": "q2"}),
            _text("Done."),
        ]
    )
    ctx = _build_ctx(tmp_path, llm, registry=registry)

    state = _state_at_searching(tmp_path)
    new = await phase_search(state, ctx)
    assert len(new.pending_payload["candidates"]) == 1


async def test_search_respects_max_iterations(tmp_path: Path) -> None:
    fake = _FakePublicSource(results=[])
    registry = ToolRegistry()
    register_default_tools(registry, public_source=fake)  # type: ignore[arg-type]

    llm = StubLLM(
        [_tool_use(f"tu{i}", fake.tool_name, {"query": "q"}) for i in range(5)]
    )
    ctx = _build_ctx(tmp_path, llm, registry=registry, max_search_iterations=2)

    state = _state_at_searching(tmp_path)
    new = await phase_search(state, ctx)

    assert new.status is RunStatus.AWAITING_SOURCE_SELECTION
    assert len(llm.calls) == 2  # capped at max_search_iterations


async def test_search_reranks_candidates_by_concept_relevance(
    tmp_path: Path,
) -> None:
    """phase_search orders the pool by concept-relevance before the gate, so the
    most relevant work is first regardless of source return order (spec §5)."""
    fake = _FakePublicSource(
        results=[
            SearchResult(
                "test_source",
                "off",
                "Sourdough baking",
                "bread recipes",
                (),
                None,
                "http://x",
            ),
            SearchResult(
                "test_source",
                "on",
                "Widget concept system",
                "a widget concept mechanism",
                (),
                None,
                "http://y",
            ),
        ]
    )
    registry = ToolRegistry()
    register_default_tools(registry, public_source=fake)  # type: ignore[arg-type]
    llm = StubLLM([_tool_use("tu1", fake.tool_name, {"query": "q"}), _text("done")])
    ctx = _build_ctx(tmp_path, llm, registry=registry)

    state = _state_at_searching(tmp_path)  # concept = "widget concept"
    new = await phase_search(state, ctx)

    ids = [c["external_id"] for c in new.pending_payload["candidates"]]
    assert ids[0] == "on"  # the concept-matching paper is lifted to the top


class _QueryFakeSource:
    """A public source that returns different results per query."""

    name = "test_source"
    tool_name = "search_test_source"
    tool_description = "Query-dependent fake source."

    def __init__(self, by_query: dict[str, list[SearchResult]]) -> None:
        self._by_query = by_query

    async def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        return list(self._by_query.get(query, []))


async def test_search_records_cross_query_support(tmp_path: Path) -> None:
    """A doc surfaced by two distinct queries gets support=2 with both queries
    recorded (v1.3 slice 1)."""
    doc = SearchResult("test_source", "D1", "Widget", "abs", (), None, "http://x")
    fake = _FakePublicSource(results=[doc])  # same doc for any query
    registry = ToolRegistry()
    register_default_tools(registry, public_source=fake)  # type: ignore[arg-type]
    llm = StubLLM(
        [
            _tool_use("t1", fake.tool_name, {"query": "beta angle"}),
            _tool_use("t2", fake.tool_name, {"query": "alpha angle"}),
            _text("done"),
        ]
    )
    ctx = _build_ctx(tmp_path, llm, registry=registry)

    new = await phase_search(_state_at_searching(tmp_path), ctx)
    cands = new.pending_payload["candidates"]
    assert len(cands) == 1  # deduped across the two queries
    assert cands[0]["support"] == 2
    assert cands[0]["matched_queries"] == ["alpha angle", "beta angle"]  # sorted


async def test_search_support_does_not_override_relevance_sort(
    tmp_path: Path,
) -> None:
    """Decision (case B): support is metadata, not the sort key. A high-support
    but off-concept doc must NOT outrank a low-support but on-concept doc."""
    core = SearchResult(
        "test_source", "CORE", "Sourdough baking", "bread", (), None, ""
    )
    frontier = SearchResult(
        "test_source",
        "FRONT",
        "Widget concept system",
        "a widget concept",
        (),
        None,
        "",
    )
    fake = _QueryFakeSource(
        {"qa": [core], "qb": [core], "qc": [frontier]}  # core support 2, frontier 1
    )
    registry = ToolRegistry()
    register_default_tools(registry, public_source=fake)  # type: ignore[arg-type]
    llm = StubLLM(
        [
            _tool_use("t1", fake.tool_name, {"query": "qa"}),
            _tool_use("t2", fake.tool_name, {"query": "qb"}),
            _tool_use("t3", fake.tool_name, {"query": "qc"}),
            _text("done"),
        ]
    )
    ctx = _build_ctx(tmp_path, llm, registry=registry)

    new = await phase_search(
        _state_at_searching(tmp_path), ctx
    )  # concept "widget concept"
    cands = new.pending_payload["candidates"]
    by_id = {c["external_id"]: c["support"] for c in cands}
    assert by_id == {"CORE": 2, "FRONT": 1}
    # frontier (on-concept, support 1) ranks above core (off-concept, support 2)
    assert cands[0]["external_id"] == "FRONT"


async def test_search_stores_directions_from_reranker(tmp_path: Path) -> None:
    """When the reranker can cluster (embedding mode), phase_search carries a
    compact directions structure forward in context for the report (slice 3),
    with representative ids resolved to titles."""
    from dataclasses import replace as dc_replace

    from gar_backend.retrieval.directions import Direction, Directions

    class _DirReranker:
        def rank(self, query: str, candidates: list[Any]) -> list[Any]:
            return candidates

        def analyze_directions(
            self, query: str, candidates: list[Any], *, k: int | None = None
        ) -> Directions:
            return Directions(
                directions=[
                    Direction(
                        candidate_ids=["test_source:D1"],
                        representatives=["test_source:D1"],
                        contains_concept=True,
                    )
                ]
            )

    fake = _FakePublicSource(
        results=[SearchResult("test_source", "D1", "Widget paper", "x", (), None, "")]
    )
    registry = ToolRegistry()
    register_default_tools(registry, public_source=fake)  # type: ignore[arg-type]
    llm = StubLLM([_tool_use("t1", fake.tool_name, {"query": "q"}), _text("done")])
    ctx = dc_replace(
        _build_ctx(tmp_path, llm, registry=registry), reranker=_DirReranker()
    )

    new = await phase_search(_state_at_searching(tmp_path), ctx)
    dirs = new.context.get("directions")
    assert dirs and dirs[0]["representatives"] == ["Widget paper"]
    assert dirs[0]["contains_concept"] is True
    assert dirs[0]["size"] == 1
    assert dirs[0]["id"] == 0
    # The candidate is annotated with its direction so the gate can group it.
    cand = new.pending_payload["candidates"][0]
    assert cand["direction"] == 0


def test_compose_user_text_includes_directions_map() -> None:
    text = loop._build_compose_user_text(
        concept="c",
        adopted_evidence=[],
        adopted_ids=["arxiv:1"],
        directions=[
            {
                "representatives": ["Paper A", "Paper B"],
                "size": 5,
                "contains_concept": True,
            }
        ],
    )
    assert "Literature directions" in text
    assert "[CONCEPT-NEAREST]" in text
    assert "Paper A" in text


# ---------- phase_compose_report ----------


async def test_compose_report_uses_concept_and_adopted_ids(
    tmp_path: Path,
) -> None:
    llm = StubLLM([_text("# Report\n[test_source:1.1] etc.")])
    ctx = _build_ctx(tmp_path, llm)
    state = _state_at_evaluating(tmp_path, adopted=["1.1", "2.2"])

    new = await phase_compose_report(state, ctx)

    assert new.status is RunStatus.AWAITING_REPORT_APPROVAL
    assert "[test_source:1.1]" in new.pending_payload["report"]
    user_text = llm.calls[0]["messages"][0].content[0]["text"]
    assert "widget concept" in user_text
    assert "1.1" in user_text


async def test_compose_report_empty_response_fails(tmp_path: Path) -> None:
    llm = StubLLM([_text("")])
    ctx = _build_ctx(tmp_path, llm)
    state = _state_at_evaluating(tmp_path)
    new = await phase_compose_report(state, ctx)
    assert new.status is RunStatus.FAILED


async def test_compose_report_truncated_at_max_tokens_fails_with_clear_error(
    tmp_path: Path,
) -> None:
    """If the LLM hits its output-token cap mid-report, the run fails fast
    with a recognizable error rather than silently saving a truncated report."""
    truncated = LLMResponse(
        text_blocks=("# Partial report ending mid-sent",),
        tool_uses=(),
        stop_reason="max_tokens",
    )
    llm = StubLLM([truncated])
    ctx = _build_ctx(tmp_path, llm)
    state = _state_at_evaluating(tmp_path)

    new = await phase_compose_report(state, ctx)

    assert new.status is RunStatus.FAILED
    assert "max_tokens" in (new.error or "")


async def test_compose_report_uses_elevated_max_tokens(tmp_path: Path) -> None:
    """phase_compose_report must request more output tokens than the default
    so long reports with many references aren't truncated."""
    llm = StubLLM([_text("# Report")])
    ctx = _build_ctx(tmp_path, llm)
    state = _state_at_evaluating(tmp_path)

    await phase_compose_report(state, ctx)

    assert llm.calls[0]["max_tokens"] == loop.COMPOSE_REPORT_MAX_TOKENS
    assert loop.COMPOSE_REPORT_MAX_TOKENS > 4096


# ---------- compose_report: grounding validation + re-prompt ----------


def _state_at_evaluating_with_evidence(
    tmp_path: Path, adopted: list[dict[str, Any]]
) -> Any:
    """Drive to EVALUATING with `adopted` records carried in context.adopted_evidence."""
    state = create_run(run_id="r1", tenant_id="default", vault_path=tmp_path)
    state = request_concept_approval(state, concept="widget concept")
    state = approve_concept(state)
    state = request_source_selection(state, candidates=adopted)
    composite_ids = [f"{c['source_name']}:{c['external_id']}" for c in adopted]
    state = select_sources(state, adopted_source_ids=composite_ids)
    return state


async def test_compose_report_validates_and_skips_retry_when_valid(
    tmp_path: Path,
) -> None:
    state = _state_at_evaluating_with_evidence(
        tmp_path,
        adopted=[{"source_name": "test_source", "external_id": "1.1", "title": "P1"}],
    )
    llm = StubLLM([_text("Good report citing [test_source:1.1]")])
    ctx = _build_ctx(tmp_path, llm)

    new = await phase_compose_report(state, ctx)

    assert new.status is RunStatus.AWAITING_REPORT_APPROVAL
    assert len(llm.calls) == 1  # validated on first attempt, no retry

    records = [
        json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()
    ]
    grounding_records = [r for r in records if r["tool_name"] == "grounding.validate"]
    assert len(grounding_records) == 1
    assert grounding_records[0]["output"]["is_valid"] is True


async def test_compose_report_prompt_includes_adopted_evidence_metadata(
    tmp_path: Path,
) -> None:
    """When the user adopts candidates, the compose prompt receives each
    source's full record (title, authors, abstract) so the LLM does not
    have to guess titles from the id alone."""
    state = _state_at_evaluating_with_evidence(
        tmp_path,
        adopted=[
            {
                "source_name": "test_source",
                "external_id": "1.1",
                "title": "Distinctive Title For Paper One",
                "authors": ["Alice Smith", "Bob Jones"],
                "published": "2023-04-05T00:00:00+00:00",
                "snippet": "Abstract: this paper studies X via Y.",
            }
        ],
    )
    llm = StubLLM([_text("Report citing [test_source:1.1]")])
    ctx = _build_ctx(tmp_path, llm)

    await phase_compose_report(state, ctx)

    user_text = llm.calls[0]["messages"][0].content[0]["text"]
    assert "Distinctive Title For Paper One" in user_text
    assert "Alice Smith" in user_text
    assert "2023-04-05" in user_text
    assert "Abstract: this paper studies X via Y." in user_text


async def test_compose_report_prompt_falls_back_to_ids_without_evidence(
    tmp_path: Path,
) -> None:
    """When the adopted IDs don't match any candidate (so adopted_evidence
    is empty), the prompt cannot include titles — it falls back to the
    minimal id-list form."""
    state = _state_at_evaluating(tmp_path, adopted=["x:1", "x:2"])
    llm = StubLLM([_text("Report.")])
    ctx = _build_ctx(tmp_path, llm)

    await phase_compose_report(state, ctx)

    user_text = llm.calls[0]["messages"][0].content[0]["text"]
    assert "x:1" in user_text
    assert "x:2" in user_text
    # No structured-evidence headers in the prompt because no evidence
    # matched the adopted ids.
    assert "Abstract:" not in user_text
    assert "Authors:" not in user_text


async def test_compose_report_retries_when_citations_are_unknown(
    tmp_path: Path,
) -> None:
    state = _state_at_evaluating_with_evidence(
        tmp_path,
        adopted=[{"source_name": "test_source", "external_id": "1.1", "title": "P1"}],
    )
    llm = StubLLM(
        [
            _text("First try with [author2016:test_source:1.1] bad citation"),
            _text("Second try with [test_source:1.1] correct citation"),
        ]
    )
    ctx = _build_ctx(tmp_path, llm)

    new = await phase_compose_report(state, ctx)

    assert new.status is RunStatus.AWAITING_REPORT_APPROVAL
    assert "[test_source:1.1]" in new.pending_payload["report"]
    assert "[author2016:" not in new.pending_payload["report"]
    assert len(llm.calls) == 2

    records = [
        json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()
    ]
    grounding_records = [r for r in records if r["tool_name"] == "grounding.validate"]
    assert [r["output"]["is_valid"] for r in grounding_records] == [False, True]


async def test_compose_report_accepts_latest_after_max_attempts(
    tmp_path: Path,
) -> None:
    """After MAX_COMPOSE_ATTEMPTS, the latest report is accepted even if
    grounding is still invalid. The audit log records both validations."""
    state = _state_at_evaluating_with_evidence(
        tmp_path,
        adopted=[{"source_name": "test_source", "external_id": "1.1"}],
    )
    llm = StubLLM(
        [
            _text("Try 1 [bogus:1.1]"),
            _text("Try 2 [also_bogus:9.9]"),
        ]
    )
    ctx = _build_ctx(tmp_path, llm)

    new = await phase_compose_report(state, ctx)

    assert new.status is RunStatus.AWAITING_REPORT_APPROVAL
    assert "Try 2" in new.pending_payload["report"]
    assert len(llm.calls) == loop.MAX_COMPOSE_ATTEMPTS

    grounding_records = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text().splitlines()
        if json.loads(line)["tool_name"] == "grounding.validate"
    ]
    assert len(grounding_records) == loop.MAX_COMPOSE_ATTEMPTS
    assert all(r["output"]["is_valid"] is False for r in grounding_records)


async def test_compose_report_skips_validation_when_no_evidence(
    tmp_path: Path,
) -> None:
    """If no adopted_evidence in context, validation is skipped entirely
    (LLM is called only once even if citations are 'unknown')."""
    state = _state_at_evaluating(tmp_path)  # no evidence
    llm = StubLLM([_text("Report with [test_source:1.1] citation")])
    ctx = _build_ctx(tmp_path, llm)

    new = await phase_compose_report(state, ctx)

    assert new.status is RunStatus.AWAITING_REPORT_APPROVAL
    assert len(llm.calls) == 1

    records = [
        json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()
    ]
    grounding_records = [r for r in records if r["tool_name"] == "grounding.validate"]
    assert grounding_records == []  # no validation runs when no evidence


# ---------- run_until_gate ----------


async def test_run_until_gate_unknown_run_raises(tmp_path: Path) -> None:
    ctx = _build_ctx(tmp_path, StubLLM([]))
    with pytest.raises(ValueError, match="Unknown run"):
        await run_until_gate(run_id="missing", ctx=ctx)


async def test_run_until_gate_advances_from_deriving_and_persists(
    tmp_path: Path,
) -> None:
    (tmp_path / "idea.md").write_text("idea content")
    llm = StubLLM([_text("the concept")])
    ctx = _build_ctx(tmp_path, llm)
    state = create_run(run_id="r1", tenant_id="default", vault_path=tmp_path)
    await ctx.store.save(state)

    final = await run_until_gate(run_id="r1", ctx=ctx)
    assert final.status is RunStatus.AWAITING_CONCEPT_APPROVAL

    persisted = await ctx.store.get("r1")
    assert persisted is not None
    assert persisted.status is RunStatus.AWAITING_CONCEPT_APPROVAL


async def test_run_until_gate_catches_exception_into_failed(
    tmp_path: Path,
) -> None:
    (tmp_path / "idea.md").write_text("idea")

    class BoomLLM:
        async def complete(self, **kwargs: Any) -> LLMResponse:
            raise RuntimeError("LLM exploded")

    ctx = _build_ctx(tmp_path, BoomLLM())
    state = create_run(run_id="r1", tenant_id="default", vault_path=tmp_path)
    await ctx.store.save(state)

    final = await run_until_gate(run_id="r1", ctx=ctx)
    assert final.status is RunStatus.FAILED
    assert "LLM exploded" in (final.error or "")


# ---------- _audited_complete ----------


async def test_audited_complete_logs_success_record(tmp_path: Path) -> None:
    llm = StubLLM([_text("hello")])
    ctx = _build_ctx(tmp_path, llm)

    await _audited_complete(
        ctx,
        "r1",
        model="claude-test",
        system="sys",
        messages=[Message("user", [{"type": "text", "text": "x"}])],
        tools=[],
    )

    record = json.loads((tmp_path / "audit.jsonl").read_text())
    assert record["tool_name"] == "llm.complete"
    assert record["status"] == "ok"
    assert record["output"]["text_blocks"] == 1
    assert record["output"]["stop_reason"] == "end_turn"
    assert record["run_id"] == "r1"
    assert record["tenant_id"] == "default"


async def test_audited_complete_logs_error_and_reraises(
    tmp_path: Path,
) -> None:
    class BoomLLM:
        async def complete(self, **kwargs: Any) -> LLMResponse:
            raise RuntimeError("kaboom")

    ctx = _build_ctx(tmp_path, BoomLLM())

    with pytest.raises(RuntimeError, match="kaboom"):
        await _audited_complete(
            ctx, "r1", model="claude-test", system="", messages=[], tools=[]
        )

    record = json.loads((tmp_path / "audit.jsonl").read_text())
    assert record["status"] == "error"
    assert "kaboom" in record["error"]


# ---------- retry-on-rate-limit ----------


async def test_audited_complete_retries_on_rate_limit_then_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop, "RETRY_INITIAL_DELAY_SEC", 0.001)
    monkeypatch.setattr(loop, "RETRY_MAX_DELAY_SEC", 0.001)

    llm = StubLLM([RateLimitError("limited"), _text("recovered")])
    ctx = _build_ctx(tmp_path, llm)

    response = await _audited_complete(
        ctx,
        "r1",
        model="claude-test",
        system="",
        messages=[],
        tools=[],
    )
    assert response.text_blocks == ("recovered",)
    assert len(llm.calls) == 2

    records = [
        json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()
    ]
    assert [r["status"] for r in records] == ["error", "ok"]
    assert records[0]["input"]["attempt"] == 1
    assert records[1]["input"]["attempt"] == 2


async def test_audited_complete_propagates_after_max_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop, "RETRY_INITIAL_DELAY_SEC", 0.001)
    monkeypatch.setattr(loop, "RETRY_MAX_DELAY_SEC", 0.001)

    llm = StubLLM(
        [
            RateLimitError("limit 1"),
            RateLimitError("limit 2"),
            RateLimitError("limit 3"),
        ]
    )
    ctx = _build_ctx(tmp_path, llm)

    with pytest.raises(RateLimitError, match="limit 3"):
        await _audited_complete(
            ctx,
            "r1",
            model="claude-test",
            system="",
            messages=[],
            tools=[],
        )

    records = [
        json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()
    ]
    assert len(records) == loop.RETRY_MAX_ATTEMPTS
    assert all(r["status"] == "error" for r in records)


async def test_audited_complete_does_not_retry_on_non_rate_limit(
    tmp_path: Path,
) -> None:
    llm = StubLLM([ValueError("permanent bug")])
    ctx = _build_ctx(tmp_path, llm)

    with pytest.raises(ValueError, match="permanent bug"):
        await _audited_complete(
            ctx,
            "r1",
            model="claude-test",
            system="",
            messages=[],
            tools=[],
        )

    lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    assert len(lines) == 1  # only one attempt logged, no retry


# ---------- make_model_policy (per-phase cost tiers) ----------


def test_model_policy_defaults_haiku_haiku_sonnet() -> None:
    policy = make_model_policy()
    assert policy.derive == "claude-haiku-4-5"
    assert policy.search == "claude-haiku-4-5"
    assert policy.compose == "claude-sonnet-4-6"


def test_model_policy_env_overrides_each_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GAR_MODEL_DERIVE", "d-model")
    monkeypatch.setenv("GAR_MODEL_SEARCH", "s-model")
    monkeypatch.setenv("GAR_MODEL_COMPOSE", "c-model")
    policy = make_model_policy()
    assert (policy.derive, policy.search, policy.compose) == (
        "d-model",
        "s-model",
        "c-model",
    )


def test_model_policy_thorough_escalates_search_to_compose_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GAR_THOROUGH", "1")
    policy = make_model_policy()
    assert policy.search == policy.compose == "claude-sonnet-4-6"
    assert policy.derive == "claude-haiku-4-5"  # derive stays cheap


def test_model_policy_explicit_search_wins_over_thorough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GAR_THOROUGH", "true")
    monkeypatch.setenv("GAR_MODEL_SEARCH", "explicit-search")
    assert make_model_policy().search == "explicit-search"


# ---------- each phase calls the LLM with its tier model ----------


async def test_derive_uses_derive_tier_model(tmp_path: Path) -> None:
    (tmp_path / "idea.md").write_text("Half-formed idea about widgets.")
    llm = StubLLM([_text("Concept: a widget system.")])
    ctx = _build_ctx(
        tmp_path, llm, models=ModelPolicy("derive-m", "search-m", "compose-m")
    )

    state = create_run(run_id="r1", tenant_id="default", vault_path=tmp_path)
    await phase_derive_concept(state, ctx, vault_path=tmp_path)

    assert llm.calls[-1]["model"] == "derive-m"


async def test_search_uses_search_tier_model(tmp_path: Path) -> None:
    fake = _FakePublicSource(results=[])
    registry = ToolRegistry()
    register_default_tools(registry, public_source=fake)  # type: ignore[arg-type]
    llm = StubLLM([_text("Done.")])
    ctx = _build_ctx(
        tmp_path,
        llm,
        registry=registry,
        models=ModelPolicy("derive-m", "search-m", "compose-m"),
    )

    state = _state_at_searching(tmp_path)
    await phase_search(state, ctx)

    assert all(c["model"] == "search-m" for c in llm.calls)


async def test_compose_uses_compose_tier_model(tmp_path: Path) -> None:
    llm = StubLLM([_text("# Report\n\nNo citations needed.")])
    ctx = _build_ctx(
        tmp_path, llm, models=ModelPolicy("derive-m", "search-m", "compose-m")
    )

    state = _state_at_evaluating(tmp_path)
    await phase_compose_report(state, ctx)

    assert llm.calls[-1]["model"] == "compose-m"
