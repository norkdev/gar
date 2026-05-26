"""ideas/search unit tests. Async tests via pytest-asyncio."""

from pathlib import Path

from gar_backend.ideas.reader import IdeaDocument
from gar_backend.ideas.search import SOURCE_NAME, IdeasSource, InMemoryIdeasSource


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


# ---------- list_all (used by phase_derive_concept) ----------


async def test_list_all_returns_every_supported_document(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("alpha")
    (tmp_path / "b.md").write_text("beta")
    source = IdeasSource(tmp_path)
    docs = await source.list_all()
    paths = sorted(d.path.name for d in docs)
    assert paths == ["a.md", "b.md"]


# ---------- InMemoryIdeasSource ----------


def _docs(*entries: tuple[str, str]) -> list[IdeaDocument]:
    return [IdeaDocument(path=Path(p), content=c) for (p, c) in entries]


async def test_inmemory_search_finds_matching_document() -> None:
    source = InMemoryIdeasSource(_docs(("a.md", "Quantum is fun"), ("b.md", "Cooking")))
    results = await source.search("quantum")
    assert len(results) == 1
    assert results[0].external_id == "a.md"


async def test_inmemory_search_is_case_insensitive() -> None:
    source = InMemoryIdeasSource(_docs(("note.md", "Quantum Mechanics")))
    results = await source.search("QUANTUM")
    assert len(results) == 1


async def test_inmemory_search_uses_and_semantics_across_terms() -> None:
    source = InMemoryIdeasSource(
        _docs(("both.md", "quantum and biology"), ("one.md", "just quantum")),
    )
    results = await source.search("quantum biology")
    assert [r.external_id for r in results] == ["both.md"]


async def test_inmemory_search_external_id_is_the_path_as_given() -> None:
    """For content-mode the path is whatever the client provided (a label)."""
    source = InMemoryIdeasSource(_docs(("vault/folder/deep.md", "quantum")))
    results = await source.search("quantum")
    assert results[0].external_id == "vault/folder/deep.md"


async def test_inmemory_search_url_is_empty_string() -> None:
    """No real URL exists in content mode — the linkifier leaves these as plain text."""
    source = InMemoryIdeasSource(_docs(("a.md", "quantum")))
    results = await source.search("quantum")
    assert results[0].url == ""


async def test_inmemory_search_source_name_matches_filesystem_version() -> None:
    """Both implementations must declare the same source name so citations resolve."""
    source = InMemoryIdeasSource(_docs(("a.md", "quantum")))
    results = await source.search("quantum")
    assert results[0].source_name == SOURCE_NAME


async def test_inmemory_search_respects_max_results() -> None:
    source = InMemoryIdeasSource(_docs(*[(f"n{i}.md", "quantum") for i in range(5)]))
    results = await source.search("quantum", max_results=2)
    assert len(results) == 2


async def test_inmemory_list_all_returns_provided_documents() -> None:
    docs = _docs(("a.md", "alpha"), ("b.md", "beta"))
    source = InMemoryIdeasSource(docs)
    out = await source.list_all()
    assert [d.path.name for d in out] == ["a.md", "b.md"]
    assert [d.content for d in out] == ["alpha", "beta"]


async def test_inmemory_list_all_returns_a_copy() -> None:
    """Mutating the returned list must not affect the source's internal state."""
    docs = _docs(("a.md", "alpha"))
    source = InMemoryIdeasSource(docs)
    out = await source.list_all()
    out.clear()
    again = await source.list_all()
    assert len(again) == 1
