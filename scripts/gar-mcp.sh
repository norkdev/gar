#!/usr/bin/env bash
#
# Launch the GAR MCP server (stdio) for an MCP client (Claude Code / Desktop).
#
# Point your client's config at this script instead of the raw `uv run` line so
# the environment lives in one place. It runs from the repo root (so the uv
# workspace resolves) and keeps stdout clean — only the MCP JSON-RPC stream goes
# there; everything else goes to stderr, as the stdio transport requires.
#
# The MCP server is a thin client of the backend REST API, so the backend must
# already be running (see scripts/run-backend.sh). If it is unreachable we warn
# on stderr but still start — the client will surface a clear tool error too.
#
# Config (all optional; defaults shown):
#   GAR_API_URL=http://localhost:8000   backend REST base URL
#   GAR_MCP_ROLE=public                 public | owner
#   GAR_API_KEY=                        bearer token, if the backend wants one
#   GAR_MCP_HEALTHCHECK=1               set to 0 to skip the startup probe
set -euo pipefail

# Repo root = parent of this script's directory. Resolve symlinks.
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

export GAR_API_URL="${GAR_API_URL:-http://localhost:8000}"
export GAR_MCP_ROLE="${GAR_MCP_ROLE:-public}"

# Non-fatal reachability probe (stderr only — never touch stdout).
if [[ "${GAR_MCP_HEALTHCHECK:-1}" != "0" ]] && command -v curl >/dev/null 2>&1; then
  if ! curl -fsS --max-time 2 "${GAR_API_URL}/healthz" >/dev/null 2>&1; then
    echo "gar-mcp: warning: backend not reachable at ${GAR_API_URL}." >&2
    echo "gar-mcp: start it first (scripts/run-backend.sh) or set GAR_API_URL." >&2
  fi
fi

# exec so signals (SIGTERM from the client) reach the server directly.
exec uv run --package gar-backend gar-mcp
