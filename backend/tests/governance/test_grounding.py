"""governance/grounding unit tests.

The validator is source-agnostic: it operates on whatever ``source_name``
strings appear in the evidence list and in the cited text. The tests use a
generic ``"public_src"`` placeholder to make this independence visible.
"""

from gar_backend.governance.grounding import (
    Citation,
    extract_citations,
    validate,
)
from gar_backend.sources.base import SearchResult


def _sr(source_name: str, external_id: str) -> SearchResult:
    return SearchResult(
        source_name=source_name,
        external_id=external_id,
        title="x",
        snippet="x",
        authors=(),
        published=None,
        url="x",
    )


# ---------- extract_citations ----------


def test_extract_citations_finds_single_reference() -> None:
    text = "Graph neural networks have been studied [public_src:p.1]."
    [c] = extract_citations(text)
    assert c.source_name == "public_src"
    assert c.external_id == "p.1"
    assert c.raw == "[public_src:p.1]"


def test_extract_citations_finds_multiple_references() -> None:
    text = "See [public_src:1.1] and [public_src:2.2] for context."
    citations = extract_citations(text)
    assert [c.external_id for c in citations] == ["1.1", "2.2"]


def test_extract_citations_returns_empty_for_text_without_citations() -> None:
    assert extract_citations("Plain text with no references.") == []


def test_extract_citations_ignores_non_citation_brackets() -> None:
    """`[item]` without a `source:id` form is not a citation."""
    text = "List: [item one] and [item two]."
    assert extract_citations(text) == []


def test_extract_citations_accepts_relative_path_in_external_id() -> None:
    """ideas external_id is a vault-relative path."""
    text = "From [ideas:sub/note.md] we see the idea."
    [c] = extract_citations(text)
    assert c.source_name == "ideas"
    assert c.external_id == "sub/note.md"


def test_extract_citations_records_character_span() -> None:
    text = "intro [public_src:1.1] outro"
    [c] = extract_citations(text)
    assert text[c.span[0] : c.span[1]] == "[public_src:1.1]"


def test_extract_citations_accepts_underscored_source_name() -> None:
    """web_search uses an underscored form for citation purposes."""
    text = "Per [web_search:https://example.com/page]"
    [c] = extract_citations(text)
    assert c.source_name == "web_search"


# ---------- validate ----------


def test_validate_all_citations_match_evidence_is_valid() -> None:
    text = "claim [public_src:1.1] another [public_src:2.2]."
    evidence = [_sr("public_src", "1.1"), _sr("public_src", "2.2")]
    report = validate(text, evidence)
    assert report.is_valid
    assert report.has_citations
    assert report.unknown_citations == ()


def test_validate_unknown_citation_flagged_and_invalid() -> None:
    text = "Bogus claim [public_src:9.9.9]."
    evidence = [_sr("public_src", "1.1")]
    report = validate(text, evidence)
    assert not report.is_valid
    assert len(report.unknown_citations) == 1
    assert report.unknown_citations[0].external_id == "9.9.9"


def test_validate_text_without_citations_has_citations_false() -> None:
    report = validate("plain text", [_sr("public_src", "1.1")])
    assert not report.has_citations
    assert report.is_valid  # no fabricated citations either


def test_validate_lists_unused_evidence() -> None:
    """Retrieved results not cited in the text are reported as unused."""
    text = "Cites [public_src:1.1] only."
    evidence = [
        _sr("public_src", "1.1"),
        _sr("public_src", "2.2"),
        _sr("public_src", "3.3"),
    ]
    report = validate(text, evidence)
    assert set(report.unused_evidence) == {"2.2", "3.3"}


def test_validate_unused_evidence_empty_when_all_cited() -> None:
    text = "[public_src:1.1] and [public_src:2.2]"
    evidence = [_sr("public_src", "1.1"), _sr("public_src", "2.2")]
    assert validate(text, evidence).unused_evidence == ()


def test_validate_source_name_must_match_in_citation() -> None:
    """A citation with the right id but wrong source_name is treated as unknown."""
    text = "[public_src:note.md]"  # claim it's public_src, evidence is from ideas
    evidence = [_sr("ideas", "note.md")]
    report = validate(text, evidence)
    assert not report.is_valid
    assert report.unknown_citations[0].source_name == "public_src"


def test_validate_handles_empty_evidence() -> None:
    text = "[public_src:1.1]"
    report = validate(text, [])
    assert not report.is_valid
    assert len(report.unknown_citations) == 1


def test_validate_handles_empty_text_and_evidence() -> None:
    report = validate("", [])
    assert report.is_valid
    assert not report.has_citations
    assert report.unknown_citations == ()
    assert report.unused_evidence == ()


def test_validate_same_citation_appearing_twice_counts_twice() -> None:
    """Repeated citation does not affect validity but is counted."""
    text = "[public_src:1.1] and again [public_src:1.1]"
    evidence = [_sr("public_src", "1.1")]
    report = validate(text, evidence)
    assert report.is_valid
    assert len(report.citations) == 2


def test_validate_external_id_match_is_case_sensitive() -> None:
    text = "[public_src:Foo]"
    evidence = [_sr("public_src", "foo")]
    assert not validate(text, evidence).is_valid


def test_validate_flags_compound_prefix_citation_as_unknown() -> None:
    """LLM may emit ``[author2016:public_src:1.1]`` instead of
    ``[public_src:1.1]``.

    The validator parses this as ``source_name='author2016'`` — which
    does not match any retrieved evidence — so it goes into
    ``unknown_citations``. Caller (agent loop) can re-prompt or surface
    the deviation. Found during a v1 end-to-end smoke run with the LLM.
    """
    text = "Foundational result [abadi2016:public_src:1504.06998v1]"
    evidence = [_sr("public_src", "1504.06998v1")]
    report = validate(text, evidence)
    assert not report.is_valid
    assert len(report.unknown_citations) == 1
    assert report.unknown_citations[0].source_name == "abadi2016"
    # The real ``public_src:1504.06998v1`` is also reported as unused.
    assert report.unused_evidence == ("1504.06998v1",)


def test_validate_treats_plain_author_year_brackets_as_no_citation() -> None:
    """`[Smith 2020]` lacks the colon — not a citation at all (has_citations stays False)."""
    text = "Recent work [Smith 2020] shows that..."
    evidence = [_sr("public_src", "1.1")]
    report = validate(text, evidence)
    assert not report.has_citations
    assert report.unknown_citations == ()


def test_citation_is_frozen() -> None:
    """Citations should not be mutable after parsing."""
    import dataclasses
    [c] = extract_citations("[public_src:1.1]")
    with __import__("pytest").raises(dataclasses.FrozenInstanceError):
        c.external_id = "other"  # type: ignore[misc]
