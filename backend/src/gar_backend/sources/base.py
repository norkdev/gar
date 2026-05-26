"""Public source abstract interface.

Any retrieval source backed by an external API implements :class:`PublicSource`.
:class:`SearchResult` is the lingua franca returned to the agent loop.

A source declares its agent-facing tool identity via the ``tool_name`` and
``tool_description`` class attributes so the agent loop can register any
``PublicSource`` implementation without source-specific glue code.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class SearchResult:
    """A single hit from a public source. Used for grounding citations."""

    source_name: str
    external_id: str
    title: str
    snippet: str
    authors: tuple[str, ...]
    published: datetime | None
    url: str
    citation_anchor: str | None = None


class PublicSource(Protocol):
    """Protocol for public retrieval sources.

    Implementations declare:
    - ``name``: short identifier emitted as ``SearchResult.source_name`` and
      used by the grounding validator (citation `[name:external_id]`).
    - ``tool_name``: identifier of the tool the LLM sees in its tool-use
      schema. Convention: ``search_<name>``.
    - ``tool_description``: LLM-facing description of when and why to call
      this source.
    """

    name: str
    tool_name: str
    tool_description: str

    async def search(
        self, query: str, *, max_results: int = 10
    ) -> list[SearchResult]: ...
