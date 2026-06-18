# GAR v1.x — Design and Implementation Report

*Guided Agentic Retrieval for Literature Survey*
Status: **v1.3.2** · backend complete, verified end-to-end against live arXiv + Anthropic (+ Voyage) APIs · 360 backend tests · ~4.7k LOC backend Python, ~1.4k LOC frontend TS.

This report consolidates the design rationale and implementation of GAR across the v1.x line. It is a synthesis of the working spec (`spec.md`), the expansion plan and its decision log (`plan.md`, D-101…D-108 + tracks §5–§9), the public narrative (`README.md`), and the code itself. Where the documents disagree, `spec.md` is the contract.

---

## 1. Executive summary

GAR helps a researcher or engineer **survey published literature against their own in-progress idea** — and deliberately stops there. The agent gathers the closest related work, every claim carrying a citation the user can inspect, organizes it, and composes a hedged report. It **never decides whether the idea is novel or its contribution genuine**: that judgement depends on what the literature actually shows and how the user scopes their own contribution, so the human keeps it.

The system is *dynamic* — an LLM plans tool use, calls retrieval, reasons over results, and decides whether to keep going — but every link in that chain runs under a **governance layer** the implementation makes externally visible. Four mechanisms (grounding, human-in-the-loop gating, an audit log, role-based access) are each isolated to one file so a reader can find and reason about them independently.

A single governed agent loop is reachable three ways — a **CLI**, a **Web UI**, and an **MCP server** — that differ only at the edges (how notes get in, where the report goes); the loop, governance, retrieval, and grounding are shared.

v1 is intentionally narrow (Markdown-only private notes; arXiv title+abstract+metadata only; in-process state) but is built with **seven scale seams** so the AWS migration (v2.x) is a swap of implementations, not a rewrite.

---

## 2. The problem, and the core stance

A literature survey is meaningful only against the public record; an unfinished private idea is not public. Comparing the two is exactly the work a researcher needs help with — *and* exactly where two failure modes lurk:

1. **A model that judges novelty** is at best confidently wrong and at worst dangerous: the user might quote "this is novel" in a paper or grant and end up defending an assertion the model invented.
2. **A model that mixes public and private content** can leak the user's unpublished idea into a public-knowledge context (e.g. a web-search query), compromising the very originality they were refining in private.

GAR's two load-bearing stances answer these directly:

- **Prepare material, not a judgement.** The compose prompt requires hedged language ("the most similar candidate appears to be X; the main differentiator appears to be Y") and forbids final novelty statements. HITL gates put the human into the loop at exactly the moments a judgement would otherwise emerge — after candidates are gathered, and before the report is saved.
- **Physically separate public and private sources.** Public sources (`sources/`) and private notes (`ideas/`) are different packages, routed through different tool registries (`governance/rbac.py`), and the search prompt carries an explicit rule against passing private content to web search.

---

## 3. Non-negotiables (from `spec.md`)

`spec.md` (the working contract, in Japanese) fixes the following. They are requirements, not implementation conveniences.

- **The tool prepares material; it never decides** (spec §2a). Novelty/contribution is the human's.
- **Strict public/private separation** (spec §2b). Mixing defeats the purpose and risks leaking private ideas.
- **Governance layer — four pillars that must appear in the implementation, not just the README** (spec §2c):
  1. **Grounding required** — every statement about a paper cites a retrieved source; if it cannot be cited, the agent says so rather than fabricate.
  2. **Human-in-the-loop approval** — gating private-idea access, any external transmission, and any comparative conclusion.
  3. **Audit log** — every tool call recorded (what, when, which source) so a run can be replayed.
  4. **Role-based access** — private-idea tools are *structurally invisible/uncallable* to non-owner roles, not merely refused at call time.
