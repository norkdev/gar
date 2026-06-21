#!/usr/bin/env bash
#
# Print a ready-to-paste MCP client config for the GAR server, with the path
# to gar-mcp.sh filled in for THIS checkout — so other users don't have to
# hand-substitute an absolute path.
#
#   ./scripts/print-mcp-config.sh                  # Claude Code (.mcp.json shape)
#   ./scripts/print-mcp-config.sh claude-code      # same (relative path)
#   ./scripts/print-mcp-config.sh claude-desktop   # absolute path, for Desktop
#   ./scripts/print-mcp-config.sh claude-code --write   # write ./.mcp.json
#
# It prints a block to paste and never edits your client's own config file
# (e.g. Claude Desktop's claude_desktop_config.json) — that's invasive and
# fragile. The only file it can write (--write, Claude Code only) is the
# repo-local .mcp.json, which is ours to own.
#
# Env overrides (defaults shown):
#   GAR_API_URL=http://localhost:8000   GAR_MCP_ROLE=public
# For the cloud backend, also set the Cognito M2M creds — they're then included
# in the printed config (omit them for a local backend with auth disabled):
#   GAR_COGNITO_TOKEN_ENDPOINT  GAR_COGNITO_CLIENT_ID
#   GAR_COGNITO_CLIENT_SECRET   GAR_COGNITO_SCOPE   (default gar-api/access)
# e.g.  GAR_API_URL=https://…lambda-url…/ GAR_COGNITO_CLIENT_ID=… \
#       GAR_COGNITO_CLIENT_SECRET=… GAR_COGNITO_TOKEN_ENDPOINT=…/oauth2/token \
#       ./scripts/print-mcp-config.sh claude-desktop
set -euo pipefail

usage() {
  echo "usage: print-mcp-config.sh [claude-code|claude-desktop] [--write]" >&2
  echo "  claude-code      relative ./scripts/gar-mcp.sh (default)" >&2
  echo "  claude-desktop   absolute path for clients without a project cwd" >&2
  echo "  --write          write the repo-local .mcp.json (claude-code only)" >&2
}

# Repo root = parent of this script's dir (resolve symlinks).
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

target="claude-code"
write=0
for arg in "$@"; do
  case "$arg" in
    claude-code | claude-desktop) target="$arg" ;;
    --write) write=1 ;;
    -h | --help) usage; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; usage; exit 2 ;;
  esac
done

api_url="${GAR_API_URL:-http://localhost:8000}"
role="${GAR_MCP_ROLE:-public}"

if [[ "$target" == "claude-desktop" ]]; then
  command_path="${REPO_ROOT}/scripts/gar-mcp.sh"
else
  command_path="./scripts/gar-mcp.sh"
fi

# Always pass the base URL + role; add the Cognito M2M creds when they're in the
# environment so the printed config drives the cloud backend.
env_entries=(
  "\"GAR_API_URL\": \"${api_url}\""
  "\"GAR_MCP_ROLE\": \"${role}\""
)
if [[ -n "${GAR_COGNITO_TOKEN_ENDPOINT:-}${GAR_COGNITO_CLIENT_ID:-}" ]]; then
  env_entries+=(
    "\"GAR_COGNITO_TOKEN_ENDPOINT\": \"${GAR_COGNITO_TOKEN_ENDPOINT:-}\""
    "\"GAR_COGNITO_CLIENT_ID\": \"${GAR_COGNITO_CLIENT_ID:-}\""
    "\"GAR_COGNITO_CLIENT_SECRET\": \"${GAR_COGNITO_CLIENT_SECRET:-}\""
    "\"GAR_COGNITO_SCOPE\": \"${GAR_COGNITO_SCOPE:-gar-api/access}\""
  )
fi

# Join the entries with commas, indented for the JSON env block.
env_block=""
for i in "${!env_entries[@]}"; do
  comma=","
  [[ "$i" -eq $((${#env_entries[@]} - 1)) ]] && comma=""
  env_block+="        ${env_entries[$i]}${comma}"$'\n'
done

json=$(
  cat <<JSON
{
  "mcpServers": {
    "gar": {
      "command": "${command_path}",
      "env": {
${env_block}      }
    }
  }
}
JSON
)

if [[ "$write" == "1" ]]; then
  if [[ "$target" != "claude-code" ]]; then
    echo "--write only applies to claude-code (it writes the repo-local .mcp.json)." >&2
    echo "For Claude Desktop, paste the printed block into claude_desktop_config.json." >&2
    exit 2
  fi
  dest="${REPO_ROOT}/.mcp.json"
  if [[ -e "$dest" ]]; then
    echo "refusing to overwrite existing ${dest} — edit it by hand or remove it first." >&2
    exit 1
  fi
  printf '%s\n' "$json" >"$dest"
  echo "wrote ${dest} — Claude Code picks up the 'gar' server from the repo root." >&2
  exit 0
fi

printf '%s\n' "$json"

if [[ "$target" == "claude-desktop" ]]; then
  {
    echo
    echo "# Paste the block above into your Claude Desktop config, then restart it:"
    echo "#   macOS:   ~/Library/Application Support/Claude/claude_desktop_config.json"
    echo "#   Windows: %APPDATA%\\Claude\\claude_desktop_config.json"
    if [[ -n "${GAR_COGNITO_TOKEN_ENDPOINT:-}${GAR_COGNITO_CLIENT_ID:-}" ]]; then
      echo "# Cloud backend — already deployed; no local backend to start."
    else
      echo "# Local backend — start it first: ./scripts/run-backend.sh"
    fi
  } >&2
fi
