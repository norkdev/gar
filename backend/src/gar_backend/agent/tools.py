"""Tool definitions exposed to the LLM via Anthropic tool use.

Each retrieval source (any ``PublicSource`` implementation, plus the
private ``IdeasSource``) is wrapped into an :class:`AgentTool`:

- ``definition``: Anthropic-compatible schema the LLM sees (name +
  description + JSON schema for input).
- ``handler``: async callable that delegates to the source.

Tools are placed in either the public or the private bucket of a
``ToolRegistry`` (governance pillar #4 / spec §10 seam #1). When the idea
source is not supplied to :func:`register_default_tools`, the private
tool is **not registered** — structurally absent, not refused at call time.

Dispatching a tool goes through :func:`dispatch`, which records every
call in the audit log (governance pillar #3) with input, output summary,
duration, and status — including errors.
"""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from gar_backend.agent.llm import ToolDefinition
from gar_backend.governance.audit import AuditLogger, AuditRecord
from gar_backend.governance.rbac import ToolRegistry
from gar_backend.ideas.search import IdeasSource
from gar_backend.sources.base import PublicSource, SearchResult


IDEAS_TOOL_NAME = "search_private_ideas"


# Generic JSON-schema for any public-source search tool. Both ``query`` and
# ``max_results`` are universally meaningful across sources; if a future
# source needs richer parameters it can expose its own input schema by
# overriding the factory.
_PUBLIC_SEARCH_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Keyword query to send to the source.",
        },
        "max_results": {
            "type": "integer",
            "description": "Maximum results to return.",
            "default": 10,
        },
    },
    "required": ["query"],
}


@dataclass(frozen=True)
class AgentTool:
    """A registered tool: LLM-facing schema + dispatch handler."""

    name: str
    definition: ToolDefinition
    handler: Callable[..., Awaitable[Any]]


def make_public_search_tool(source: PublicSource) -> AgentTool:
    """Wrap any ``PublicSource`` as an AgentTool.

    The source itself declares ``tool_name`` and ``tool_description`` so
    multiple public sources can register side-by-side without any per-source
    glue here.
    """

    async def handler(query: str, max_results: int = 10) -> list[dict[str, Any]]:
        results = await source.search(query, max_results=max_results)
        return [_serialize_result(r) for r in results]

    return AgentTool(
        name=source.tool_name,
        definition=ToolDefinition(
            name=source.tool_name,
            description=source.tool_description,
            input_schema=_PUBLIC_SEARCH_INPUT_SCHEMA,
        ),
        handler=handler,
    )


def make_ideas_tool(source: IdeasSource) -> AgentTool:
    async def handler(query: str, max_results: int = 10) -> list[dict[str, Any]]:
        results = await source.search(query, max_results=max_results)
        return [_serialize_result(r) for r in results]

    return AgentTool(
        name=IDEAS_TOOL_NAME,
        definition=ToolDefinition(
            name=IDEAS_TOOL_NAME,
            description=(
                "Search the user's private idea notes (Markdown) for terms. "
                "Returns matching files with snippets. Use only when directly "
                "relevant to the user's current question — these are private "
                "and must not be quoted externally."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Keyword query against the idea vault.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum results to return.",
                        "default": 10,
                    },
                },
                "required": ["query"],
            },
        ),
        handler=handler,
    )


def register_default_tools(
    registry: ToolRegistry,
    *,
    public_source: PublicSource | None = None,
    ideas: IdeasSource | None = None,
) -> None:
    """Register v1 tools into `registry`.

    Spec §2(c)4: when ``ideas`` is None, the private tool is **not
    registered** — it does not appear in the registry. The agent will never
    see it. This is the structural enforcement of role separation.
    """
    if public_source is not None:
        registry.register_public(make_public_search_tool(public_source))
    if ideas is not None:
        registry.register_private(make_ideas_tool(ideas))


async def dispatch(
    tool: AgentTool,
    input_args: dict[str, Any],
    *,
    audit: AuditLogger,
    run_id: str,
    tenant_id: str,
) -> Any:
    """Run a tool's handler with audit logging around it.

    On exception, an audit record with status='error' is written and the
    exception re-raised so the caller (agent loop) decides how to recover.
    """
    start = time.perf_counter()
    try:
        output = await tool.handler(**input_args)
        audit.log(AuditRecord(
            run_id=run_id,
            tenant_id=tenant_id,
            tool_name=tool.name,
            input=input_args,
            output={
                "result_count": len(output) if isinstance(output, list) else None
            },
            duration_ms=(time.perf_counter() - start) * 1000,
            status="ok",
        ))
        return output
    except Exception as exc:
        audit.log(AuditRecord(
            run_id=run_id,
            tenant_id=tenant_id,
            tool_name=tool.name,
            input=input_args,
            duration_ms=(time.perf_counter() - start) * 1000,
            status="error",
            error=f"{type(exc).__name__}: {exc}",
        ))
        raise


def _serialize_result(result: SearchResult) -> dict[str, Any]:
    return {
        "source_name": result.source_name,
        "external_id": result.external_id,
        "title": result.title,
        "snippet": result.snippet,
        "authors": list(result.authors),
        "published": result.published.isoformat() if result.published else None,
        "url": result.url,
        "citation_anchor": result.citation_anchor,
    }