- **An agent loop**, not a fixed retrieve→generate pipeline (spec §5). The LLM plans, executes, accumulates, decides whether to continue.
- **Retrieval sources behind one abstract interface** (spec §4) and **retrieval *techniques* treated as tools/design judgements** (spec §5), leaving room to compare them in evaluation.
- **Tight v1 scope** (spec §11): private side Markdown-only; public side (arXiv) title + abstract + metadata only; PDF/images, more public sources, multi-tenant runtime, Bedrock LLM all Future Work with seams left.
- **Source identifiers stay generic.** The `PublicSource` Protocol carries `name`/`tool_name`/`tool_description`; the agent loop hard-codes no specific source. `arxiv` appears only in `sources/arxiv.py`, its tests, and the one wiring line in `api/deps.py`.
- **Architecture is React + FastAPI + AWS** (spec §9): static frontend on S3+CloudFront; FastAPI on Lambda+Function URLs via Mangum; Step Functions for orchestration with wait-for-callback HITL; DynamoDB for state; S3 for audit/private content/cache. No VPC/NAT in v1.
- **Personal project, public repo.** No employer code, customer data, or internal know-how; credentials out of git.

---

## 4. System architecture

```
┌─────────────────────┐         ┌──────────────────────────────────────┐
│ Vite + React + TS   │  HTTP   │ FastAPI                              │
│ (5 views, SSE feed) │ ◄────►  │  ├─ api/        REST + SSE endpoints │
└─────────────────────┘         │  ├─ agent/      LLM client + loop    │
   MCP client ──► gar-mcp ──HTTP─┤  ├─ governance/ audit/hitl/grounding │
   (Claude Code/Desktop)         │  │                /rbac              │
   CLI ──► in-process ──────────►│  ├─ retrieval/  rerank/embedding/... │
                                 │  ├─ sources/    public retrieval     │
                                 │  ├─ ideas/      private (Markdown)    │
                                 │  ├─ reports/    compose → save        │
                                 │  └─ state/      RunStore + checkpoints│
                                 └──┬────────┬─────────┬──────────┬─────┘
                                    ▼        ▼         ▼          ▼
                                 arXiv   Anthropic   Voyage    Local vault
                                 (3s ToU  (Haiku +   (embeddings, (Markdown +
                                  + 429   Sonnet,    opt-in)      .ignore)
                                  backoff) per phase)
```

- **Layered backend** (one concern per package): `api/` (HTTP + SSE), `agent/` (loop + LLM), `governance/` (the four pillars), `retrieval/` (rerank/cluster), `sources/` (public), `ideas/` (private), `reports/` (compose+save), `state/` (run store + checkpoints), plus `mcp_server/` (the stdio surface) and `cli.py`.
- **Synchronous-within-a-gate.** The agent loop runs to completion *inside* each gate POST; `RunState` persists across requests via a `RunStore` (in-memory in v1). Long phases that outrun a client timeout still complete server-side because state is durable; the MCP surface returns `status: "processing"` and the client polls (D-104).
- **One hop to the data plane.** The UI never calls arXiv/Anthropic/Voyage directly — only the FastAPI backend does. This is scale seam #2 and the reason the Web UI and MCP server work unchanged against a future remote backend.

---

## 5. The agent loop and the run-state machine

The loop lives in `agent/loop.py`: **three pure phase functions** plus an orchestrator (`run_until_gate`) that drives them through HITL gates over a frozen `RunState`.

| Phase | Function | Model tier | Produces |
|---|---|---|---|
| Derive concept | `phase_derive_concept` | Haiku | A concept (lead sentence + facet bullets) from the notes → **gate 1** |
| Search | `phase_search` | Haiku (×N iterations) | A reranked, optionally clustered candidate pool → **gate 2** |
| Compose report | `phase_compose_report` | Sonnet | A grounded Markdown report → **gate 3** |

