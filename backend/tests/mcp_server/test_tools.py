"""Tool-function tests: each tool maps backend responses to its result model,
validates its own arguments, and transmits backend errors faithfully."""

import json

import httpx
import pytest
from gar_backend.mcp_server.client import GarApiError
from gar_backend.mcp_server.models import NoteInput

from tests.mcp_server.conftest import (
    constant_handler,
    make_client,
    recording_handler,
    tools_by_name,
)


async def test_start_survey_returns_run_id_and_status() -> None:
    client = make_client(
        constant_handler({"run_id": "r1", "status": "awaiting_concept_approval"})
    )
    tools = tools_by_name(client)
    out = await tools["start_survey"].fn(notes=[NoteInput(path="a.md", content="x")])
    assert out.run_id == "r1"
    assert out.status == "awaiting_concept_approval"
    await client.aclose()


async def test_start_survey_rejects_empty_notes() -> None:
    client = make_client(constant_handler({}))
    tools = tools_by_name(client)
    with pytest.raises(GarApiError):
        await tools["start_survey"].fn(notes=[])
    await client.aclose()


async def test_list_runs_maps_rows() -> None:
    client = make_client(
        constant_handler(
            [
                {"run_id": "r1", "status": "completed", "updated_at": "2026-01-01"},
                {"run_id": "r2", "status": "searching", "updated_at": "2026-01-02"},
            ]
        )
    )
    tools = tools_by_name(client)
    out = await tools["list_runs"].fn()
    assert [r.run_id for r in out] == ["r1", "r2"]
    assert out[0].updated_at == "2026-01-01"
    await client.aclose()


def _sources_data(n: int) -> dict:
    return {
        "run_id": "r1",
        "status": "awaiting_source_selection",
        "pending_payload": {
            "candidates": [
                {
                    "source_name": "arxiv",
                    "external_id": str(i),
                    "title": f"Paper {i}",
                    "snippet": f"Abstract {i}",
                    "authors": ["A. Author"],
                    "published": "2024-01-01T00:00:00",
                    "url": f"https://arxiv.org/abs/{i}",
                }
                for i in range(n)
            ]
        },
    }


async def test_get_run_status_returns_structured_candidates() -> None:
    client = make_client(constant_handler(_sources_data(2)))
    tools = tools_by_name(client)
    out = await tools["get_run_status"].fn(run_id="r1")
    assert out.current_gate == "sources"
    assert out.candidate_count == 2
    assert [c.id for c in out.candidates] == ["arxiv:0", "arxiv:1"]
    assert out.candidates[0].title == "Paper 0"
    assert out.candidates[0].url == "https://arxiv.org/abs/0"
    assert out.candidates[0].authors == ["A. Author"]
    await client.aclose()


async def test_get_run_status_surfaces_support_and_provenance() -> None:
    data = {
        "run_id": "r1",
        "status": "awaiting_source_selection",
        "pending_payload": {
            "candidates": [
                {
                    "source_name": "arxiv",
                    "external_id": "1",
                    "title": "T",
                    "snippet": "a",
                    "support": 3,
                    "matched_queries": ["q1", "q2", "q3"],
                }
            ]
        },
    }
    client = make_client(constant_handler(data))
    tools = tools_by_name(client)
    out = await tools["get_run_status"].fn(run_id="r1")
    assert out.candidates[0].support == 3
    assert out.candidates[0].matched_queries == ["q1", "q2", "q3"]
    await client.aclose()


def _sources_data_with_directions() -> dict:
    return {
        "run_id": "r1",
        "status": "awaiting_source_selection",
        "context": {
            "directions": [
                {
                    "id": 0,
                    "representatives": ["Near A", "Near B"],
                    "size": 2,
                    "contains_concept": True,
                },
                {
                    "id": 1,
                    "representatives": ["Far X"],
                    "size": 1,
                    "contains_concept": False,
                },
            ]
        },
        "pending_payload": {
            "candidates": [
                {
                    "source_name": "arxiv",
                    "external_id": "0",
                    "title": "Near A",
                    "snippet": "a",
                    "direction": 0,
                },
                {
                    "source_name": "arxiv",
                    "external_id": "1",
                    "title": "Near B",
                    "snippet": "b",
                    "direction": 0,
                },
                {
                    "source_name": "arxiv",
                    "external_id": "2",
                    "title": "Far X",
                    "snippet": "c",
                    "direction": 1,
                },
            ]
        },
    }


async def test_get_run_status_exposes_directions() -> None:
    """The sources gate surfaces the server-side directions so the MCP client
    can present candidates grouped, instead of improvising its own clusters."""
    client = make_client(constant_handler(_sources_data_with_directions()))
    tools = tools_by_name(client)
    out = await tools["get_run_status"].fn(run_id="r1")
    assert [d.id for d in out.directions] == [0, 1]
    assert out.directions[0].contains_concept is True
    assert out.directions[0].representatives == ["Near A", "Near B"]
    assert out.directions[0].size == 2
    # Each candidate carries its cluster id, matching a Direction.id.
    assert [c.direction for c in out.candidates] == [0, 0, 1]
    await client.aclose()


