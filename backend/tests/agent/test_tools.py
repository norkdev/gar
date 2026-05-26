"""agent/tools unit tests.

Uses ``_FakePublicSource`` (a generic stand-in for any ``PublicSource``)
to exercise tool wiring, registry behavior, and audit logging without
depending on a specific source implementation.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gar_backend.agent.tools import (
    IDEAS_TOOL_NAME,
    AgentTool,
    _serialize_result,
    dispatch,
    make_ideas_tool,
    make_public_search_tool,
    register_default_tools,
)
from gar_backend.governance.audit import AuditLogger, FileAuditSink
from gar_backend.governance.rbac import AccessContext, ToolRegistry
from gar_backend.ideas.search import IdeasSource
from gar_backend.sources.base import SearchResult


class _FakePublicSource:
    """Stand-in for any ``PublicSource`` implementation.

    Records calls and returns canned data so tests can drive the agent
    machinery without hitting a real provider.
    """

    name = "fake_public"
    tool_name = "search_fake_public"
    tool_description = "Fake public-literature source used in unit tests."

    def __init__(self) -> None:
        self.last_query: str | None = None
        self.last_max: int | None = None
        self._results: list[SearchResult] = []

    def returns(self, results: list[SearchResult]) -> None:
        self._results = results

    async def search(
        self, query: str, *, max_results: int = 10
    ) -> list[SearchResult]:
        self.last_query = query
        self.last_max = max_results
        return list(self._results)


# ---------- factory tests ----------


def test_make_public_search_tool_inherits_name_from_source() -> None:
    fake = _FakePublicSource()
    tool = make_public_search_tool(fake)  # type: ignore[arg-type]
    assert isinstance(tool, AgentTool)
    assert tool.name == fake.tool_name
    assert tool.definition.name == fake.tool_name


def test_make_public_search_tool_inherits_description_from_source() -> None:
    fake = _FakePublicSource()
    tool = make_public_search_tool(fake)  # type: ignore[arg-type]
    assert tool.definition.description == fake.tool_description


def test_make_public_search_tool_input_schema_requires_query() -> None:
    schema = make_public_search_tool(
        _FakePublicSource()  # type: ignore[arg-type]
    ).definition.input_schema
    assert "query" in schema["properties"]
    assert "query" in schema["required"]


def test_make_ideas_tool_has_expected_name(tmp_path: Path) -> None:
    tool = make_ideas_tool(IdeasSource(tmp_path))
    assert tool.name == IDEAS_TOOL_NAME
    assert tool.definition.name == IDEAS_TOOL_NAME


# ---------- _serialize_result tests ----------


def test_serialize_result_round_trip_basic_fields() -> None:
    sr = SearchResult(
        source_name="fake_public",
        external_id="X-1",
        title="X",
        snippet="abstract",
        authors=("A", "B"),
        published=datetime(2023, 1, 15, tzinfo=timezone.utc),
        url="http://example/X-1",
    )
    out = _serialize_result(sr)
    assert out["external_id"] == "X-1"
    assert out["authors"] == ["A", "B"]
    assert out["published"] == "2023-01-15T00:00:00+00:00"
    assert out["citation_anchor"] is None


def test_serialize_result_handles_missing_published() -> None:
    sr = SearchResult(
        source_name="ideas",
        external_id="note.md",
        title="X",
        snippet="...",
        authors=(),
        published=None,
        url="file:///x",
    )
    assert _serialize_result(sr)["published"] is None


# ---------- handler delegation tests ----------


async def test_public_search_tool_handler_delegates_to_source() -> None:
    fake = _FakePublicSource()
    fake.returns([
        SearchResult(
            source_name="fake_public",
            external_id="X-1",
            title="T",
            snippet="S",
            authors=(),
            published=None,
            url="http://x",
        )
    ])
    tool = make_public_search_tool(fake)  # type: ignore[arg-type]
    result = await tool.handler(query="q", max_results=3)
    assert fake.last_query == "q"
    assert fake.last_max == 3
    assert len(result) == 1
    assert result[0]["external_id"] == "X-1"


async def test_ideas_tool_handler_delegates_to_source(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("hello world")
    tool = make_ideas_tool(IdeasSource(tmp_path))
    result = await tool.handler(query="hello")
    assert len(result) == 1
    assert result[0]["source_name"] == "ideas"


# ---------- register_default_tools tests ----------


def test_register_default_tools_with_both_sources(tmp_path: Path) -> None:
    fake = _FakePublicSource()
    registry = ToolRegistry()
    register_default_tools(
        registry,
        public_source=fake,  # type: ignore[arg-type]
        ideas=IdeasSource(tmp_path),
    )
    owner = registry.tools_for(
        AccessContext(tenant_id="default", role="owner")
    )
    names = {t.name for t in owner}
    assert names == {fake.tool_name, IDEAS_TOOL_NAME}


def test_register_default_tools_without_ideas_means_no_private_tool() -> None:
    """Spec §2(c)4: if no idea source, the private tool is structurally absent."""
    fake = _FakePublicSource()
    registry = ToolRegistry()
    register_default_tools(
        registry, public_source=fake  # type: ignore[arg-type]
    )
    owner = registry.tools_for(
        AccessContext(tenant_id="default", role="owner")
    )
    names = {t.name for t in owner}
    assert fake.tool_name in names
    assert IDEAS_TOOL_NAME not in names


def test_register_default_tools_only_ideas(tmp_path: Path) -> None:
    registry = ToolRegistry()
    register_default_tools(registry, ideas=IdeasSource(tmp_path))
    owner = registry.tools_for(
        AccessContext(tenant_id="default", role="owner")
    )
    assert [t.name for t in owner] == [IDEAS_TOOL_NAME]


def test_register_default_tools_with_neither_leaves_registry_empty() -> None:
    registry = ToolRegistry()
    register_default_tools(registry)
    assert registry.tools_for(AccessContext(tenant_id="default")) == []


def test_ideas_tool_lands_in_private_bucket(tmp_path: Path) -> None:
    """Non-owner role must not see the ideas tool."""
    fake = _FakePublicSource()
    registry = ToolRegistry()
    register_default_tools(
        registry,
        public_source=fake,  # type: ignore[arg-type]
        ideas=IdeasSource(tmp_path),
    )
    non_owner = registry.tools_for(
        AccessContext(tenant_id="default", role="public_only")
    )
    names = {t.name for t in non_owner}
    assert names == {fake.tool_name}


# ---------- dispatch tests ----------


async def test_dispatch_logs_audit_record_on_success(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(FileAuditSink(audit_path))

    fake = _FakePublicSource()
    fake.returns([
        SearchResult(
            source_name="fake_public",
            external_id="x",
            title="T",
            snippet="S",
            authors=(),
            published=None,
            url="http://x",
        )
    ])
    tool = make_public_search_tool(fake)  # type: ignore[arg-type]

    result = await dispatch(
        tool,
        {"query": "q"},
        audit=logger,
        run_id="run-1",
        tenant_id="default",
    )
    assert len(result) == 1

    record = json.loads(audit_path.read_text())
    assert record["tool_name"] == fake.tool_name
    assert record["status"] == "ok"
    assert record["output"] == {"result_count": 1}
    assert record["duration_ms"] >= 0
    assert record["run_id"] == "run-1"
    assert record["tenant_id"] == "default"
    assert record["input"] == {"query": "q"}


async def test_dispatch_logs_error_and_reraises(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    logger = AuditLogger(FileAuditSink(audit_path))

    class _FailingSource:
        name = "failing"
        tool_name = "search_failing"
        tool_description = "Failing source for tests."

        async def search(
            self, query: str, *, max_results: int = 10
        ) -> list[SearchResult]:
            raise RuntimeError("network down")

    tool = make_public_search_tool(_FailingSource())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="network down"):
        await dispatch(
            tool,
            {"query": "q"},
            audit=logger,
            run_id="run-1",
            tenant_id="default",
        )

    record = json.loads(audit_path.read_text())
    assert record["status"] == "error"
    assert "RuntimeError" in record["error"]
    assert "network down" in record["error"]
