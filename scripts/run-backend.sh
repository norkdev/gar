#!/usr/bin/env bash
#
# Start the GAR backend (FastAPI) — the prerequisite for the MCP server, the
# web UI, and the CLI's shared singletons. Runs in the foreground; Ctrl-C stops.
#
# Config (all optional; defaults shown):
#   GAR_PORT=8000     port to bind
#   GAR_RELOAD=1      auto-reload on code changes (set 0 to disable)
#
# Needs ANTHROPIC_API_KEY in .env for real runs (concept derivation, search,
# report composition all call the LLM).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

PORT="${GAR_PORT:-8000}"
RELOAD_ARGS=()
if [[ "${GAR_RELOAD:-1}" != "0" ]]; then
  RELOAD_ARGS+=(--reload)
fi

echo "Starting GAR backend on http://localhost:${PORT} (health: /healthz)" >&2
exec uv run --package gar-backend uvicorn gar_backend.main:app \
  --port "${PORT}" "${RELOAD_ARGS[@]}"