async def test_get_run_status_no_directions_off_sources_gate() -> None:
    """Directions are only surfaced at the sources gate (where the candidate
    list they index is also returned), even if they linger in context."""
    data = {
        "run_id": "r1",
        "status": "awaiting_report_approval",
        "context": {"directions": [{"id": 0, "representatives": ["X"], "size": 1}]},
        "pending_payload": {"report": "# R"},
    }
    client = make_client(constant_handler(data))
    tools = tools_by_name(client)
    out = await tools["get_run_status"].fn(run_id="r1")
    assert out.directions == []
    await client.aclose()


async def test_get_run_status_bm25_mode_has_no_directions() -> None:
    """No context directions (BM25 mode) → empty directions, candidates carry None."""
    client = make_client(constant_handler(_sources_data(2)))
    tools = tools_by_name(client)
    out = await tools["get_run_status"].fn(run_id="r1")
    assert out.directions == []
    assert all(c.direction is None for c in out.candidates)
    await client.aclose()


async def test_get_run_status_includes_abstracts_by_default() -> None:
    client = make_client(constant_handler(_sources_data(1)))
    tools = tools_by_name(client)
    out = await tools["get_run_status"].fn(run_id="r1")
    assert out.candidates[0].abstract == "Abstract 0"
    await client.aclose()


async def test_get_run_status_can_opt_out_of_abstracts() -> None:
    client = make_client(constant_handler(_sources_data(1)))
    tools = tools_by_name(client)
    out = await tools["get_run_status"].fn(run_id="r1", include_abstracts=False)
    assert out.candidates[0].abstract is None
    await client.aclose()


async def test_get_run_status_caps_candidates_and_keeps_total() -> None:
    client = make_client(constant_handler(_sources_data(150)))
    tools = tools_by_name(client)
    out = await tools["get_run_status"].fn(run_id="r1", max_candidates=10)
    assert out.candidate_count == 150  # total preserved
    assert len(out.candidates) == 10  # but truncated
    assert "showing 10" in out.activity_summary
    await client.aclose()


async def test_get_run_status_default_cap_is_100() -> None:
    client = make_client(constant_handler(_sources_data(150)))
    tools = tools_by_name(client)
    out = await tools["get_run_status"].fn(run_id="r1")
    assert len(out.candidates) == 100
    await client.aclose()


