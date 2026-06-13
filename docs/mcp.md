# GAR MCP server

`gar-mcp` exposes GAR to Model Context Protocol clients (Claude Code,
Claude Desktop) over stdio. It lets the client drive a literature survey
through GAR's three human-in-the-loop gates — without bypassing the
governance layer.

## What it exposes — and what it doesn't

The surface is **run management and the gates**, not GAR's low-level
retrieval tools. The point of GAR is that every step is grounded, gated,
and audited; a raw `search_arxiv` on the MCP surface would let the
client's own LLM run the retrieval-and-compose loop and return a "survey"
that never passed grounding validation, never stopped at a human gate,
and left no audit trail. Exposing the gates instead makes GAR a
**governed sub-agent**: the client decides *when* to advance; the
governance layer still owns *how* each step runs.

| Tool | Input | Output | Maps to |
|---|---|---|---|
| `start_survey` | `notes: [{path, content}]` | `run_id, status` | `POST /runs` |
| `list_runs` | — | `[{run_id, status, updated_at}]` | `GET /runs` |
| `get_run_status` | `run_id, max_candidates=100, include_abstracts=true` | `status, current_gate?, activity_summary, candidates[], candidate_count` | `GET /runs/{id}` |
| `review_concept` | `run_id, action: approve\|edit, edited_concept?` | `status` | gate 1 |
| `select_sources` | `run_id, adopted_ids: [str]` | `status` | gate 2 |
| `approve_report` | `run_id, action: approve\|reject, feedback?` | `status` | gate 3 |
| `get_report` | `run_id` | `markdown, citations_valid, warnings` | `GET /runs/{id}` |

Notes:

- **Notes are passed by content**, not by path — the backend never reads
  the client's filesystem, so the same server works against a local or a
  remote backend. Reading your vault's files is the MCP client's job
  (Claude Code can read files); saving the returned report is too.
- **Candidates at the sources gate.** `get_run_status` returns the
  candidates as a structured `candidates` list (id, title, abstract,
  authors, published, url), with `candidate_count` as the total found.
  Abstracts are included by default — they are what makes relevance
  judgeable, and they cost only tokens (the backend already fetched them).
  A token-conscious caller sets `include_abstracts=false`; `max_candidates`
  (default 100, or the `GAR_MCP_MAX_CANDIDATES` env default) caps the list.
  Organizing and ranking the list is the client's job — the server returns
  it raw. (Beyond the cap, results can still be missed; better ranking is
  future work, not a bigger cap.)
- **The gates need a human.** Each gate tool's description instructs the
  client to get an explicit human decision before calling it. That
  last mile lives in the client's behavior — present the material, then
  call the tool.
- **`action: reject`** on `approve_report` is accepted in the schema but
  returns a "not supported in v1.1" error (the backend report gate only
  supports approval). The shape is kept for forward compatibility.
- **`citations_valid` / `warnings`** come from the grounding validation
  the backend ran while composing. `citations_valid` is `null` when the
  run adopted no sources (nothing to validate against).

## Configuration

All by environment variable:

| Variable | Default | Meaning |
|---|---|---|
| `GAR_API_URL` | `http://localhost:8000` | GAR backend REST base URL |
| `GAR_API_KEY` | _(unset)_ | Optional bearer token, sent on every request |
| `GAR_MCP_ROLE` | `public` | `public` \| `owner`; selects which tools appear |

`gar-mcp` is a thin client of the REST API — **the backend must be
running** (locally or, after the AWS migration, remotely). Only
`GAR_API_URL` and the auth header change between the two.

## Claude Code

Create a `.mcp.json` at the repository root:

```json
{
  "mcpServers": {
    "gar": {
      "command": "uv",
      "args": ["run", "--package", "gar-backend", "gar-mcp"],
      "env": {
        "GAR_API_URL": "http://localhost:8000",
        "GAR_MCP_ROLE": "public"
      }
    }
  }
}
```

Start the backend first (`uv run --package gar-backend uvicorn
gar_backend.main:app --port 8000`), then Claude Code will launch `gar-mcp`
on demand and the `gar` tools become available.

## Claude Desktop

Add the server to `claude_desktop_config.json` (macOS:
`~/Library/Application Support/Claude/claude_desktop_config.json`). Use an
absolute path to `uv` and the repo via `--directory` so it resolves
outside your shell:

```json
{
  "mcpServers": {
    "gar": {
      "command": "uv",
      "args": [
        "run",
        "--directory", "/absolute/path/to/gar",
        "--package", "gar-backend",
        "gar-mcp"
      ],
      "env": {
        "GAR_API_URL": "http://localhost:8000"
      }
    }
  }
}
```

Restart Claude Desktop after editing. The backend must already be
running.

## A run, end to end

1. `start_survey` with your note contents → `run_id`, status
   `awaiting_concept_approval`.
2. `get_run_status` → shows the derived concept. Confirm with the human,
   then `review_concept` (`approve`, or `edit` with a rewrite).
3. `get_run_status` → lists candidates at the sources gate. With the
   human, pick ids (`source_name:external_id`) → `select_sources`.
4. `get_report` → the composed Markdown plus `citations_valid` /
   `warnings`. Show it to the human.
5. On the human's approval, `approve_report` (`approve`). Save the
   returned report yourself.

Every call is audited (`X-GAR-Client: mcp`), so `audit.jsonl` shows the
full trace with the surface that drove it.
