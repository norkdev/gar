"""Construct the per-run AgentContext from a request or a stored run.

Extracted from api/runs.py so both the HTTP endpoints and the async segment
runner (api/segments.py) can share it without an import cycle — this module
imports no api siblings.
"""

from __future__ import annotations

from pathlib import Path

from gar_backend.agent.llm import LLMClient
from gar_backend.agent.loop import AgentContext
from gar_backend.agent.tools import register_default_tools
from gar_backend.governance.audit import AuditLogger
from gar_backend.governance.hitl import RunState
from gar_backend.governance.rbac import AccessContext, ToolRegistry
from gar_backend.ideas.reader import IdeaDocument
from gar_backend.ideas.search import IdeasSource, InMemoryIdeasSource
from gar_backend.sources.base import PublicSource
from gar_backend.state.runs import RunStore


def build_agent_context(
    *,
    ideas: IdeasSource | InMemoryIdeasSource,
    store: RunStore,
    audit: AuditLogger,
    llm: LLMClient,
    access: AccessContext,
    public_source: PublicSource,
) -> AgentContext:
    """Wire AgentContext for a given run.

    ``public_source`` is injected (not created here) so a single
    process-wide instance can enforce its provider's rate-limit policy
    across all requests. ``ideas`` is per-run because it carries either
    the vault path or the uploaded content.
    """
    registry = ToolRegistry()
    register_default_tools(
        registry,
        public_source=public_source,
        ideas=ideas,
    )
    return AgentContext(
        llm=llm,
        registry=registry,
        audit=audit,
        store=store,
        access=access,
    )


def ideas_source_for_state(state: RunState) -> IdeasSource | InMemoryIdeasSource:
    """Re-construct the right ideas source from a stored state's context.

    Used both at run start and on each gate resume so the agent loop has
    the same data view across requests.
    """
    if "notes_content" in state.context:
        documents = [
            IdeaDocument(path=Path(item["path"]), content=item["content"])
            for item in state.context["notes_content"]
        ]
        return InMemoryIdeasSource(documents)
    return IdeasSource(Path(state.context["vault_path"]))