async def test_get_run_status_env_sets_default_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GAR_MCP_MAX_CANDIDATES sets the default when no max_candidates is passed."""
    monkeypatch.setenv("GAR_MCP_MAX_CANDIDATES", "5")
    client = make_client(constant_handler(_sources_data(150)))
    tools = tools_by_name(client)  # default captured at make_tools time
    out = await tools["get_run_status"].fn(run_id="r1")
    assert len(out.candidates) == 5
    await client.aclose()


async def test_get_run_status_no_candidates_off_the_sources_gate() -> None:
    data = {
        "run_id": "r1",
        "status": "awaiting_concept_approval",
        "pending_payload": {"concept": "a concept"},
    }
    client = make_client(constant_handler(data))
    tools = tools_by_name(client)
    out = await tools["get_run_status"].fn(run_id="r1")
    assert out.candidates == []
    assert out.candidate_count == 0
    assert "concept" in out.activity_summary.lower()
    await client.aclose()


async def test_get_run_status_no_gate_when_terminal() -> None:
    client = make_client(
        constant_handler({"run_id": "r1", "status": "completed", "pending_payload": {}})
    )
    tools = tools_by_name(client)
    out = await tools["get_run_status"].fn(run_id="r1")
    assert out.current_gate is None
    await client.aclose()


async def test_review_concept_approve_sends_null_edit() -> None:
    rec: list[httpx.Request] = []
    client = make_client(recording_handler({"status": "searching"}, recorder=rec))
    tools = tools_by_name(client)
    await tools["review_concept"].fn(run_id="r1", action="approve")
    assert json.loads(rec[0].content) == {"edited_concept": None}
    await client.aclose()


async def test_review_concept_edit_requires_text() -> None:
    client = make_client(constant_handler({"status": "searching"}))
    tools = tools_by_name(client)
    with pytest.raises(GarApiError):
        await tools["review_concept"].fn(run_id="r1", action="edit", edited_concept="")
    await client.aclose()


async def test_review_concept_edit_sends_text() -> None:
    rec: list[httpx.Request] = []
    client = make_client(recording_handler({"status": "searching"}, recorder=rec))
    tools = tools_by_name(client)
    await tools["review_concept"].fn(
        run_id="r1", action="edit", edited_concept="refined"
    )
    assert json.loads(rec[0].content) == {"edited_concept": "refined"}
    await client.aclose()


async def test_approve_report_reject_is_not_supported() -> None:
    client = make_client(constant_handler({"status": "completed"}))
    tools = tools_by_name(client)
    with pytest.raises(GarApiError) as ei:
        await tools["approve_report"].fn(run_id="r1", action="reject")
    assert "not supported" in str(ei.value)
    await client.aclose()


async def test_approve_report_approve_posts_report_gate() -> None:
    rec: list[httpx.Request] = []
    client = make_client(recording_handler({"status": "completed"}, recorder=rec))
    tools = tools_by_name(client)
    out = await tools["approve_report"].fn(run_id="r1", action="approve")
    assert rec[0].url.path == "/runs/r1/gates/report"
    assert out.status == "completed"
    await client.aclose()


async def test_get_report_returns_markdown_and_validity() -> None:
    data = {
        "run_id": "r1",
        "status": "awaiting_report_approval",
        "pending_payload": {
            "report": "# Survey",
            "report_validation": {
                "is_valid": False,
                "has_citations": True,
                "unknown_citations": ["[ghost:9]"],
                "unused_evidence": ["arxiv:2"],
            },
        },
    }
    client = make_client(constant_handler(data))
    tools = tools_by_name(client)
    out = await tools["get_report"].fn(run_id="r1")
    assert out.markdown == "# Survey"
    assert out.citations_valid is False
    assert any("not found" in w for w in out.warnings)
    assert any("not cited" in w for w in out.warnings)
    await client.aclose()


async def test_get_report_valid_has_no_warnings() -> None:
    data = {
        "run_id": "r1",
        "status": "awaiting_report_approval",
        "pending_payload": {
            "report": "# Survey",
            "report_validation": {
                "is_valid": True,
                "has_citations": True,
                "unknown_citations": [],
                "unused_evidence": [],
            },
        },
    }
    client = make_client(constant_handler(data))
    tools = tools_by_name(client)
    out = await tools["get_report"].fn(run_id="r1")
    assert out.citations_valid is True
    assert out.warnings == []
    await client.aclose()


async def test_get_report_without_validation_reports_unknown_validity() -> None:
    """No adopted evidence -> no validation summary -> citations_valid is null."""
    data = {
        "run_id": "r1",
        "status": "awaiting_report_approval",
        "pending_payload": {"report": "# Survey"},
    }
    client = make_client(constant_handler(data))
    tools = tools_by_name(client)
    out = await tools["get_report"].fn(run_id="r1")
    assert out.markdown == "# Survey"
    assert out.citations_valid is None
    assert out.warnings == []
    await client.aclose()


async def test_get_report_errors_when_no_report_yet() -> None:
    data = {"run_id": "r1", "status": "searching", "pending_payload": {}}
    client = make_client(constant_handler(data))
    tools = tools_by_name(client)
    with pytest.raises(GarApiError) as ei:
        await tools["get_report"].fn(run_id="r1")
    assert "No report" in str(ei.value)
    await client.aclose()


def _timeout_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timeout")


async def test_review_concept_timeout_reports_processing() -> None:
    """A long search times out the POST, but the run keeps going server-side —
    the tool reports 'processing' so the client polls (D-104)."""
    client = make_client(_timeout_handler)
    tools = tools_by_name(client)
    out = await tools["review_concept"].fn(run_id="r1", action="approve")
    assert out.run_id == "r1"
    assert out.status == "processing"
    await client.aclose()


async def test_select_sources_timeout_reports_processing() -> None:
    client = make_client(_timeout_handler)
    tools = tools_by_name(client)
    out = await tools["select_sources"].fn(run_id="r1", adopted_ids=[])
    assert out.status == "processing"
    await client.aclose()


async def test_start_survey_timeout_advises_list_runs() -> None:
    """start_survey has no run_id to poll yet, so it points the client at
    list_runs instead of a bare failure."""
    client = make_client(_timeout_handler)
    tools = tools_by_name(client)
    with pytest.raises(GarApiError) as ei:
        await tools["start_survey"].fn(notes=[NoteInput(path="a.md", content="x")])
    assert "list_runs" in str(ei.value)
    await client.aclose()


async def test_backend_error_propagates_through_tool() -> None:
    """Gate state errors are the backend's responsibility; the MCP tool passes
    them through unchanged (plan §2.3)."""
    client = make_client(
        lambda r: httpx.Response(409, json={"detail": "concept not approved"})
    )
    tools = tools_by_name(client)
    with pytest.raises(GarApiError) as ei:
        await tools["select_sources"].fn(run_id="r1", adopted_ids=["arxiv:1"])
    assert "not in the right state" in str(ei.value)
    await client.aclose()
