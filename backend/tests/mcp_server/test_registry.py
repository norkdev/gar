"""Role-gated registry tests (D-103).

v1.1 ships only public tools, so the role mechanism is exercised here with a
synthetic owner-only tool — the receiver for when ideas search is added to the
MCP surface. The property under test: a tool above the caller's role is absent
from the schema, not merely refused at call time.
"""

import asyncio

import httpx
from gar_backend.mcp_server.client import GarApiClient
from gar_backend.mcp_server.server import build_server
from gar_backend.mcp_server.tools import McpTool, make_tools, tools_for_role


async def _noop() -> dict[str, str]:
    return {}


def _public() -> McpTool:
    return McpTool("pub_tool", "public tool", _noop, min_role="public")


def _owner() -> McpTool:
    return McpTool("owner_tool", "owner tool", _noop, min_role="owner")


def test_public_role_omits_owner_only_tool() -> None:
    names = [t.name for t in tools_for_role([_public(), _owner()], "public")]
    assert "pub_tool" in names
    assert "owner_tool" not in names


def test_owner_role_sees_all_tools() -> None:
    names = [t.name for t in tools_for_role([_public(), _owner()], "owner")]
    assert {"pub_tool", "owner_tool"} <= set(names)


def test_unknown_role_defaults_to_public() -> None:
    names = [t.name for t in tools_for_role([_public(), _owner()], "intruder")]
    assert names == ["pub_tool"]


def test_unknown_min_role_fails_closed_to_owner() -> None:
    """A tool with an unrecognized min_role is treated as the most restrictive
    known tier (owner): hidden from public, visible only to owner."""
    weird = McpTool("weird", "d", _noop, min_role="superuser")
    assert tools_for_role([weird], "public") == []
    assert [t.name for t in tools_for_role([weird], "owner")] == ["weird"]


def _client() -> GarApiClient:
    return GarApiClient(
        base_url="http://test",
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    )


def test_v1_1_production_tools_are_all_public() -> None:
    """No private (ideas) tool exists yet, so public sees the full set."""
    tools = make_tools(_client())
    assert all(t.min_role == "public" for t in tools)
    public_names = {t.name for t in tools_for_role(tools, "public")}
    assert public_names == {t.name for t in tools}


def test_build_server_registers_seven_public_tools() -> None:
    server = build_server(client=_client(), role="public")
    registered = asyncio.run(server.list_tools())
    names = {t.name for t in registered}
    assert names == {
        "start_survey",
        "list_runs",
        "get_run_status",
        "review_concept",
        "select_sources",
        "approve_report",
        "get_report",
    }


def test_gate_tools_carry_human_confirmation_note() -> None:
    """The governance last mile lives in the MCP client's behavior, so each
    gate tool's description must instruct it to get a human decision first."""
    server = build_server(client=_client(), role="public")
    registered = {t.name: t for t in asyncio.run(server.list_tools())}
    for name in ("review_concept", "select_sources", "approve_report"):
        assert "GOVERNANCE" in registered[name].description
        assert "human" in registered[name].description.lower()
