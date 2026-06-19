"""Role-based access control for tool registration.

Spec §2(c)4 and §10 seam #1: public-source tools and private-source tools
live in separate buckets. Roles without private access never see private
tools in the registry's output — visibility is enforced structurally, not
by refusing calls at invocation time.

v1: single tenant (`default`), single role (`owner`). The structure supports
future multi-tenant / multi-role expansion without changes to callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# Roles permitted to see private (idea-source) tools.
PRIVATE_ROLES: frozenset[str] = frozenset({"owner"})


class Tool(Protocol):
    """Anything registrable. Only `name` is required at the registry level."""

    name: str


@dataclass(frozen=True)
class AccessContext:
    """Identity carried with each tool-list request.

    Two boundaries (D-202): ``tenant_id`` is the isolation boundary (recorded on
    every operation, seam #1); ``user_id`` is the idea-privacy boundary (whose
    private content this is). ``role`` gates tool visibility. Through v2.0 there
    is one user, so ``user_id`` defaults; v2.1 fills it from the verified token.
    Owner-scoped data filtering by ``user_id`` lands with sessions (D-204).
    """

    tenant_id: str
    role: str = "owner"
    user_id: str = "local-owner"


class ToolRegistry:
    """Holds tools split into public and private buckets.

    Cross-bucket name collisions are rejected at registration time so callers
    can't accidentally aliasing a private tool's name from the public bucket.
    """

    def __init__(self) -> None:
        self._public: dict[str, Tool] = {}
        self._private: dict[str, Tool] = {}

    def register_public(self, tool: Tool) -> None:
        if tool.name in self._private:
            raise ValueError(
                f"Tool name '{tool.name}' is already registered as private"
            )
        self._public[tool.name] = tool

    def register_private(self, tool: Tool) -> None:
        if tool.name in self._public:
            raise ValueError(f"Tool name '{tool.name}' is already registered as public")
        self._private[tool.name] = tool

    def tools_for(self, context: AccessContext) -> list[Tool]:
        """Tools visible to `context`. Private tools are excluded entirely
        when `context.role` is not in PRIVATE_ROLES — realizing spec §2(c)4
        ('private tools must be invisible to non-owner roles').
        """
        visible = list(self._public.values())
        if context.role in PRIVATE_ROLES:
            visible.extend(self._private.values())
        return visible
