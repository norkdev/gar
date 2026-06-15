"""FastMCP server exposing GAR's governed survey workflow over stdio (D-107).

Run it with the ``gar-mcp`` console script. Configuration is by environment:

- ``GAR_API_URL``  — GAR backend REST base URL (default http://localhost:8000)
- ``GAR_API_KEY``  — optional bearer token sent on every request
- ``GAR_MCP_ROLE`` — ``public`` (default) | ``owner``; gates which tools appear

The server is a thin client of the REST API (plan D-102), so the same process
works against a local or a remote backend by changing only GAR_API_URL.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

from gar_backend.mcp_server.client import GarApiClient
from gar_backend.mcp_server.tools import make_tools, tools_for_role

INSTRUCTIONS = (
    "GAR runs a governed literature survey: it surfaces related work with "
    "citations but never decides novelty — the human does. You drive it as a "
    "sub-agent through three human-in-the-loop gates (concept, sources, "
    "report). At each gate, present the material to the human and get an "
    "explicit decision BEFORE calling the gate tool; do not auto-approve. "
    "Start with start_survey, poll get_run_status to see the open gate, and "
    "retrieve the final report with get_report (you are responsible for "
    "saving it)."
)


def build_server(
    *, client: GarApiClient, role: str = "public", name: str = "gar"
) -> FastMCP:
    """Build a FastMCP server with the tools visible to ``role`` (D-103)."""
    mcp = FastMCP(name, instructions=INSTRUCTIONS)
    for tool in tools_for_role(make_tools(client), role):
        mcp.add_tool(tool.fn, name=tool.name, description=tool.description)
    return mcp


def main() -> None:
    client = GarApiClient()
    role = os.environ.get("GAR_MCP_ROLE", "public")
    build_server(client=client, role=role).run(transport="stdio")
