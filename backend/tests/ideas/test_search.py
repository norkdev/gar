"""ideas/search unit tests. Async tests via pytest-asyncio."""

from pathlib import Path

from gar_backend.ideas.search import SOURCE_NAME, IdeasSource


async def test_search_finds_matching_file(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("Quantum computing is interesting.")
    (tmp_path / "b.md").write_text("Cooking with garlic.")
    source = IdeasSource(tmp_path)
    results = await source.search("quantum")
    assert len(results) == 1
    assert results[0].title == "a"


async def test_search_is_case_insensitive(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_text("Quantum Mechanics")
    source = IdeasSource(tmp_path)
    results = await source.search("QUANTUM")
    assert len(results) == 1


async def test_search_uses_and_semantics_across_terms(tmp_path: Path) -> None:
    (tmp_path / "both.md").write_text("quantum and biology")
    (tmp_path / "only_one.md").write_text("just quantum")
    source = IdeasSource(tmp_path)
    results = await source.search("quantum biology")
    assert [r.title for r in results] == ["both"]


async def test_search_empty_query_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("anything")
    source = IdeasSource(tmp_path)
    assert await source.search("") == []


async def test_search_no_match_returns_empty(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("hello")
    source = IdeasSource(tmp_path)
    assert await source.search("nonexistent") == []


async def test_search_respects_max_results(tmp_path: Path) -> None:
    for i in range(5):
        (tmp_path / f"n{i}.md").write_text("quantum")
    source = IdeasSource(tmp_path)
    results = await source.search("quantum", max_results=2)
    assert len(results) == 2


async def test_search_result_source_name_is_ideas(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("quantum")
    source = IdeasSource(tmp_path)
    results = await source.search("quantum")
    assert results[0].source_name == SOURCE_NAME


async def test_search_external_id_is_relative_path_within_vault(
    tmp_path: Path,
) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.md").write_text("quantum")
    source = IdeasSource(tmp_path)
    results = await source.search("quantum")
    assert results[0].external_id == "sub/deep.md"


async def test_search_url_is_file_uri(tmp_path: Path) -> None:
    f = tmp_path / "a.md"
    f.write_text("quantum")
    source = IdeasSource(tmp_path)
    results = await source.search("quantum")
    assert results[0].url.startswith("file://")
    assert results[0].url.endswith("a.md")


async def test_search_snippet_contains_query_term(tmp_path: Path) -> None:
    body = "Lorem ipsum. Quantum mechanics is fascinating. More text follows."
    (tmp_path / "a.md").write_text(body)
    source = IdeasSource(tmp_path)
    results = await source.search("quantum")
    assert "quantum" in results[0].snippet.lower()


async def test_search_snippet_is_bounded(tmp_path: Path) -> None:
    """Snippet should not return the whole file for large notes."""
    long_text = ("padding " * 200) + "needle" + (" tail" * 200)
    (tmp_path / "long.md").write_text(long_text)
    source = IdeasSource(tmp_path)
    results = await source.search("needle")
    # Snippet should be a small window around the match.
    assert len(results[0].snippet) < 500
    assert "needle" in results[0].snippet


async def test_search_inherits_gitignore_filtering(tmp_path: Path) -> None:
    """The walker honors .gitignore; search inherits that filtering."""
    (tmp_path / ".gitignore").write_text("private.md\n")
    (tmp_path / "private.md").write_text("quantum")
    (tmp_path / "public.md").write_text("quantum")
    source = IdeasSource(tmp_path)
    results = await source.search("quantum")
    assert [r.title for r in results] == ["public"]


async def test_search_single_file_vault(tmp_path: Path) -> None:
    f = tmp_path / "note.md"
    f.write_text("quantum")
    source = IdeasSource(f)
    results = await source.search("quantum")
    assert len(results) == 1
    assert results[0].title == "note"
    assert results[0].external_id == "note.md"


async def test_search_no_authors_for_private_notes(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("quantum")
    source = IdeasSource(tmp_path)
    results = await source.search("quantum")
    assert results[0].authors == ()
