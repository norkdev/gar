"""Keyword search across the private idea source.

Two implementations share the same shape (duck-typed; the agent loop
calls only ``.name`` and ``.search()``):

- :class:`IdeasSource` — vault on the local filesystem. Used by the CLI
  and by unit tests. Returns ``file://`` URLs so citations can link back
  to the actual file.
- :class:`InMemoryIdeasSource` — note contents uploaded by the client
  (browser picker, future Obsidian plugin). No filesystem access; the
  ``url`` field is empty since there's no canonical location to point at.

v1 search semantics for both: case-insensitive substring match with AND
across whitespace-split query terms.

Future Work: TF-IDF / BM25 / semantic search via embeddings — swappable
behind the same shape, alongside richer snippets (line ranges, heading
offsets) and front-matter-aware title extraction.
"""

from pathlib import Path

from gar_backend.ideas.reader import IdeaDocument, UnsupportedFileType, read
from gar_backend.ideas.walker import walk
from gar_backend.sources.base import SearchResult

SOURCE_NAME = "ideas"
SNIPPET_WINDOW = 100


class IdeasSource:
    """Keyword search over a private idea vault on the local filesystem."""

    name = SOURCE_NAME

    def __init__(self, vault: Path) -> None:
        self._vault = vault
        self._base = vault.parent if vault.is_file() else vault

    async def list_all(self) -> list[IdeaDocument]:
        """Read every supported idea document under the vault."""
        documents: list[IdeaDocument] = []
        for path in walk(self._vault):
            try:
                documents.append(read(path))
            except UnsupportedFileType:
                continue
        return documents

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


class InMemoryIdeasSource:
    """Keyword search over notes uploaded as raw content.

    Used by the browser picker flow (and, later, by the Obsidian plugin)
    where the backend never touches the user's filesystem. The ``url``
    field on returned search results is empty by design — there's no
    canonical address to link to, and the linkifier leaves such
    citations as plain ``[ideas:path]`` text in the final report.
    """

    name = SOURCE_NAME

    def __init__(self, documents: list[IdeaDocument]) -> None:
        # Documents' .path is treated as a display label only — typically
        # the file's webkitRelativePath from the browser picker.
        self._documents = list(documents)

    async def list_all(self) -> list[IdeaDocument]:
        return list(self._documents)

    async def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        terms = _tokenize(query)
        if not terms:
            return []

        results: list[SearchResult] = []
        for doc in self._documents:
            content_lower = doc.content.lower()
            if not all(term in content_lower for term in terms):
                continue
            display_path = doc.path.as_posix()
            results.append(
                SearchResult(
                    source_name=SOURCE_NAME,
                    external_id=display_path,
                    title=doc.path.stem or display_path,
                    snippet=_snippet(doc.content, terms[0]),
                    authors=(),
                    published=None,
                    url="",  # no real URL — citations stay as plain text
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
