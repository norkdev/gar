"""governance/rbac unit tests."""

from dataclasses import dataclass

import pytest

from gar_backend.governance.rbac import AccessContext, ToolRegistry


@dataclass(frozen=True)
class FakeTool:
    name: str


def test_owner_role_sees_both_public_and_private_tools() -> None:
    registry = ToolRegistry()
    registry.register_public(FakeTool("public_src"))
    registry.register_private(FakeTool("ideas"))
    visible = registry.tools_for(
        AccessContext(tenant_id="default", role="owner")
    )
    names = {t.name for t in visible}
    assert names == {"public_src", "ideas"}


def test_non_owner_role_does_not_see_private_tools() -> None:
    """Spec §2(c)4: private tools must be invisible to non-owner roles."""
    registry = ToolRegistry()
    registry.register_public(FakeTool("public_src"))
    registry.register_private(FakeTool("ideas"))
    visible = registry.tools_for(
        AccessContext(tenant_id="default", role="public_only")
    )
    names = {t.name for t in visible}
    assert names == {"public_src"}
    assert "ideas" not in names


def test_empty_registry_returns_empty_list() -> None:
    registry = ToolRegistry()
    assert registry.tools_for(AccessContext(tenant_id="default")) == []


def test_only_public_tools_visible_to_owner() -> None:
    registry = ToolRegistry()
    registry.register_public(FakeTool("public_src"))
    visible = registry.tools_for(
        AccessContext(tenant_id="default", role="owner")
    )
    assert [t.name for t in visible] == ["public_src"]


def test_only_private_tools_visible_to_owner() -> None:
    registry = ToolRegistry()
    registry.register_private(FakeTool("ideas"))
    visible = registry.tools_for(
        AccessContext(tenant_id="default", role="owner")
    )
    assert [t.name for t in visible] == ["ideas"]


def test_only_private_tools_invisible_to_non_owner() -> None:
    registry = ToolRegistry()
    registry.register_private(FakeTool("ideas"))
    assert (
        registry.tools_for(
            AccessContext(tenant_id="default", role="public_only")
        )
        == []
    )


def test_registering_public_with_existing_private_name_raises() -> None:
    registry = ToolRegistry()
    registry.register_private(FakeTool("shared"))
    with pytest.raises(ValueError, match="already registered as private"):
        registry.register_public(FakeTool("shared"))


def test_registering_private_with_existing_public_name_raises() -> None:
    registry = ToolRegistry()
    registry.register_public(FakeTool("shared"))
    with pytest.raises(ValueError, match="already registered as public"):
        registry.register_private(FakeTool("shared"))


def test_re_registering_same_name_in_same_bucket_overwrites() -> None:
    registry = ToolRegistry()
    registry.register_public(FakeTool("public_src"))
    new_tool = FakeTool("public_src")
    registry.register_public(new_tool)
    visible = registry.tools_for(
        AccessContext(tenant_id="default", role="owner")
    )
    assert len(visible) == 1
    assert visible[0] is new_tool


def test_access_context_default_role_is_owner() -> None:
    ctx = AccessContext(tenant_id="default")
    assert ctx.role == "owner"


def test_tenant_id_does_not_filter_visibility_in_v1() -> None:
    """Spec §10 seam #1: tenant_id flows through, v1 doesn't enforce."""
    registry = ToolRegistry()
    registry.register_public(FakeTool("public_src"))
    visible_default = registry.tools_for(
        AccessContext(tenant_id="default", role="owner")
    )
    visible_other = registry.tools_for(
        AccessContext(tenant_id="other-tenant", role="owner")
    )
    assert [t.name for t in visible_default] == [t.name for t in visible_other]


def test_public_tools_come_before_private_in_output() -> None:
    """Deterministic ordering: public first, then private. Useful for stable
    prompt construction in the agent."""
    registry = ToolRegistry()
    registry.register_private(FakeTool("ideas"))
    registry.register_public(FakeTool("public_src"))
    visible = registry.tools_for(
        AccessContext(tenant_id="default", role="owner")
    )
    assert [t.name for t in visible] == ["public_src", "ideas"]