The status machine (`governance/hitl.py`) is a set of **pure functions over a frozen dataclass** — `create_run` → `DERIVING_CONCEPT` → `AWAITING_CONCEPT_APPROVAL` → (`SEARCHING`) → `AWAITING_SOURCE_SELECTION` → (`EVALUATING`) → `AWAITING_REPORT_APPROVAL` → `COMPLETED` (or `FAILED`). Because the gates are *durable state* rather than in-flight awaits, a paused run can resume from a different process — the Step-Functions `wait-for-callback` pattern, written for in-process v1 (`state/checkpoints.py` carries the durable form).

The LLM is reached through an `LLMClient` **Protocol** (`agent/llm.py`); `AnthropicLLM` is the v1 implementation. Every call goes through `_audited_complete`, which logs each attempt (success or failure), retries on `RateLimitError` with provider-supplied or exponential back-off, and records the per-call model.

### Under the hood: the Anthropic Messages tool-use loop

The search phase is a hand-written instance of Anthropic's **agentic tool-use loop**. There is no managed "tool runner" — GAR drives the loop itself, which is exactly what lets governance wrap every step.

The single underlying primitive is the **Messages API**. `AnthropicLLM.complete` calls `anthropic.AsyncAnthropic().messages.create(...)` with four things:

- **`system`** — the phase's system prompt (`SEARCH_SYSTEM`: the recall/breadth instructions).
- **`messages`** — the running conversation: a list of turns, each `{role, content}` where `role` is `user` or `assistant` and `content` is a list of **typed blocks**. GAR's `Message` dataclass mirrors Anthropic's block shape exactly.
- **`tools`** — the callable tools as JSON-Schema (`ToolDefinition` = `name` / `description` / `input_schema`), built from the **RBAC-filtered** registry (`ctx.registry.tools_for(ctx.access)`), so the model only ever sees tools its role may call.
- **`model`** / **`max_tokens`** — the per-phase model tier (§8) and output budget.

The reply returns as content blocks of two kinds GAR consumes — **`text`** (prose) and **`tool_use`** (`{id, name, input}`: a request to run a named tool with arguments) — plus a **`stop_reason`**. `_parse_response` decodes these into `LLMResponse(text_blocks, tool_uses, stop_reason)`.

The loop (`phase_search`) is the canonical pattern:

```text
messages = [ user: concept + original-note phrases + "search broadly…" ]
for _ in range(max_search_iterations):            # bounded budget (default 6)
    resp = messages.create(system=SEARCH_SYSTEM, messages=messages, tools=…)
    if not resp.tool_uses:                         # nothing more to call → done
        break
    messages.append( assistant: resp.text + tool_use blocks )    # echo the model's turn back
    results = []
    for tu in resp.tool_uses:                      # run each requested tool
        out = dispatch(tool_by_name[tu.name], tu.input, …audited…)
        results.append({ "type":"tool_result", "tool_use_id": tu.id,
                         "content": json.dumps(out) })            # keyed back to the request
    messages.append( user: results )               # feed results back; loop re-calls the model
```

Each turn the model **sees the entire accumulated conversation** — its own prior queries and every tool result — and decides the next move: emit more `tool_use` blocks (often several queries in one turn, covering facets in parallel), or stop. GAR keys "stop" off the *absence* of `tool_use` blocks (equivalently `stop_reason: "end_turn"` rather than `"tool_use"`). Two correspondences the protocol enforces: every `tool_use` the model emits must be answered by a `tool_result` carrying the same **`tool_use_id`**, and those results go back in a **`user`** turn — that closure is what lets the model reason over outcomes. A failed tool is not an exception that breaks the run; it returns as a `tool_result` with **`is_error: true`**, so the model can react (retry a different query, skip).

An annotated slice of one run's `messages` array:

