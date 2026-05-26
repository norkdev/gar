"""Keyword search across discovered idea files.

v1: case-insensitive substring match with AND semantics across whitespace-split
query terms. Returns SearchResult records with `source_name = "ideas"`,
`external_id` set to the vault-relative POSIX path, and `url` as a `file://`
URI so the agent / report can refer back unambiguously.

Future Work: TF-IDF / BM25 / semantic search via embeddings — swappable
behind the same `IdeasSource.search()` shape, alongside richer snippets
(line ranges, heading offsets) and front-matter-aware title extraction.
"""

from pathlib import Path

from gar_backend.ideas.reader import UnsupportedFileType, read
from gar_backend.ideas.walker import walk
from gar_backend.sources.base import SearchResult

SOURCE_NAME = "ideas"
SNIPPET_WINDOW = 100


class IdeasSource:
    """Keyword search over a private idea vault (file or folder)."""

    name = SOURCE_NAME

    def __init__(self, vault: Path) -> None:
        self._vault = vault
        self._base = vault.parent if vault.is_file() else vault

    async def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        terms = _tokenize(query)
        if not terms:
            return []

        results: list[SearchResult] = []
        for path in walk(self._vault):
            try:
                doc = read(path)
            except UnsupportedFileType:
                # walker already filters by suffix, but be defensive
                continue

            content_lower = doc.content.lower()
            if not all(term in content_lower for term in terms):
                continue

            relative = path.relative_to(self._base)
            results.append(
                SearchResult(
                    source_name=SOURCE_NAME,
                    external_id=relative.as_posix(),
                    title=path.stem,
                    snippet=_snippet(doc.content, terms[0]),
                    authors=(),
                    published=None,
                    url=path.resolve().as_uri(),
                )
            )
            if len(results) >= max_results:
                break
        return results


def _tokenize(query: str) -> list[str]:
    return [t.lower() for t in query.split() if t]


def _snippet(content: str, term: str) -> str:
    idx = content.lower().find(term)
    if idx < 0:
        return " ".join(content[: SNIPPET_WINDOW * 2].split()).strip()
    start = max(0, idx - SNIPPET_WINDOW)
    end = min(len(content), idx + len(term) + SNIPPET_WINDOW)
    snippet = " ".join(content[start:end].split()).strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(content):
        snippet = snippet + "…"
    return snippet
