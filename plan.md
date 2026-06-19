# plan.md — Plan for the MCP server implementation and the AWS migration

> This document is the expansion plan for everything after v1.0.0. Implementation
> sessions in Claude Code treat this file as canonical. If a conflict with
> README / spec.md arises, update this file first, then implement.

---

## 0. Goals and priorities

1. **Phase 1 (done): local MCP server** — make GAR operable from Claude Code /
   Claude Desktop. stdio transport. **Shipped as `v1.1.0`.**
2. **Search-recall track (done): `v1.2.0`** — breadth search + verbatim-phrase
   injection + BM25 rerank + recall@K instrumentation, plus timeout-polling
   recovery for the MCP gates (§5 / PR #2,#3). v1.2.0 was originally assigned to
   AWS, but AWS was pushed back to v2.x, and this intermediate feature addition
   was made v1.2.0.
3. **Phase 2: AWS migration** — replace the scale seams already built into v1
   with real resources. **`v2.0.0` (v2.x) tag.**
4. **Phase 3 (out of scope, recorded only): remote MCP** — streamable HTTP MCP
   on AWS + OAuth. Not implemented in this plan.

Design Phase 1 and Phase 2 to be orthogonal (D-102, below). Order:
Phase 1 done → README update → start Phase 2. No parallel work.

---

## 1. Decisions

### D-101: MCP exposes the HITL gates, not the internal tools

**Decision**: Cut the MCP tool surface as run management and the three HITL
gates, not as GAR's internal mechanisms (low-level tools such as `search_arxiv`).
From the MCP client's point of view, GAR is a "governed sub-agent."

**Rationale**: If we exposed the low-level tools, the LLM on the MCP client side
could substitute for the agent loop, and a survey that bypasses grounding
validation, the HITL gates, and the audit log would become possible. The central
claim of this project — "every step is audited, and a human gates it" — would
break at the MCP boundary. If the gates are the public boundary, the governance
layer stays invariant even across the protocol boundary.

**Exception (original proposal)**: `search_arxiv` deals only with public
information, so exposing it standalone is acceptable (treated as an auxiliary
tool). The private side (ideas search) follows D-103.

**Revision (2026-06-14, during the v1.1 implementation)**: `search_arxiv` is
**not implemented** in v1.1. Two reasons. (1) This auxiliary tool is the
exposure of low-level search, and it slightly dilutes the claim in the body of
D-101 that "the MCP surface is cut as governed gates." The seven gate / run-
management tools complete a survey by themselves, and auxiliary search meets the
functional requirements even without it. (2) Adding a standalone search endpoint
(`GET /sources/arxiv/search`) makes `arxiv` appear in a route path, which is in
tension with the generic-source principle (CLAUDE.md / spec §4) that "`arxiv`
appears only in `sources/arxiv.py`, its tests, and one line of `deps.py`." If we
later add public search to MCP, we put it in as a generic
`GET /sources/public/search` + a generically named tool (Future Work).
Therefore the public tools in v1.1 number **7** (0 auxiliary).

### D-102: Implement the MCP server as a thin client of the existing HTTP API (plan B)

**Decision**: The MCP server does not import `gar_backend` directly; it calls the
existing REST API (`POST /runs`, `POST /runs/{id}/gates/*`, `GET /runs/{id}`,
etc.) via `httpx`. The base URL is the environment variable `GAR_API_URL`
(default `http://localhost:8000`). The auth header is `GAR_API_KEY` (not added if
unset; unnecessary because the v1.1 local backend is pass-through).

**Rationale**: Apply seam #2 ("the UI does not call AWS directly; the data plane
is one hop from wherever the backend lives") to MCP too. As a result, even after
the AWS migration (Phase 2), the same MCP server runs with **only the base URL
and auth header swapped**, and the MCP implementation and the AWS migration stay
orthogonal. "Two clients" becomes "Three clients," extending the existing
arrangement of sharing the agent loop and the governance layer.

**Rejected alternative**: in-process (import and drive it, as the CLI does). Easy
locally, but unusable after the AWS migration, and it forks the client
implementation.

### D-103: Private tools are structurally absent from the MCP schema by default

**Decision**: The MCP server takes a role at startup (env `GAR_MCP_ROLE`, default
`public`) and reproduces, at the MCP boundary, the same registry-separation
principle as the existing `governance/rbac.py`. In the `public` role, tools that
touch ideas (private) **do not appear in the schema rather than being refused**.
Only when the `owner` role is explicitly set are all tools exposed.

**Rationale**: Show, in implementation and tests, that the central claim of
rbac.py ("private tools are not refused at call time; they are structurally
absent from the schema") holds at another tool-exposure boundary, namely MCP.

**Note**: `start_survey` takes the content of the notes themselves as an argument
(D-105), which is a separate matter from exposing the ideas-search tool. The MCP
client passing note content is the user's own operation, outside the jurisdiction
of RBAC.

### D-104: v1.1 tool calls are synchronous; the schema is cut up front assuming polling

**Decision**: In v1.1 we accept synchronous calls, the same as the existing API
(the agent phase runs to completion inside the tool call; in practice this is no
problem locally). However, the tool schema returns `run_id` and is defined from
the start to be polled via `get_run_status`, so that even when the API changes to
202 + polling in Phase 2, **the shape of the MCP tools does not change**.

**Rationale**: At AWS migration time, going asynchronous becomes necessary
because of Lambda's execution-time constraints. If the schema is already in an
async-ready shape, the migration-time change is confined to the inside of the
server implementation.

### D-105: Note input via MCP uses the content-upload path

**Decision**: The MCP `start_survey` takes `notes` (an array of
`{path, content}`) and rides the API's `notes_content` path
(`InMemoryIdeasSource`). The `vault_path` path is not exposed in MCP.

**Rationale**: From Phase 2 on, there is no longer a guarantee that the MCP server
and the backend are on the same machine. vault_path presumes the backend's
filesystem and is meaningless against a remote backend. With the content-upload
path, behavior is identical against either a local or AWS backend (same reason as
the Web UI). Reading files from a local vault is the responsibility of the MCP
client side (Claude Code can read files). Saving the report is likewise: `get_report`
returns the body, and writing it back is the client's responsibility (same as the
Web UI).

### D-106: Audit MCP operations too

**Decision**: Record an identifier of the originating client in the backend's
audit log. Implementation: accept the API request header `X-GAR-Client` (value:
`web` / `cli` / `mcp`) and add a `client` field to the audit record. Raise
`schema_version` to `1.1` (backward compatible: field addition only).

**Rationale**: To uphold the claim that "what happened during a run can be learned
by reading audit.jsonl; there is no shadow path," include the new client surface
in the audit scope.

### D-107: SDK and package layout

**Decision**: Use FastMCP from the official Python SDK (the `mcp` package).
Placement is `backend/src/gar_backend/mcp_server/` (module name `mcp_server` to
avoid a clash with the `mcp` package). The entry point is
`uv run --package gar-backend gar-mcp` (added to pyproject's scripts).

**Rationale**: The official SDK is safest for compatibility with Claude Code /
Claude Desktop. Placing it inside the same package lets us share the Pydantic
models (tool I/O) with the API schema and prevents drift.

### D-108: `get_run_status` returns candidates as a structured list, with abstracts, and generously

**Decision (2026-06-14, added after the v1.1 smoke)**: Change the candidate
presentation of `get_run_status` from a prose-string summary (first 20 items) to
a **structured list** `candidates: [{id, title, abstract?, authors, published,
url}]` + `candidate_count` (total). The count cap is the argument `max_candidates`
(default 100, default changeable via env `GAR_MCP_MAX_CANDIDATES`). Abstracts are
controlled by the argument `include_abstracts` (**default on**), so callers that
worry about tokens can opt out. `activity_summary` reverts to a short headline.

**Rationale**: Gate 2 (adoption selection) is the proper human decision point. In
the smoke, against 89 hits only 20 were visible, and those were title-only, so
the client side's (Claude Code's) relevance grouping stalled at "guessing from
titles." The abstract is already in state as SearchResult.snippet at arXiv-fetch
time and is existing data used in the agent's relevance evaluation, so passing it
through the MCP surface costs only tokens, with no additional API call. To make
the most of "the client summarizes and organizes" (the D-101 division of labor),
passing the material for organizing — the abstracts — by default is reasonable.
A count of 100 is about 3k with id+title, and about 20–25k tokens with abstracts.
If it gets too heavy, split it out into `list_candidates(limit, offset,
include_abstracts)` in the future (for now, avoid over-engineering; extending
get_run_status suffices).

**Limit (stated explicitly)**: Raising the cap is symptomatic treatment. If hits
exceed the cap, things still spill, and arXiv's return order is not necessarily
relevance order. The essential mitigation is rerank (a retrieval technique, a
later phase).

---

## 2. Phase 1 — MCP server (stdio)

### 2.1 Public tools (7. The auxiliary `search_arxiv` is excluded from v1.1 by the D-101 revision)

All I/O is defined with Pydantic models and shared with the API schema.

| Tool | Input | Output | Corresponding API |
|---|---|---|---|
| `start_survey` | `notes: list[{path, content}]` | `run_id, status` | `POST /runs` (notes_content) |
| `list_runs` | none | `runs: list[{run_id, status, updated_at}]` | `GET /runs` |
| `get_run_status` | `run_id` | `status, current_gate?, activity_summary` | `GET /runs/{id}` |
| `review_concept` | `run_id, action: approve\|edit, edited_concept?` | `status` | `POST /runs/{id}/gates/concept` |
| `select_sources` | `run_id, adopted_ids: list[str]` | `status` | `POST /runs/{id}/gates/sources` |
| `approve_report` | `run_id, action: approve\|reject, feedback?` | `status` | `POST /runs/{id}/gates/report` |
| `get_report` | `run_id` | `markdown, citations_valid, warnings` | `GET /runs/{id}` (report portion) |

Notes:
- For the time field of `list_runs`, what the backend's `serialize_state` holds
  is `updated_at` (`created_at` is not retained), so return `updated_at`.
- `approve_report`'s `action: reject` has no rejection transition in the backend,
  so in v1.1 calling it returns a "not supported" error (the schema shape is
  retained because of D-104).
- `get_report`'s `citations_valid` / `warnings` are supplied by a small change
  (hitl.py + loop.py) that puts the grounding-validation summary
  (`report_validation`) onto the report gate's `pending_payload`. For runs with no
  adopted evidence, `citations_valid = null` (nothing to validate). The same
  fields are available to the web UI in the future.
- `get_run_status`'s `current_gate` should contain enough information for the MCP
  client (Claude) to decide "what to confirm with the human next" (e.g., when
  gate=sources, a summary of the candidate list).
- The tool description must state "at a gate, always obtain human confirmation
  before calling." Because the last mile of governance depends on the MCP client's
  behavior, treat the description as the place that carries that instruction.
- If there is currently no bare endpoint in the API for `search_arxiv`, add
  `GET /sources/arxiv/search` (rate control passes through the existing arxiv.py).

### 2.2 Implementation tasks

1. New `mcp_server/` package: FastMCP server, tool definitions, `GarApiClient`
   (httpx wrapper, `GAR_API_URL` / `GAR_API_KEY` / `X-GAR-Client: mcp` header).
2. Role implementation of D-103: `GAR_MCP_ROLE=public|owner`. In v1.1 the ideas
   tools do not exist, so it is effectively a no-op, but route tool registration
   through the registry and write a test that the schema changes with the role (a
   receptacle for when ideas search is later added to MCP).
3. Audit extension of D-106: the `client` field, `schema_version: "1.1"`, and a
   backward-compatibility test against existing logs.
4. Add `gar-mcp` to pyproject scripts.
5. Document `.mcp.json` (repo root, for Claude Code) and a configuration example
   for Claude Desktop in `docs/mcp.md` or the README.
6. Smoke test: one full run from Claude Desktop (start → concept → sources →
   report → get_report). Paste a fragment of the audit.jsonl from that run into
   the README (the existing "show the real log" practice).

### 2.3 Testing approach

- Follow the existing practice: offline, mock the backend API with
  `httpx.MockTransport`. No real API key needed.
- Required cases:
  - each tool → conversion to the correct endpoint / payload
  - the difference in tool schema by `GAR_MCP_ROLE` (verification of structural
    absence)
  - error messages when the backend is not running / on 4xx / 5xx (worded so the
    MCP client's LLM can read it and decide the next action)
  - the audit `client` field and schema_version 1.1
- State anomalies in gate transitions (e.g., select_sources with concept not
  approved) are the existing responsibility of the backend side. The MCP side
  tests that it passes the error through verbatim.

### 2.4 Documentation / release

- README: expand the table from "Two clients" → "Three clients" (CLI / Web UI /
  MCP). New MCP section (the rationale of D-101 / D-102, one paragraph each).
- Add an MCP chapter to spec.md.
- `v1.1.0` tag.

---

## 3. Phase 2 — AWS migration

Approach: realize the existing seven scale seams from the top down.
**Do not change the API's outward shape (paths and schema).** What changes is only
the synchronicity of the response (3.2 below). The completion condition for the
migration is that the frontend and the MCP server run with a base-URL swap.

### 3.1 Tasks (in priority order)

1. **Externalize state**: `DynamoDbRunStore` (add a RunStore Protocol
   implementation, a 1-class swap). Move report storage to S3 (confirm the
   storage-destination abstraction of `reports/`, and cut a `ReportStore` if
   needed).
2. **Lambda-ize**: using the existing Mangum hook, put the current API on API
   Gateway + Lambda.
3. **Go asynchronous**: change POSTs that involve an agent phase (/runs, each
   gate) to 202 Accepted + `get_run_status` polling. For phase execution, first
   take the minimal form of "the accepting Lambda self-invokes asynchronously,"
   and leave the evolution to Step Functions wait-for-callback shelved as future
   work. ※ The shape of the MCP tools is unchanged by D-104. The Web UI needs a
   fix to support polling (SSE need not be replaced with a CloudWatch base in v2;
   it may be simplified to polling — decide at implementation time).
4. **Audit log**: write to an S3 object (JSONL) per run. Local stays a file as
   before. Cut an `AuditSink` abstraction with 2 implementations.
5. **BedrockLLM**: add an `LLMClient` Protocol implementation (realization of seam
   #5). Provider selection via env. Decide whether to use a cross-region inference
   profile at implementation time, after checking the regional situation.
6. **Auth**: replace the pass-through in `api/auth.py` with API-key verification
   (an API Gateway API key or a custom header). **Cognito is not done in v2.**
7. **CDK**: define real resources on the scaffolded stacks (DynamoDB / S3 / Lambda
   / API Gateway / IAM). Keep `cdk synth` passing in CI.
8. **Spell out the handling of notes**: the AWS backend supports only the content-
   upload path. State in the README that vault_path is local-backend only (because
   the whereabouts of unpublished notes is the core of this project's privacy
   design, write one paragraph on it as a design decision).

### 3.2 What is not done (out of v2 scope)

- Cognito / OAuth, materializing multi-tenancy (keep the seams)
- Remote MCP (streamable HTTP)
- LLM token streaming
- PDF ingestion, additional public sources

### 3.3 Release

- README: update the Architecture diagram to the v2 configuration (the 2 ways:
  local / AWS). Update the "AWS infra: scaffolded" wording to match reality.
- `v2.0.0` tag.

### 3.4 v2.0 status — shipped 2026-06-19

Deployed to account `733287383921`, region `ap-northeast-1`, and verified live
end-to-end (survey driven through the MCP client + SigV4 HTTP). Slices, each
deployed and checked before the next:

1. `DynamoDbRunStore` (run_id PK + `tenant-index` GSI) + **S3 candidate-pool
   offload** (400 KB item-limit dodge) — done.
2. **Lambda** via Mangum behind a **Function URL** (not API Gateway — Function
   URL is simpler and sufficient for one app), arm64, Docker-bundled — done.
3. **Async self-invoke worker** (`InvocationType=Event`); endpoints schedule +
   return a snapshot; clients poll. Keyed on `AWS_LAMBDA_FUNCTION_NAME` to
   avoid a CDK self-reference cycle. Web UI switched SSE → polling — done.
   *(Bug found + fixed live: the worker's `asyncio.run` poisoned Mangum's
   reused event loop → 502; isolated it on its own thread.)*
4. **S3 audit sink** — one immutable object per record (a single per-run object
   would be overwritten across the multi-invocation HITL split) — done.
5. **Bedrock seam** — `BedrockLLM` stub + `GAR_LLM_PROVIDER` selector — done.
6. **Auth** — real `X-GAR-API-Key` gate; Function URL flipped to `NONE` + CORS;
   app key + Anthropic key both in **Secrets Manager** — done.
7. **CDK** — `DataStack` + `BackendStack` define real resources; `cdk synth`
   green — done. (FrontendStack/AuthStack still scaffolds — see D-205.)
8. Notes: AWS backend is content-upload only (vault_path is local-only). — done.

### D-205: defer public browser hosting to v2.1 (with Cognito)

The frontend was made cloud-capable (polling + `X-GAR-API-Key` + base-URL
config), but **hosting it publicly is deferred to v2.1**, paired with Cognito.

**Why:** a hosted SPA against the public Function URL would need its auth in
the browser. The only v2.0-shippable options are a shared key baked into the JS
bundle (visible to anyone → effectively public) or a CloudFront proxy injecting
the key as an origin header (key stays server-side, but the key becomes an
operator-supplied deploy input and the injection layer is replaced once Cognito
lands). Both are throwaway or weak relative to the real answer — a per-user
token in `Authorization` (the header slice 4 deliberately kept free). The cloud
is already provably governed through the **MCP path**, so a public browser adds
hosting, not architecture. FrontendStack (S3 + CloudFront) therefore pairs
naturally with Cognito in v2.1, where the browser gets auth it deserves.

---

## 4. Notes on how to proceed (for Claude Code sessions)

- Work happens on a feature branch on the local copy (`feature/mcp-server`,
  `feature/aws-backend`). No direct push to main.
- 1 PR = 1 concern. Split Phase 1 into roughly 3 PRs: (a) the audit extension, (b)
  the mcp_server itself, (c) docs.
- New code also follows the existing structural conventions (governance is one
  concern per file, swap points via Protocol, pure functions over frozen
  dataclasses).
- Add tests in the mirror structure of `backend/tests/`. Keep all tests offline.
- If an implementation that contradicts the Decisions in this file becomes
  necessary, update this file first and write one paragraph of rationale before
  implementing.

---

## 5. Search-recall improvement track (independent of Phase 2)

Discovered in the MCP smoke: arXiv search misses (relevant core literature gets
buried toward the back of the candidate order / does not ride the search terms at
all), which directly affects GAR's true purpose (preliminary investigation of
novelty / inventive step). **In light of the purpose, recall dominates over
precision** — missing prior work (FN) is the fatal error that produces "false
novelty," while extra candidates (FP) can be filtered by the human / client
(abstracts already presented per D-108). The metric is **recall@K** (whether the
top K items a human reads catch the decisive prior work) + **citation precision =
1.0** (grounding). F1 (equal weight) does not fit the purpose, so it is not used.

Levers (large impact → small):

- **B. breadth search (implemented, feature/recall)**: rewrite `SEARCH_SYSTEM` to
  favor recall (facet decomposition, synonyms / alternate spellings, parallel
  queries, no over-pruning; drop "stop at 5–20 items"). `max_search_iterations`
  4→6, search tool `max_results` default 10→15.
- **A. verbatim-phrase injection (implemented, feature/recall)**: inject the
  original note verbatim (cap 8000 chars) into the search phase, so technical
  phrases dropped in summarization are used in the literature query (implements the
  unrealized part of spec §5). Privacy: the instruction not to flow the raw private
  draft into web search is kept (only distilled technical terms go to literature
  sources such as arXiv).
- **D. rerank (implemented, feature/recall)**: a `Reranker` Protocol (the swap point
  of spec §5) + a dependency-free `BM25Reranker` in `retrieval/rerank.py`. In
  `phase_search`, after dedup and before the source gate, reorder by the concept →
  the MCP cap is cut after rerank (only the low-relevance tail is dropped). Stable
  sort, a no-op when there is no signal. embedding / LLM rerank can be swapped in
  later under the same Protocol.
- **Instrumentation (implemented, feature/recall)**: `recall_at_k` / `rank_of` /
  `known_item_recall` (pure functions) in `retrieval/recall.py`. Offline tests
  demonstrate "plant decisive prior work at the tail of the pool → rerank lifts it
  into the top K (recall@5: 0.0→1.0)." A live evaluation harness against real arXiv
  (seed concept + hand labels) is future work.

### Field validation (v1.1 smoke, 2026-06-15)

Compared before/after applying B+A on the same note: candidates **94→294**
(3.1×), arXiv searches **12→23**, and verbatim injection also fired the
private_ideas search. Re-found 5 of the 6 core items adopted last time, plus many
new pieces of literature closer to the idea (One Chatbot Per Person, etc.) surfaced.
Findings: (a) breadth-ification is not a strict superset (in/out churn from query-
vocabulary variation) → motivation to control/measure with rerank + the recall@K
instrument. (b) recall-max search is heavy, and a synchronous gate POST hit a
connection timeout → the run is durable, completes, and recovers by polling
(demonstration of D-104, and motivation for Phase 2 going asynchronous).

---

## 6. retrieval-structure track (v1.3, search-phase redesign)

Convert "narrowing down" from a flat top-K by relevance to **a structure of the
query set (core + frontier)**. While staying within what the user can handle,
present a map of the foundational idea and the directions of extension, and support
the judgment of novelty / inventive step.

### Settled design decisions

- **Core = contiguous support only**. Hold per-doc `support` (the number of query
  angles in which it appeared), and draw the core/frontier line **dynamically on
  the client at presentation time** (do not bin it in the data). Do not take a
  strict intersection (with diverse variants the core becomes empty/minimal).
- **Variant generation = agent, calibration = deterministic**. The agent emits
  N≈6–10 variants over the facet axis × terminology axis, and the system calibrates
  by count. The terminology axis loosens the BM25 lexical limit at the query stage.
- **≤100 = complete set**. Calibrate each query down to `totalResults≤100` to
  obtain a **complete set**. Recall is secured by "narrow complete sets × many ×
  union." Default 100, variable.
- **Boundaries**: agent (variant generation) → deterministic (sizing / provenance /
  support / two-stage abstracts / in-bucket rerank) → agent (interpret core +
  frontier and write the report positioning).
- **Default sort = BM25 (plan B)**. support is **metadata**, not rank. The cap
  drops the low-relevance tail and preserves the recall@K behavior. A structure-
  aware cap selection that leverages support is a follow-up slice.

### Slices (validation first)

- **Slice 1 (implemented, integrated)**: provenance → support → visualized at
  gate 2. Change `phase_search` from flat extend+dedup to **holding doc→the set of
  appearing queries**, and attach `support` / `matched_queries` to each candidate.
  The default sort stays BM25 (support is exposure only). Expose
  support/matched_queries in MCP's `Candidate` / `get_run_status`.

### Slice 1 live validation (2026-06-16) — the "common = foundational" hypothesis is rejected

Measured on the same note. 275 candidates, 23 angles. Result: **high support
picked up not the "foundational core" but "generic multi-agent papers"** (s=11
"Survey of Multi-Agent Deep RL", s≥7 is almost entirely generic MARL). Meanwhile
**the paper that is dead-on for the idea has support=1** ("One Chatbot Per Person",
"Gossip-Enhanced Substrate for Agentic AI", etc.). Cause: because the 23 angles
share the stems "multi-agent / agent / decentralized," in lexical search **generic
papers match the vocabulary across almost all angles → high support**. What support
measures is "vocabulary genericness," not "foundationalness" (a reprise of the
lexical bias seen with BM25).

Conclusions:
- **Plan B (BM25 sort, support as metadata) is correct**. Had it been plan A,
  generic papers would have occupied the top and the dead-on idea paper would have
  been buried. Live backs this up.
- **"common = foundational" does not hold under lexical**. The value is rather on
  the **frontier side (support=1, per-angle = direction of extension)**. support is
  useful not for "core identification" but as metadata for "**generic detection**
  (push high support down)" and "frontier extraction."
- **Both BM25 rerank and lexical support have the same generic bias** → next, a
  **semantic (embedding) signal** is the real bet (the double limit of lexical lined
  up in measurement).

### Revised slices (turn toward an embedding approach)

- **Slice 2 (the real bet, semantic, implemented)**: add an **embedding
  implementation** of the `Reranker` Protocol and measure relevance by **semantic
  closeness** (cosine). It resolves the lexical (BM25/support) vocabulary-generic
  bias. **The method is decided as an external embedding API** (Anthropic has no
  native embeddings → Voyage recommended): lightest dependency (httpx only, zero new
  deps), Lambda-clean, privacy is marginal on the premise that the concept is
  already in the LLM, and it is **globally opt-in**.
  - `retrieval/embedding.py`: `EmbeddingClient` (OpenAI-compatible
    `data[].embedding`, query/document input_type, batch, `EmbeddingError` on
    error) + `EmbeddingReranker` (cosine order, **fall back to BM25 on API failure**
    = do not drop the run).
  - `make_reranker()` (env selection): default `bm25`. Enabled with
    `GAR_RERANKER=embedding` + `GAR_EMBED_API_KEY`/`VOYAGE_API_KEY` (optionally
    `GAR_EMBED_MODEL`/`GAR_EMBED_URL`). If no key is set, it degrades to BM25 (the
    wiring is valid regardless of key presence). Make this factory the default for
    `AgentContext.reranker`. BM25 stays the zero-dependency default.
  - **The fallback logs a warning** (to prevent silent degradation). embedding
    failure → BM25 is a logging.warning.
  - **Live-validated (2026-06-16, Voyage voyage-3.5)**: re-ranked slice-1's 275-
    candidate pool. The embedding TOP is consistently "personalized LLM agents /
    profiles," and it **removed the off-target items that BM25 had raised by
    vocabulary match (game theory, UCB bandits, claim verification)** — measured
    correction of the lexical generic bias. Nuance: being single-vector, it
    prioritizes the semantic centroid (personal profiles / agents), and facets off
    the centroid (pure gossip infrastructure) drop → **embedding (relevance) and
    support (facet coverage) are complementary**. The free tier has a 3RPM/10K TPM
    limit, so a batch hits 429→fallback; running everything needs a paid tier
    (confirmed).
- **Slice 3 (positioning map, implemented)**: turn the report's **positioning
  section** into a "direction map." The original count sizing (totalResults probe)
  is **skipped** — Slice 1 found that "lexical set-algebra (support) = generic," so
  the payoff of a complete set is small, and core identification moved to embedding.
  The directions are built with **A+B** (user's choice):
  - **B (quantitative, deterministic)**: pure-Python k-means in
    `retrieval/directions.py` (normalization + deterministic maximin initialization
    + small-cluster exclusion) **semantically clusters the embeddings**. Add a
    memoization cache to `EmbeddingClient` and **reuse the vectors that rerank
    embedded (no double API billing)**. K=clamp(round(n/40),3,7), env
    `GAR_DIRECTIONS_K`. `EmbeddingReranker.analyze_directions`.
  - **A (natural language)**: `phase_search` puts the directions (representative
    title + concept-nearest) into the context, and in §4 of
    `phase_compose_report`/`COMPOSE_REPORT_SYSTEM` **the LLM names each direction and
    describes "core / direction of extension / position of the idea" in a hedged
    manner**. BM25 mode has no directions (graceful).
  - **Live validation (2026-06-16, 275 pool)**: the 4 directions "personal profiles
    / agents (concept-nearest) / federated / distributed learning / distributed
    socioeconomics / multi-agent communication" — the facets of the idea itself.
    Finding: maximin initialization picks **an outlier (a physics paper) as a
    standalone cluster** → solved by excluding it with `min_cluster_size`.
  - **Hermeticizing the test environment**: the problem of `.env`'s
    `GAR_RERANKER=embedding` leaking into pytest via main.py's load_dotenv is blocked
    with an autouse fixture in conftest (tests default to BM25 and do not hit the live
    API).
  - **Exposing directions at gate2 (implemented, 2026-06-17)**: the browser smoke
    found that "a flat list of 285 items has high cognitive load." `phase_search`
    attaches `direction` (the cluster id it belongs to) to each candidate and a stable
    `id` to each direction, and puts them on the gate2 payload. The web UI shows
    concept-nearest first, **grouped by direction** (representative title as the label,
    off-topic collapsed by default, within a group top-N in relevance order + "show
    more," "Adopt top N" on nearest). The grouping that the MCP client was improvising
    can be **unified onto server-side deterministic directions** (but MCP schema
    exposure is not implemented — see below).
  - **Hardening the clustering (implemented, 2026-06-17)**: observed the degeneration
    **302/9/7/4/3/3** in live (1 mega-cluster + small clusters of physics noise). The
    cause is that maximin initialization **seeds the farthest point = an off-topic
    outlier**. Fix: **cluster only the top N by relevance**
    (`DEFAULT_CLUSTER_POOL=200`, env `GAR_DIRECTIONS_POOL`). Since candidates are in
    rerank order, the off-topic tail can be excluded and the seeds are limited to on-
    topic. Live re-validation improved it to **90/48/29/21/12** (all on-topic; the
    168-item tail goes to "Other").
  - **Exposing directions to MCP `get_run_status` (implemented, 2026-06-18)**: added
    `direction` to MCP's `Candidate` and a `directions` summary (id / representatives /
    size / contains_concept) to `RunStatusResult`, surfaced at the sources gate. The
    MCP client now consumes the same server-side grouping the web UI uses instead of
    improvising its own clusters.
  - **Not implemented (future)**: count sizing, structure-aware cap selection (MMR),
    a relevance-threshold-based variable pool (an alternative to a fixed N).

---

## 7. Cost track — per-phase model tier (2026-06-16)

**Background**: iterative live validation has run up Claude API costs. The policy
is to make Haiku the main and use **Sonnet only when it counts** (user
instruction).

**Decision**: replace the single `AgentContext.model` with `ModelPolicy` (3 fields:
derive / search / compose) and assign a model per phase.

- **derive=Haiku 4.5**: note summarization. Short and low-risk → a cheap model
  suffices.
- **search=Haiku 4.5**: heaviest in tokens (abstracts accumulate in context) =
  largest savings. Recall is partly secured by the breadth prompt, embedding rerank,
  and the human's gate2 selection.
- **compose=Sonnet 4.6**: the report a human reads (citation discipline, hedged
  synthesis, positioning map). A weaker model breaks citations and induces grounding
  retries → spend the money here.

**Implementation**: `make_model_policy()` resolves from env. `GAR_MODEL_DERIVE` /
`GAR_MODEL_SEARCH` / `GAR_MODEL_COMPOSE` can override each phase. `GAR_THOROUGH=1`
promotes the recall-focused search **to the compose tier (Sonnet)** (an explicit
`GAR_MODEL_SEARCH` takes priority). `_audited_complete` receives `model` as an
explicit argument and records the model used in the audit record (which phase ran at
which tier remains in the trace). The LLM abstraction (Anthropic↔Bedrock swap, spec
§10 seam) is kept.

**Not implemented (future)**: dynamic escalation (promote compose only on a
grounding retry), audit aggregation of cost measurement.

---

## 8. Readability of the derived concept (2026-06-16)

**Background**: the derived concept a human reads at gate1 was one dense prose
block, and the facets were hard to read. We want to improve readability, but the
concept is also **the input to retrieval** (the prompt for `phase_search` + the
query for `reranker.rank(concept, …)`), so a precision drop was a concern.

**Decision**: change `DERIVE_CONCEPT_SYSTEM` to emit "a short lead sentence (1–2
sentences) + facet bullets." Reframed the **concern as missing content, not
format**: embedding rerank turns the whole concept into 1 vector, BM25 is bag-of-
words — both depend on **vocabulary** and are layout-independent. Because bulleting
could drop proper technical terms (sub-profile, confidence threshold, etc.) and
lower the discriminating power of the rerank query, the prompt explicitly instructs
to **preserve proper terms verbatim**. In addition, verbatim-phrase injection (§5)
backs retrieval through a separate path, so the dependence on the concept prose is
partial to begin with.

**Live validation (2026-06-16, same agent.md)**: output a lead sentence + 8 facet
bullets, and confirmed proper-term preservation. Recall is 281 items (the prose
version was 313 items) — within the range of the agent search's query jitter, no
significant drop, and the TOP is PersonaX (the core user-modeling), with relevance
also maintained.

---

## 9. Report-gate rejection / recompose-with-feedback (future enhancement)

**Status: not implemented (idea, recorded 2026-06-18).** v1.1 has **no functional
"reject"** at the report gate in either client:

- The browser `FinalReport` view exposes only "Approve & save."
- MCP `approve_report` accepts `action="reject"` in the schema, but raises a "not
  supported in v1.1" error in the MCP tool layer (`mcp_server/tools.py`) — *before*
  any backend call. The schema shape is kept only for forward compatibility.
- To not accept a report today, the only move is to **abandon the run**. (Concept
  *editing* is the one real non-approve action — `review_concept(action="edit")` and
  the browser edit box; the sources gate also lets you adopt zero.)

**Audit note.** Because the MCP reject raises before `client.gate_report()` and the
MCP server has no audit path of its own (all auditing is backend-side, attributed via
the `X-GAR-Client` header), a reject *attempt* produces **no `audit.jsonl` record** —
only the client conversation shows the error. The reject is a pure no-op (no state
change, no LLM call), so functionally there's nothing to audit; but strictly it is a
small gap against the "every tool call recorded, no shadow path" audit claim.

**The enhancement.** Add a real report-gate transition — **reject → recompose with
feedback** (re-run `phase_compose_report` with the human's feedback folded into the
prompt), or a plain **discard** — in the backend (`governance/hitl.py` +
`api/gates.py` + `agent/loop.py`). Then expose it in both clients:

- Browser: a **Reject** button on `FinalReport` that collects feedback.
- MCP: wire `approve_report(action="reject", feedback=…)` to round-trip to the
  backend instead of raising.

**Build backend-first** so both clients share one transition and the audit is
automatic: once the reject hits the backend it is recorded like any other gate call
(with `client` + feedback), which also closes the audit gap above. Don't special-case
auditing the no-op error in the thin MCP client. The MCP `approve_report` schema is
already forward-compatible (it carries `reject`), so the client surface doesn't change
shape — only its behavior. Relatedly, a bounded recompose loop should be careful not
to recurse indefinitely (cap attempts, like the grounding-retry loop).

---

## 10. v2 — identity, data governance, and sessions (design, 2026-06-18)

**Status: design only (v2 / AWS phase). No v1 code.** This section captures the
data-governance model for the persistent, multi-user version and the *Sessions*
product feature that motivates it. It refines Phase 2 (§3) and folds in spec §2(b)
(public/private separation) and the scale seams (§10 of spec). v1 stays as-is —
everything here is ephemeral-friendly and activates seams already present.

### The real invariant (what segregation actually protects)

The concern is **not** "private content must never leave the machine" — the concept
and notes already go to the LLM/embeddings provider, and idea-derived *terms* must go
to public archives to find related work (that's the task). The invariant is: **idea-
linked data and public data must not co-mingle in a way that exposes the private idea
— specifically across users/tenants, or in a shared/public store.** v1 satisfies this
trivially because everything is ephemeral and per-run; the risk appears when v2
**persists** artifacts.

### D-201: classify by idea-linkage, not byte-provenance ("mixed → owner-scoped")

An artifact is private not because of the bytes it contains but because it encodes a
**linkage to the private idea**: its framing (the concept), its position (concept-
nearest cluster), its selection (the adopted set), or its derivation. Public bytes
with the linkage stripped can be public.

- **"Private" means owner-scoped / tenant-isolated, NOT "may never co-mingle."** Within
  one owner's boundary, concept + candidates + report in one record is fine — it's their
  own data. The harm is only (a) crossing to **another tenant**, or (b) landing idea-
  linked data in a **shared/public** store.
- **Strip-the-linkage-to-share:** a topic clustering of public papers is shareable; the
  `contains_concept` flag and "computed for idea X" privatize it. Selection lists too —
  each arXiv id is public, but "the set this user adopted for their idea" is revealing.

| Artifact | Class | Store (v2) |
|---|---|---|
| Raw notes | private | private, per-tenant, encrypted — never shared |
| arXiv results / search cache | public | the **only** deliberately shared store (pure public) |
| Derived concept | private | private (the idea, distilled) |
| Directions / clusters | mixed (idea-linked) | private/owner-scoped |
| RunState / session | mixed | private, tenant-isolated |
| Report | mixed | private/owner |
| Audit log | metadata today (private if it logs content) | private, per-tenant |

**Caveat — the shared public cache is a side channel.** A shared, enumerable search
cache is a query-existence oracle (tenant B infers tenant A's research directions from
cache hits). For a rate-limited archive the savings may not justify it → per-tenant
cache, or hash-keyed/non-enumerable.

### D-202: two boundaries — tenant (isolation/residency) vs user (idea-privacy)

Model **both from day one**, even while they coincide, so personal → org is data+policy,
not a rewrite. **A tenant is a workspace that can hold N users — exactly one in the
minimal case.** Signup creates a workspace (tenant) + its first user (owner).

- **`tenant_id`** — isolation/residency boundary: CMK, region, billing, one-shot
  deletion key off this. The **hard** wall; never relaxed.
- **`user_id`** — idea-privacy boundary: whose private content this is.
- **`AccessContext`** grows from `(tenant_id, role)` → `(tenant_id, user_id, role)`.
  Records carry `tenant_id` + `owner_user_id`.
- **Two-axis access check** on every run/gate/session access:
  (i) tenant isolation `caller.tenant_id == record.tenant_id` — hard;
  (ii) idea-privacy `caller.user_id == record.owner_user_id` **or an explicit grant** —
  relaxable only via sharing.
- **`owner_user_id` is the user, not the tenant** — even in a multi-user org, one
  researcher's private idea stays user-scoped; colleagues don't see it by sharing a
  billing boundary.

### D-203: minimal identity integration (v2.0), expansion left as a seam

The whole integration is **one point**: `api/auth.py` / `api/deps.py` stop returning a
constant and instead **verify the Cognito JWT** (issuer / JWKS / audience), read `sub →
user_id` and a `custom:tenant_id` (or workspace lookup) → `tenant_id`, and build the
`AccessContext`. Everything downstream — RBAC `tools_for`, the gates, the stores —
already consumes it.

- **Ships minimal:** Cognito auth → `AccessContext(tenant_id, user_id, role)`; tenant =
  one-user workspace; the two-axis check (passes trivially); per-tenant store keys;
  tenant/account deletion. An *identity + isolation* layer, not an org product.
- **Deferred, seam-ready (no rewrite):** multiple users per tenant; **sharing as an
  explicit grant** (`shared_with` / `tenant_visible`, consulted only by check (ii));
  org/admin management; richer roles. Adding these inserts grant records and relaxes one
  half of one check — the isolation wall and store partitioning never move.
- **Sharing must be explicit and linkage-aware** — never a side effect of tenant
  membership. That line keeps the private-idea stance intact under multi-user.
- Sequenced in spec §13: Cognito + multi-tenant (team) → per-tenant CMK → IdP federation
  (regulated). v2.0 is the first rung.

### D-204: Sessions — the persistent product surface

A **session** is a persisted, idea-linked **deliverable bundle**: `{derived concept,
selected literatures, clusters, final report}`. The user can list, view, download, and
delete sessions in the cloud. This is the centerpiece v2 product feature — and the
concrete reason D-201–D-203 exist.

- **Store the deliverable, not the working set.** Persist the concept, the *adopted*
  sources (with metadata), the session's directions/clusters, and the report markdown —
  **not** the raw 300-candidate pool (large, ephemeral, the most public-volume data).
  Sessions stay storage-light and privacy-tight; the bundle is exactly the idea-linked
  artifact set.
- **A session is the persisted `RunState`** (or a projection of it). Persistence is
  seam #3 (in-memory `RunStore` → DynamoDB); a paused run is resumable via seam #4
  (durable HITL), so "my sessions" spans **finished and in-progress** runs.
- **CRUD maps onto the model:**
  - *list / view* → the two-axis access check (D-202) — owner + tenant.
  - *download* → export the owner's own artifact (report `.md`, or a bundle with a small
    JSON of concept / selected sources / clusters so it round-trips).
  - *delete* → **right-to-be-forgotten**: purge the owner-scoped record + its S3 objects;
    with per-tenant CMK later, crypto-shredding.
- **Fixes a current gap:** today a completed run's report is dropped (`get_report` works
  only at the report gate). Sessions require **retaining the final report** in the
  session record (or S3, referenced).

### Open tensions (decide at implementation time)

- **Delete vs. audit retention.** Hard-delete (the privacy default) conflicts with the
  audit pillar's "every step recorded, replayable." Personal product: the audit log is
  owner-scoped, so deleting a session may purge/redact its audit entries (the user owns
  their trace). Regulated deployment: compliance retention may override user deletion — a
  policy knob, not a v2.0 decision.
- **Data residency** tightens via existing seams: the `LLMClient → Bedrock` swap is also a
  residency lever (inference in-account/region), and per-tenant CMK isolates at rest.
- **Cache side channel** (D-201 caveat) — resolve when the persistent cache is built.

### Why this is expansion, not a rewrite

Every piece rides a seam already in v1: `tenant_id` on every record (#1), auth check at
the API boundary (#7), `AccessContext` built in one place, `RunStore` Protocol (#3),
durable-HITL state (#4), and the empty `Auth` CDK stack. Minimal v2 pays for **one extra
field (`user_id` / `owner_user_id`) and one extra check axis** up front; the personal
version then *is* the product version with the multi-user dimension dormant.
