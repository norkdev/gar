"""SearchResult construction smoke test."""

from gar_backend.sources.base import SearchResult


def test_search_result_can_be_constructed() -> None:
    result = SearchResult(
        source_name="public_src",
        external_id="2301.12345v1",
        title="Sample",
        snippet="Abstract text.",
        authors=("Jane Doe",),
        published=None,
        url="http://example.com/p/2301.12345v1",
    )
    assert result.source_name == "public_src"
    assert result.citation_anchor is None
