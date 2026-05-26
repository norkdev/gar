"""reports/linkify unit tests."""

from gar_backend.reports.linkify import linkify_report


def _evidence(*entries: tuple[str, str, str]) -> list[dict]:
    """Helper: build evidence dicts from (source_name, external_id, url) tuples."""
    return [
        {"source_name": s, "external_id": eid, "url": url}
        for (s, eid, url) in entries
    ]


def test_citation_with_known_url_becomes_external_link() -> None:
    report = "Prior work [arxiv:1.1] is relevant."
    out = linkify_report(
        report, _evidence(("arxiv", "1.1", "https://arxiv.org/abs/1.1"))
    )
    # Linkified form is present; the original un-escaped citation is gone.
    assert r"\[[arxiv:1.1](https://arxiv.org/abs/1.1)\]" in out
    assert "[arxiv:1.1] is" not in out  # un-escaped form removed


def test_citation_appearing_in_body_and_references_both_get_linkified() -> None:
    report = (
        "Claim [arxiv:1.1] supported by prior work.\n\n"
        "## 6. References\n\n- [arxiv:1.1] — Description."
    )
    out = linkify_report(
        report, _evidence(("arxiv", "1.1", "https://arxiv.org/abs/1.1"))
    )
    assert out.count(r"\[[arxiv:1.1](https://arxiv.org/abs/1.1)\]") == 2


def test_citation_without_evidence_kept_as_plain_text() -> None:
    """An unrelated `[foo:99]` in the body has no URL to link to and stays as-is."""
    report = "Claim [foo:99] not in evidence."
    out = linkify_report(report, [])
    assert out == report  # unchanged


def test_citation_with_evidence_but_empty_url_kept_as_plain_text() -> None:
    report = "Claim [arxiv:1.1] with no URL."
    out = linkify_report(report, _evidence(("arxiv", "1.1", "")))
    assert out == report


def test_returns_unmodified_when_no_citations_present() -> None:
    report = "Plain prose with no citations at all."
    assert linkify_report(report, []) == report


def test_multiple_distinct_citations_all_linkified() -> None:
    report = "Two claims: [arxiv:1.1] and [arxiv:2.2]."
    out = linkify_report(
        report,
        _evidence(
            ("arxiv", "1.1", "https://arxiv.org/abs/1.1"),
            ("arxiv", "2.2", "https://arxiv.org/abs/2.2"),
        ),
    )
    assert r"\[[arxiv:1.1](https://arxiv.org/abs/1.1)\]" in out
    assert r"\[[arxiv:2.2](https://arxiv.org/abs/2.2)\]" in out


def test_ideas_source_with_file_uri_linkifies_normally() -> None:
    """Linkifier is source-agnostic — file:// URLs from the ideas source
    are treated like any other URL."""
    report = "From [ideas:note.md] we see..."
    out = linkify_report(
        report, _evidence(("ideas", "note.md", "file:///vault/note.md"))
    )
    assert r"\[[ideas:note.md](file:///vault/note.md)\]" in out


def test_source_name_with_underscore_is_recognized() -> None:
    """Source identifiers like `web_search` (snake_case) match the regex."""
    report = "From [web_search:https://example.com/page] we see..."
    out = linkify_report(
        report,
        _evidence(
            (
                "web_search",
                "https://example.com/page",
                "https://example.com/page",
            )
        ),
    )
    assert (
        r"\[[web_search:https://example.com/page](https://example.com/page)\]"
        in out
    )