```jsonc
[
  { "role": "user",      "content": [{ "type": "text", "text": "Concept to investigate: …\nORIGINAL NOTES: …\nSearch broadly…" }] },
  { "role": "assistant", "content": [                                  // stop_reason: "tool_use"
      { "type": "text",     "text": "I'll cover the facets in parallel." },
      { "type": "tool_use", "id": "tu_1", "name": "search_arxiv", "input": { "query": "multi-agent user profiling" } },
      { "type": "tool_use", "id": "tu_2", "name": "search_arxiv", "input": { "query": "contract-net agent coordination" } } ] },
  { "role": "user",      "content": [
      { "type": "tool_result", "tool_use_id": "tu_1", "content": "[{…10 SearchResults…}]" },
      { "type": "tool_result", "tool_use_id": "tu_2", "content": "[{…10 SearchResults…}]" } ] },
  { "role": "assistant", "content": [ /* more tool_use → loop again, or only text → loop ends */ ] }
]
```

v1 uses **plain tool use** — no extended/adaptive thinking and no streaming (the LLM client notes streaming is deferred). The loop knows only the `LLMClient` Protocol, so a Bedrock implementation of the same `complete()` swaps the provider without touching the loop (seam #5).

### What controls and bounds the loop

The loop is *model-driven* (the LLM picks the queries and decides when to stop) but *code-governed* at every edge:

- **A hard iteration budget.** `max_search_iterations` (default 6) caps the model↔tool round-trips — the loop cannot spin forever no matter what the model does.
- **Structural tool gating (RBAC).** The `tools` array *is* the RBAC-filtered registry. A tool the role can't use is **absent from the schema the model sees**, so the model cannot emit a `tool_use` for a tool it was never shown — private-note search simply doesn't exist for a public role. (Governance by construction, not refusal.)
- **Per-call and per-dispatch auditing.** Every `messages.create` goes through `_audited_complete` (one record per attempt, with model + message/tool counts, plus rate-limit retry honoring the provider's `retry-after`). Every tool dispatch goes through `dispatch`, which audits input, output count, and duration. The audit log therefore reconstructs the exact interleaving of model turns and tool calls.
- **Errors stay inside the loop.** Tool exceptions become `is_error` tool results, not crashes — the boundary degrades gracefully and the model gets a chance to recover.
- **No managed agent runtime, on purpose.** GAR does *not* hand the loop to an SDK tool-runner or a server-side agent. The manual loop is what makes the four governance pillars insertable *between* every step; an opaque runner would hide exactly the points GAR needs to audit and gate.

### The other two phases: one-shot derive, and a code-controlled compose retry

Not every phase is a model-driven loop — the same Messages primitive is used three different ways:

- **Derive** is a single `messages.create` with **no tools**: the model reads the notes (in the `user` turn) and returns the concept text. One turn, no loop.
- **Compose** is also tool-free generation (`tools=[]`, a larger `max_tokens`), but it runs inside a **code-controlled loop the model never sees**. After each generation, `governance/grounding.py` parses the report and checks every `[source:id]` against the adopted evidence; on a mismatch the *code* appends a corrective `user` turn (the specific deviation + the valid citation list) and calls again, up to `MAX_COMPOSE_ATTEMPTS` (default 2). This is the mirror image of the search loop: there the **model** decides whether to continue; here **GAR's code** does, using a deterministic validator as the stop condition. Both are "agentic," but only one hands control to the model — and even that one runs on a leash.

---

## 6. The governance layer (four pillars)

One file per concern, under `governance/`. The agent loop calls into each in a way the audit log makes externally observable — there is no shadow path.

### 6.1 Grounding (`grounding.py`)
Every statement about a paper must carry an `[source_name:external_id]` citation present in the adopted-evidence set. After compose, a validator parses the report and cross-checks each citation. On a mismatch the LLM is re-prompted with the specific deviation and the valid citation list; the loop is bounded (`MAX_COMPOSE_ATTEMPTS`, default 2). If it still emits unknown citations, the latest report is accepted **with a warning in the audit log** — the human reads both and decides. (This path fired and recovered on a real smoke run: two unknown citations on attempt 1, a clean report on attempt 2.)

### 6.2 Human-in-the-loop (`hitl.py`)
The three gates are the only places the run advances, and each is the human's decision: **concept** (approve / edit), **sources** (adopt which candidates; zero is allowed → an honest "no related work" report), **report** (approve). The pure-function state machine makes the pauses durable for free. (There is deliberately *no* functional "reject" at the report gate in v1.1 — see §13 / `plan.md` §9.)

### 6.3 Audit (`audit.py`)
A JSONL log: every LLM call, tool dispatch, and validator pass is one structured record carrying a `schema_version` (so schema evolution doesn't strand old logs) and, since v1.1, a `client` field (`web`/`cli`/`mcp`) from the `X-GAR-Client` request header. A ~20-message / ~30-tool run lands in ~80–100 KB. The log is the substrate for replay and for the later retrieval-technique evaluation.

### 6.4 RBAC (`rbac.py`)
Public and private tools live in separate registries keyed by role. A non-owner role's tool list **does not contain** the private (ideas-search) tool — it is structurally absent from the schema the LLM sees, not refused at call time. v1 has a single owner with full access, but the seam is built and tested.

---

## 7. Retrieval subsystem

Retrieval is treated as an **interchangeable technique inside the loop**, not a fixed step before generation. The seam (`sources/base.py` `PublicSource` Protocol; every call yields a `SearchResult` with a stable `(source_name, external_id)`) was there from v1.0; across v1.2–v1.3 it grew into a real, swappable stack (`retrieval/`).

### 7.1 Recall-first search
The search prompt is tuned for breadth — decompose the concept into facets and query each with varied wording — and the user's **original note phrases are injected** alongside the summarized concept so distinctive terms aren't lost. `retrieval/recall.py` is a `recall@K` instrument that measures how much of a known-relevant set the search recovers.

### 7.2 The reranker (`retrieval/rerank.py`, `embedding.py`)
Candidates are ordered by concept-relevance before the sources gate behind a `Reranker` Protocol.
- **BM25** is the default: dependency-free, deterministic, always available.
- **`GAR_RERANKER=embedding`** swaps in a **semantic reranker** that scores by cosine similarity over an external embeddings API (Voyage `voyage-3.5` by default; any OpenAI-style endpoint). **Why:** lexical signals reward vocabulary *overlap*, so they surface generic high-frequency-term papers and bury relevant work phrased differently — a bias measured on live runs (`plan.md` §5–§6). Embeddings score by *meaning* and correct it. On an embeddings-API error the reranker logs a warning and falls back to BM25 — a rerank failure never fails a run.
- **Cross-query provenance:** each candidate records which query angles surfaced it (`support` / `matched_queries`), carried to the gate as metadata (slice 1 found `support` rewards generic vocabulary, so it stays metadata, never a sort key).

### 7.3 Directions: clustering the pool into a map (`retrieval/directions.py`)
A flat list of hundreds of candidates pushes all sense-making onto the human. When the embedding reranker is active, GAR clusters the pool into semantic **"directions"** — reusing the embeddings already computed for reranking (memoized; no extra API cost):
- A small, dependency-free **k-means** over unit-normalized vectors with **deterministic maximin seeding** (reproducible). `K = clamp(round(n/40), 3, 7)` (`GAR_DIRECTIONS_K` overrides). Each cluster gets representative titles (centroid-nearest); the cluster the *concept* falls in is flagged "nearest your idea."
- **In the report (§4 positioning map):** compose names each direction and writes where the idea sits — core vs. adjacent vs. out of scope — still hedged.
- **At the sources gate:** candidates are presented **grouped by direction**, concept-nearest first, off-topic collapsed, with an "adopt top N" shortcut. This is server-side structure exposed to *both* the Web UI and (since v1.3.2) the MCP `get_run_status`, so the grouping is consistent rather than improvised per client.
- **Robustness:** only the top-N most relevant candidates are clustered (`GAR_DIRECTIONS_POOL`, default 200). Because the list is rerank-ordered this drops the off-topic tail — which, left in, lets the far-apart maximin seeding pick outliers as cluster centers and collapse everything on-topic into one mega-cluster. This was *observed live* (a degenerate `302/9/7/4/3/3`) and fixed by the cap (re-verified `90/48/29/21/12`, all on-topic).
- **Graceful absence:** in BM25 mode there are no embeddings, so no directions — the report omits the map and the gate shows a single relevance-ordered list. Nothing breaks.

---

## 8. Cost: per-phase model tiers

The agent does not use one model for everything. A `ModelPolicy` (`agent/loop.py`) assigns a model per phase:
- **Haiku** (`claude-haiku-4-5`) for **derive** and the **search loop** — the token-heaviest phase, where abstracts accumulate in context, so the biggest savings. Recall is protected by the breadth prompt, the embedding rerank, and the human's gate-2 selection.
- **Sonnet** (`claude-sonnet-4-6`) for **compose** — the human-facing deliverable, where citation discipline and hedged synthesis matter most (weak models mangle citations and trigger grounding retries).

Each call records its model in the audit trail. Overrides: `GAR_MODEL_DERIVE` / `GAR_MODEL_SEARCH` / `GAR_MODEL_COMPOSE`; `GAR_THOROUGH=1` escalates search to the compose tier for a high-stakes run. The `LLMClient` Protocol is untouched, so the Anthropic↔Bedrock swap is unaffected. (Measured live: 7 Haiku + 1 Sonnet calls per run vs. 8 Sonnet previously.)

---

## 9. Three clients, one governed loop

The *ideas* (private notes) source has two interchangeable implementations behind one duck-typed shape (`.name` / `.list_all()` / `.search()`); the client picks which by what it sends to `POST /runs` (`vault_path` vs `notes_content`). The agent loop, audit, HITL, grounding, and RBAC layers don't know which is mounted.

| Client | Ideas source | Backend sees | Report destination |
|---|---|---|---|
| **CLI** (`gar /path/to/vault`) | `IdeasSource` — walks the filesystem, honors `.gitignore` + `.ignore`, returns `file://` URLs | a vault path it can read | written to the vault; filename appended to `.ignore` |
| **Web UI** (React picker) | `InMemoryIdeasSource` — note contents POSTed from the browser | `(path, content)` pairs | the user saves from the UI; no backend filesystem access |
| **MCP server** (`gar-mcp`, stdio) | `InMemoryIdeasSource` — contents passed by the MCP client | `(path, content)` pairs | `get_report` returns the Markdown; the client saves it |

### The MCP server exposes the *gates*, not the *tools* (D-101)
`gar-mcp` lets an MCP client (Claude Code, Claude Desktop) drive a survey through **run management + the three HITL gates** (`start_survey`, `get_run_status`, `review_concept`, `select_sources`, `approve_report`, `get_report`, `list_runs`) — and **not** GAR's low-level retrieval. If it offered a raw `search_arxiv`, the client's own LLM could run retrieval-and-compose itself and return a "survey" that never passed grounding, never stopped at a gate, and left no audit trail — the central claim would break at the protocol boundary. Exposing the gates instead makes GAR a **governed sub-agent**.

Two seams keep it cheap and forward-compatible (D-102, D-103):
- **Thin client over the REST API.** The server doesn't import the backend; it calls the same HTTP API the Web UI uses (`GAR_API_URL`). After the AWS migration only that URL and an auth header change.
- **Role-gated tools.** `GAR_MCP_ROLE` (`public` by default) selects which tools appear; above-role tools are *absent from the schema*, mirroring the RBAC principle.

MCP runs are audited like any other (`X-GAR-Client: mcp`). Setup is a tracked `.mcp.json.example` (Claude Code) or `scripts/print-mcp-config.sh` (fills in the absolute path for Claude Desktop); the launch wrapper is `scripts/gar-mcp.sh`. Full detail in `docs/mcp.md`.

---

## 10. Frontend

Vite + React + TypeScript, ~1.4k LOC. Five views routed by `RunState.status` (`App.tsx`): **Start** (folder/file picker → uploads all notes as one survey), **ConceptReview** (gate 1, edit textarea), **SourceSelection** (gate 2, directions-grouped with progressive disclosure and "adopt top N"), **FinalReport** (gate 3, rendered/raw Markdown tabs, save dialog), **Completed** — plus a live **Activity** feed over SSE during long POSTs (`lib/sse.ts`, `api/stream.py`). Browser smoke passed end-to-end, including the directions-grouped gate. The build is type-checked and bundled in CI.

---

## 11. Reports

Compose (`phase_compose_report`) writes a structured Markdown report with required sections: derived concept, referenced notes, similar related work (each paper with connection + key difference), a hedged §4 positioning/assessment, development suggestions, and references split into adopted vs. not. Post-processing: `reports/linkify.py` turns `[source:id]` citations into Markdown links (and strips backticks an LLM may have wrapped a citation in, so links render). `reports/naming.py` gives date-based filenames with a suffix on same-day reruns, never overwriting, and appends the filename to `.ignore` so GAR never re-ingests its own output (idempotent re-runs, spec §8). Saving destination is client-specific (see §9).

---

## 12. Seven scale seams (for AWS, spec §10)

Conventions the code already follows so the migration is additive, not a rewrite:

1. **`tenant_id` everywhere** — every audit record, `RunState`, adopted source carries one (`"default"` in v1).
2. **The UI never calls AWS directly** — one hop to the backend.
3. **Agent state is externalised** — `RunState` is a frozen dataclass in a `RunStore`; in-process dict → DynamoDB is a one-class swap.
4. **HITL gates are durable state**, not in-flight awaits — the Step-Functions `wait-for-callback` pattern.
5. **The LLM client is a Protocol** — Anthropic ↔ Bedrock is a constructor swap.
6. **Audit carries `schema_version`** — schema evolution doesn't strand old logs.
7. **Authorization is a function at the API boundary** (`api/auth.py`) — returns True in v1; the call sites exist.

---

## 13. Scope, and future work

**v1 does:** read a Markdown file or folder (honoring `.gitignore`/`.ignore`); derive a concept; search arXiv iteratively; rerank (BM25 / opt-in embeddings) and cluster into directions; let the human edit the concept, adopt candidates, and approve the report; compose a grounded report; save it idempotently; stream activity.

**Future work, with seams left** (spec §11; `plan.md`): PDF/image ingestion on the private side; public-source PDF body extraction; additional public sources (PubMed, Semantic Scholar, Crossref); **web search** (a `sources/web_search.py` skeleton exists — Anthropic web-search tool, Text-Fragments grounding — not wired); multi-tenant runtime; **AWS deployment** (CDK stacks scaffolded, no resources); Bedrock-backed LLM; streaming token output; structure-aware candidate selection (MMR) and count-sizing; and **report-gate reject → recompose-with-feedback** (`plan.md` §9 — today there is no functional reject in either client; the MCP `reject` action is schema-only and, because it raises before any backend call, isn't even audited).

---

## 14. Testing and verification

- **360 backend tests**, mirroring the `src/` layout (agent, api, governance, ideas, mcp_server, reports, retrieval, sources, state). All **offline**: arXiv via `httpx.MockTransport`, the LLM via a stub returning pre-baked responses, embeddings via mock transport. A hermetic `conftest` fixture clears `GAR_*` env so a developer's `.env` can't leak live-API behavior into the suite.
- **Frontend**: type-check + Vite build in CI.
- **CI** (`.github/workflows/ci.yml`) runs backend pytest + ruff (lint/format) and frontend ESLint/Prettier/build on every push and PR.
- **Live verification**: end-to-end smoke runs against live arXiv + Anthropic (+ Voyage) produced complete, cited reports over both the Web UI and the MCP path; the grounding-retry path fired and recovered in production; the clustering degeneration and its fix were both observed live.

---

## 15. Version history (v1.0 → v1.3.2)

| Tag | Theme |
|---|---|
| **v1.0.0** | First public release — agent loop, four governance pillars, CLI + Web UI, arXiv source, AWS CDK scaffold. |
| **v1.0.1** | Python 3.13 pin (matches Lambda `python3.13`); README mascot. |
| **v1.1.0** | **MCP server** — GAR as a governed sub-agent over stdio (gates not tools; thin REST client; role-gated; audited as a third client). |
| **v1.2.0** | **Search recall** — breadth prompt + original-note injection + **BM25 rerank** + `recall@K`; MCP gate timeout→polling recovery. |
| **v1.3.0** | **Retrieval structure** — cross-query support (slice 1) + **embedding semantic rerank** (slice 2, Voyage, opt-in). |
| **v1.3.1** | **Directions positioning map** (slice 3) + **per-phase model tiers** (cost) + **readable concept** (lead + bullets). |
| **v1.3.2** | **Gate-2 directions grouping** + **clustering robustness** (top-N pool cap) + citation linkify fix + frontend UX (Activity tail/local-time, sans-serif, final-report cancel). |
| *(post-tag)* | MCP `get_run_status` exposes directions; MCP config helper; docs brought current + `plan.md` translated to English. |

---

## 16. Repository map

```
backend/src/gar_backend/
├── main.py            FastAPI app + Mangum hook for Lambda
├── settings.py        env-driven settings (skeleton; pydantic-settings planned)
├── cli.py             terminal client (local-mode, vault write-back)
├── api/               runs.py · gates.py · stream.py (SSE) · deps.py · auth.py
├── agent/             loop.py (orchestrator + 3 phases) · prompts.py · tools.py · llm.py
├── governance/        audit.py · hitl.py · grounding.py · rbac.py   (one pillar each)
├── retrieval/         rerank.py (BM25) · embedding.py (semantic) · directions.py (k-means) · recall.py
├── sources/           base.py (PublicSource) · arxiv.py · web_search.py (skeleton)
├── ideas/             walker.py (.ignore-aware) · reader.py (Markdown; PDF stubbed) · search.py
├── reports/           naming.py · builder.py · linkify.py
├── state/             runs.py (RunStore + InMemoryRunStore) · checkpoints.py (durable)
└── mcp_server/        server.py (gar-mcp) · tools.py · client.py · models.py

frontend/src/          App.tsx (router) · lib/{api,sse}.ts · views/ (Start/ConceptReview/
                       SourceSelection/FinalReport/Completed/Activity)
infra/                 AWS CDK (Python) — 5 stacks, scaffolded, no resources yet
scripts/               run-backend.sh · gar-mcp.sh · print-mcp-config.sh
docs/mcp.md            MCP surface + client setup
spec.md                working contract (Japanese)        plan.md   expansion plan + decision log
CLAUDE.md              guidance for Claude Code sessions
```

---

## 17. Index of design decisions

The full decision log lives in `plan.md`. For reference:

- **D-101** MCP exposes HITL gates, not low-level tools (and the `search_arxiv` exclusion).
- **D-102** MCP server is a thin client of the REST API (forward-compatible with AWS).
- **D-103** Private tools structurally absent from the MCP schema by role.
- **D-104** v1.1 gate calls are synchronous; schemas are polling-ready from the start.
- **D-105** MCP note input uses the content-upload path (no backend filesystem).
- **D-106** MCP operations are audited (the `client` field).
- **D-107** Official MCP SDK (FastMCP); models shared with the API schema.
- **D-108** `get_run_status` returns structured candidates (abstracts on, generous cap).
- **Tracks:** §5 search-recall · §6 retrieval-structure (slices 1–3 + gate-2 grouping + cap) · §7 cost tiers · §8 concept readability · §9 report-gate reject/recompose (future).

---

*Prepared as a consolidated v1.x design-and-implementation reference. The code, `spec.md`, and `plan.md` remain the authoritative sources; this report synthesizes them.*
