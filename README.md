# GAR — Guided Agentic Retrieval for Literature Survey

[![CI](https://github.com/norkdev/gar/actions/workflows/ci.yml/badge.svg)](https://github.com/norkdev/gar/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

<p align="center">
  <img src="gar.png" alt="GAR mascot — a gar fish" width="240">
</p>

Surveys arXiv against your private Markdown idea notes. Every step is
audited; humans gate access, source selection, and the final report.

---

A small, runnable codebase that helps a researcher or engineer survey
published literature against their own in-progress idea — and stops there.
The agent surfaces the closest related work with citations the user can
inspect. It does **not** decide whether the idea is novel or its
contribution genuine. That judgement depends on what the literature
actually shows and on how the user scopes their own contribution; the
human keeps it.

The system is dynamic — the LLM plans tool use, calls retrieval, reasons
over results, decides whether to keep going — but every link in that chain
runs under a **governance layer** that the implementation makes visible. The
codebase is structured so a reader can find and reason about each governance
mechanism on its own. "Guided" sits at the surface because the agent is
not fully autonomous; "governance" describes how that guidance is enforced
inside.

> Status (v2.0.0): backend complete and **deployed to AWS** (Lambda +
> DynamoDB + S3 + Secrets Manager), verified live; 403 unit / integration
> tests passing; end-to-end smoke runs against the live arXiv and Anthropic
> Claude APIs have produced complete, cited reports, driven over the
> Web UI, the MCP server, and the cloud deployment. Retrieval has a swappable **rerank
> stack** (dependency-free BM25 by default; opt-in **semantic reranking**
> via an external embeddings API) and embedding-based **clustering** that
> turns the candidate pool into a positioning map. **Per-phase model
> tiers** (Haiku for the cheap, high-volume phases; Sonnet for the report)
> cut cost. A third client — an MCP server (`gar-mcp`) exposing the
> governed gates over stdio — ships alongside the CLI and Web UI. **v2.x**
> lifts the same backend to AWS (Lambda + DynamoDB + S3 + Secrets Manager,
> async self-invoke worker, Cognito auth), deployed and verified live;
> per-user identity and browser hosting are v2.1.

---

## Why this exists (the design judgements)

This is a personal project, intentionally narrow. Each non-trivial choice
has a reason:

### Why an agent loop, not a fixed retrieve→generate pipeline

A literature survey isn't a single keyword search. A useful query depends
on what the previous query returned; a useful adjustment depends on what
the LLM made of the result; sometimes the right move is to re-read the
user's notes, sometimes it's to broaden the search. A fixed pipeline
encodes the shape of "what to do next" up front; an agent loop lets the
LLM decide step by step. The same code path can survey one note or a
200-note vault.

The loop lives in `backend/src/gar_backend/agent/loop.py`. It's three pure
phase functions plus an orchestrator that drives them through HITL gates.

### Why the agent prepares material, not a judgement

Deciding whether an idea is genuinely new — and whether its contribution
matters — is exactly the kind of question the human writing the paper or
proposal needs to own. The judgement depends on how the user frames their
contribution, what slice of the literature they consider authoritative,
and what position they intend to state publicly. An LLM that asserts
"this is novel" is at best confidently wrong and at worst dangerous to
the user — they might quote it in their paper or grant proposal and find
themselves defending an assertion the model invented.

So the system is shaped to **not** say that. The compose-report prompt
explicitly requires hedged language ("the most similar candidate is X;
the main differentiator appears to be Y") and forbids final judgement
statements. The HITL gates force the human into the loop at exactly the
moments where a judgement would otherwise emerge: after the candidates
are gathered, and before the report is saved.

This isn't safety theatre — it changes what the system is for.

### Why grounding is required, not optional

If the agent can quietly cite a paper it didn't actually retrieve, the
report is worse than useless: it leads the user toward an interpretation
they can't verify. So grounding is enforced as a code-level invariant, not
a prompt nicety:

- Every statement about a paper must carry an `[source_name:external_id]`
  citation that exists in the retrieved evidence set (see
  `governance/grounding.py`).
- After the LLM composes the final report, a validator parses it and
  cross-checks each citation against the adopted candidates. If anything
  doesn't match, the LLM is re-prompted with the specific deviation and
  the valid citation list.
- This loop is bounded (configurable, default 2 attempts). If the LLM
  still emits unknown citations, the latest report is accepted **with a
  warning recorded in the audit log** — the user can read both the report
  and the warning and decide.

#### What the audit log actually looks like

The audit log is JSONL — every LLM call, tool dispatch, and validator
pass becomes one structured record carrying a `schema_version` field
(so schema evolution doesn't strand old logs). A representative slice
from a real run, formatted for readability:

```jsonl
{"timestamp":"2026-05-26T06:31:47Z","tool_name":"llm.complete",
 "input":{"model":"claude-sonnet-4-6","message_count":3,"tool_count":2},
 "output":{"text_blocks":1,"tool_uses":4,"stop_reason":"tool_use"},
 "duration_ms":4520,"status":"ok","schema_version":"1.0"}
{"timestamp":"2026-05-26T06:31:48Z","tool_name":"search_arxiv",
 "input":{"query":"multi-agent user interest matching conversational"},
 "output":{"result_count":10},
 "duration_ms":1230,"status":"ok","schema_version":"1.0"}
{"timestamp":"2026-05-26T06:38:32Z","tool_name":"grounding.validate",
 "input":{"attempt":1,"evidence_count":12},
 "output":{"is_valid":true,"citation_count":18,"unknown_count":0,
           "unused_evidence_count":1},
 "duration_ms":3,"status":"ok","schema_version":"1.0"}
```

A run of about 20 LLM messages + 30 tool dispatches lands in roughly
80–100 KB of JSONL. Replay, retrieval-technique comparison, and the
evaluation work in spec §future-work all start from this log.

During development the re-prompt path fired and recovered on a real
smoke run — the validator caught two unknown citations on the first
compose attempt and the second attempt produced a fully-valid report.
Logs from that specific run have since rotated; the path stays
unit-tested in `backend/tests/agent/test_loop.py`.

### Why public and private sources are physically separated

A literature survey is meaningful only against the public record. The
user's unpublished notes are not public. If the agent ever quoted them
outside the local process — e.g., to a web-search API — the user would
leak the very ideas they were trying to refine in private. So the
codebase keeps the two sources in separate packages (`sources/` for
public; `ideas/` for private), routes them through separate tool
registries (see `governance/rbac.py`), and the search prompt contains
an explicit rule against passing private content to web search.

The same RBAC machinery means **the private tool is structurally absent**
from the LLM's tool list when the caller's role doesn't have private
access — not refused at call time, but unmentioned in the schema. v1 has
a single user with full access, but the seam is built in.

---

## The four governance mechanisms

One file per concern, under `backend/src/gar_backend/governance/`:

| File | What it does | Visible artifact |
|---|---|---|
| `audit.py` | Structured JSONL log of every LLM call, tool dispatch, and grounding check. Schema-versioned. | `audit.jsonl` |
| `hitl.py` | State machine for the three approval gates (concept review → source selection → report approval). Pure functions over a frozen `RunState`; durable for free. | `RunState.status` |
| `grounding.py` | Citation parser + validator. Catches fabricated and malformed citations. | `GroundingReport` audit entries |
| `rbac.py` | Public/private tool registries. Tools are placed in buckets by role; non-owner roles never see private tools in the schema. | `ToolRegistry.tools_for(ctx)` |

The agent loop calls into each of these in a way the audit log makes
externally observable: every step produces a record. A reader who wants
to understand what the agent actually did on a given run reads
`audit.jsonl` — there is no shadow path.

---

## Architecture (v1)

```
┌─────────────────────┐         ┌──────────────────────────────────────┐
│ Vite + React + TS   │  HTTP   │ FastAPI                              │
│ (5 views, SSE feed) │ ◄────►  │  ├─ api/        REST + SSE endpoints │
└─────────────────────┘         │  ├─ agent/      LLM client + loop    │
                                │  ├─ governance/ audit/hitl/grounding │
                                │  │                /rbac              │
                                │  ├─ sources/    public retrieval     │
                                │  ├─ ideas/      private (Markdown)   │
                                │  ├─ reports/    save to vault        │
                                │  └─ state/      RunStore             │
                                └────┬──────────────┬───────────┬──────┘
                                     │              │           │
                                     ▼              ▼           ▼
                                  arXiv API   Anthropic API   Local vault
                                  (rate-      (Claude Haiku   (Markdown
                                  limited,    + Sonnet,       files + .ignore)
                                  back-off)   per phase)
```

- **Frontend**: 5 views routed by `RunState.status`; the final report renders
  as Markdown. (v1 showed a live SSE activity feed during long POSTs; v2.0
  replaced it with polling — see the v2.0 section.)
- **Backend**: in v1 the agent loop runs synchronously within each gate POST;
  state persists across requests via `RunStore` (in-memory in v1, DynamoDB in
  v2.0). v2.0 runs the loop off the request thread (async worker).
- **Public source**: arXiv via its public API. The implementation is
  generic (`PublicSource` Protocol) so adding PubMed / Semantic Scholar /
  Crossref later is a localized change.
- **Private source**: the user's Markdown idea notes (an Obsidian vault
  or any folder). v1 reads `.md` only; `.gitignore` and `.ignore` are
  honored.
- **Reranker**: candidates are ordered by concept-relevance before the
  sources gate. Default is dependency-free BM25; an opt-in semantic
  reranker calls an external embeddings API (Voyage) and additionally
  clusters the pool into topic *directions* (see the two sections below).
  Voyage is therefore an optional fourth external dependency, used only
  when semantic reranking is enabled.

The deployment target is AWS — **v2.0 lifts this same backend to the cloud**
(next section). The seven scale seams listed further below were in the v1
code from the start, which is what made the lift localized rather than a
rewrite.

---

## Architecture (v2.0) — the AWS deployment

v2.0 runs the **identical** `gar_backend.main:app` on AWS. Nothing about the
agent loop, the gates, or the governance layer changed; what changed is where
state lives and how the process is invoked. The lift was deliberately staged
as small, independently deployable slices (see `plan.md` §3), each verified
live before the next.

```
                         ┌──────────────── AWS · ap-northeast-1 ────────────────┐
 MCP / CLI ──┐           │                                                      │
 (Bearer JWT)│  HTTPS    │   Lambda Function URL (auth NONE, CORS)              │
             ├─────────► │     │  Cognito JWT gate (api/auth verifies)         │
 Cognito ◄───┘           │     ▼                                                │
 (M2M token)             │   Lambda  ── Mangum ──►  FastAPI (the v1 app)        │
                         │     │  ▲                   │                          │
                         │     │  └── self-invoke ────┘  (InvocationType=Event)  │
                         │     │      async worker: one segment to the next gate │
                         │     ▼                                                  │
                         │  DynamoDB  S3 (pool + audit)  Cognito   Secrets Mgr   │
                         │  (RunStore)                   (pool)    (Anthropic)   │
                         │                              │                   │     │
                         └──────────────────────────────┼───────────────────┼────┘
                                                         ▼                   ▼
                                                   Anthropic API        (Anthropic
                                                   (Haiku + Sonnet)      key)
```

- **Compute** — the FastAPI app is packaged with Docker (arm64/Graviton) and
  served on Lambda via **Mangum**; `boto3` is dropped from the bundle (the
  runtime provides it). A **Function URL** is the entry point.
- **State** — `RunStore` swaps its in-memory map for **DynamoDB** (one item
  per run, a `tenant-index` GSI for listing). The sources gate's large
  candidate pool would blow DynamoDB's 400 KB item limit, so it is offloaded
  to **S3** and rehydrated on read. The **audit log** becomes a durable S3
  sink — one immutable object per record, because a run is split across
  multiple Lambda invocations and a single per-run object would be overwritten.
- **Async execution** — a survey segment (derive / search / compose) runs for
  minutes, far past the 30 s Function URL timeout. So the request **schedules**
  the segment and returns immediately; the Lambda **self-invokes
  asynchronously** to run it under the full 15-minute budget, and the client
  polls `GET /runs/{id}`. This is the durable-HITL seam realized: each gate
  ends one invocation, approval starts the next. Step Functions orchestration
  is the planned v2.2 evolution of this same seam.
- **Secrets** — the Anthropic key lives in **Secrets Manager**, fetched once
  per cold start; never in the image, an env var, or git.
- **Auth** — the Function URL is `NONE` (no SigV4) but gated in-app by **Cognito
  JWT verification** (`api/auth`). One path for everyone: machine clients
  (MCP/CLI) use the OAuth2 client-credentials (M2M) grant; browser users (later
  slice) sign in. Both send `Authorization: Bearer <token>` over plain HTTP.
  (v2.0 shipped a shared `X-GAR-API-Key` gate as the interim; v2.1 replaced it —
  D-206.)

The governance pillars carry over unchanged in intent: **grounding** is the
same compose-time check; **HITL** gates are now durable across invocations in
DynamoDB; the **audit log** is the same schema (`schema_version`) written to
S3; **role-based access** still hides private-idea tools. The boundary
**auth check** (seam #7) graduated from a stub to Cognito JWT verification.

**Scope of v2.0:** single-tenant lift. **v2.1** then added per-user **identity
(Cognito)** — JWT verification for browser users and OAuth2 client-credentials
(M2M) for the MCP/CLI, one verification path (D-206) — **owner-scoped data** (a
two-axis tenant/owner check, D-202), **sessions** (retain / download / delete,
D-204), and **public browser hosting** (S3 + CloudFront + Cognito Hosted-UI
login). The browser waited for real per-user auth rather than shipping a weak
shared-key-in-JS (the deliberate Option-C call; see `plan.md` §10). **Step
Functions** orchestration and **per-tenant CMK** are v2.2. The **Bedrock** LLM
is a wired seam (`GAR_LLM_PROVIDER`), not an implementation.

Verified end-to-end against the live deployment: a governed survey driven
through the **MCP client** (and via SigV4-signed HTTP before the key gate
landed) reaches the concept gate with the secret resolving, state in DynamoDB,
and audit records in S3.

---

## Retrieval treated as a design judgement, not a hardcoded choice

Different retrieval methods (keyword, semantic search, rerank, ...) have
different failure modes, so the codebase treats retrieval as an
**interchangeable technique inside the agent loop**, not a fixed step
before generation. What began as a seam is now a working stack:

- **Recall-first search.** The search phase is prompted for breadth —
  decompose the concept into facets, query each with varied wording — and
  the user's original note phrases are injected alongside the summarized
  concept so distinctive terms aren't lost. A `recall@K` instrument
  (`retrieval/recall.py`) measures how much of a known relevant set the
  search recovers.
- **A swappable reranker.** Candidates are ordered by concept-relevance
  before the sources gate, behind a `Reranker` Protocol
  (`retrieval/rerank.py`). The default is **BM25** — dependency-free,
  deterministic, always available. `GAR_RERANKER=embedding` swaps in a
  **semantic reranker** (`retrieval/embedding.py`) that scores by cosine
  similarity over an external embeddings API (Voyage by default; any
  OpenAI-style endpoint). Why bother: lexical signals reward vocabulary
  *overlap*, so they surface generic high-frequency-term papers and bury
  relevant work phrased differently — a bias measured on live runs (see
  `plan.md` §5–§6). Embeddings score by *meaning* and correct it. If the
  embeddings API errors, the reranker logs a warning and falls back to
  BM25 — a rerank failure never fails a run.
- **Cross-query provenance.** Each candidate records which query angles
  surfaced it (`support` / `matched_queries`), carried to the sources gate
  as metadata.
- **One shape, uniform downstream.** `PublicSource` is a Protocol (arXiv
  is the v1 implementation; PubMed / Semantic Scholar / vector indexes
  plug in behind the same shape), and every retrieval call produces a
  `SearchResult` with a stable `(source_name, external_id)` pair, so the
  grounding validator and the reranker work identically across sources.

Every retrieval and rerank call is audited with input, output count, and
duration — so comparing techniques head-to-head, or replaying a saved
query set against a different implementation, is something the data
already supports.

---

## From a list to a map: clustering candidates into directions

A survey that hands back 300 candidates as one flat list pushes all the
sense-making onto the human. When the semantic reranker is enabled, GAR
goes further and **clusters the candidate pool into topic "directions"** —
reusing the embeddings it already computed for reranking (memoized, so no
extra API cost).

- **How.** A small, dependency-free k-means (`retrieval/directions.py`)
  over the unit-normalized embeddings, with deterministic seeding so a run
  is reproducible. Each cluster gets representative titles (its
  centroid-nearest members); the cluster the *concept* embedding falls in
  is flagged "nearest your idea."
- **In the report.** The compose phase names each direction and writes a
  **positioning map** in §4 — where the idea sits among the directions,
  which are core vs. adjacent vs. out of scope — still hedged, still
  leaving the novelty judgement to the human.
- **At the sources gate (Web UI).** Candidates are presented **grouped by
  direction**, concept-nearest first, off-topic groups collapsed, with an
  "adopt the top N of this group" shortcut. The grouping is server-side
  structure available to any client, so it stays consistent rather than
  improvised per client.
- **Robustness.** Only the top-N most relevant candidates are clustered
  (`GAR_DIRECTIONS_POOL`, default 200). Because the list is rerank-ordered,
  this drops the off-topic tail — which, left in, lets the far-apart
  seeding pick outliers as cluster centers and collapse everything
  on-topic into one mega-cluster (a degeneration observed live; see
  `plan.md` §6).
- **Graceful absence.** In BM25 mode there are no embeddings and therefore
  no directions: the report omits the map and the gate shows a single
  relevance-ordered list. Nothing breaks.

---

## Cost: per-phase model tiers

The agent doesn't use one model for everything. A `ModelPolicy` assigns a
model per phase: **Haiku** for concept derivation and the search loop (the
token-heaviest phase, where abstracts accumulate in context), and
**Sonnet** for composing the report (the human-facing deliverable, where
citation discipline and hedged synthesis matter most). Each call records
its model in the audit log, so a run's trace shows which tier ran where.
Any phase can be overridden (`GAR_MODEL_DERIVE` / `GAR_MODEL_SEARCH` /
`GAR_MODEL_COMPOSE`), and `GAR_THOROUGH=1` escalates the search phase to
the compose-tier model for a high-stakes run. The LLM client stays a
Protocol, so the Anthropic↔Bedrock swap is unaffected.

---

## Three clients, one governed loop

The same agent loop is reachable three ways. The *ideas* (private notes)
source has two interchangeable implementations behind one shape; the
client picks which by what it sends:

| Client | Ideas source impl | What the backend sees | Where the report goes |
|---|---|---|---|
| **CLI** (`gar /path/to/vault`) | `IdeasSource` — walks the filesystem, honors `.gitignore` + `.ignore`, returns `file://` URLs | A vault path it can read | Saved to the vault folder; filename appended to `.ignore` |
| **Web UI** (Vite + React picker) | `InMemoryIdeasSource` — operates on note contents POSTed from the browser | An array of `(path, content)` pairs | The user downloads / copies from the UI; no backend filesystem access |
| **MCP server** (`gar-mcp`, stdio) | `InMemoryIdeasSource` — note contents passed by the MCP client | An array of `(path, content)` pairs | `get_report` returns the Markdown; the MCP client saves it |

Both ideas-source implementations satisfy the same duck-typed surface
(`.name`, `.list_all()`, `.search()`). The agent loop, audit log, HITL
gates, grounding validator, and RBAC layer don't know or care which one
is mounted. The choice is made at the API boundary based on which field
the `POST /runs` request carries (`vault_path` vs `notes_content`).

This is *why* these code paths exist side by side: the CLI gives a
filesystem-rooted local workflow (with vault write-back and ignore
accounting); the Web UI and the MCP server give backend-agnostic
workflows that work identically against a future AWS-deployed backend.
The shared agent loop and governance layer keep the surfaces
behavior-equivalent without parallel maintenance.

### The MCP server exposes the gates, not the tools

`gar-mcp` lets an MCP client (Claude Code, Claude Desktop) drive a survey.
The deliberate design choice: it exposes **run management and the three
HITL gates** — `start_survey`, `get_run_status`, `review_concept`,
`select_sources`, `approve_report`, `get_report`, `list_runs` — and
**not** GAR's low-level retrieval tools.

That matters because the whole point of GAR is that every step is
grounded, gated, and audited. If the MCP surface offered a raw
`search_arxiv`, the *client's* LLM could run the retrieval-and-compose
loop itself and hand back a "survey" that never passed grounding
validation, never stopped at a human gate, and left no audit trail — the
central claim would break exactly at the protocol boundary. Exposing the
gates instead makes GAR a **governed sub-agent**: the MCP client gets to
orchestrate *when* to advance, but the governance layer still owns *how*
each step runs. The gate tools' descriptions carry the last-mile rule —
get a human decision before calling them — because that mile lives in the
client's behavior.

Two design seams make this cheap and forward-compatible:

- **Thin client over the REST API.** The server doesn't import the
  backend; it calls the same HTTP API the Web UI uses (`GAR_API_URL`,
  default `http://localhost:8000`). After the AWS migration only that URL
  and an auth header change — the same `gar-mcp` runs against a remote
  backend. This extends scale seam #2 (UI never calls AWS directly) to a
  third surface.
- **Role-gated tools.** A `GAR_MCP_ROLE` (`public` by default) selects
  which tools appear. Tools above the role are *absent from the schema*,
  not refused at call time — the same structural-absence principle as the
  RBAC layer. v1.1 ships only public tools; the seam is ready for an
  owner-only ideas search later.

MCP-driven runs are audited like any other: every request carries
`X-GAR-Client: mcp`, recorded on each audit record (`schema_version`
1.1), so the log attributes every run to the surface that drove it.

Configure it for Claude Code with a repo-root `.mcp.json`; see
[`docs/mcp.md`](docs/mcp.md) for that and the Claude Desktop equivalent.

---

## Scope of v1

### Does

- Read a single Markdown file or a folder of them; honor `.gitignore` and
  a sibling `.ignore` (used by the tool itself to skip its own reports).
- Derive a concise concept (a lead sentence + facet bullets) from the
  user's notes via Claude.
- Search arXiv iteratively with the agent loop deciding queries,
  prioritizing recall (facet / synonym breadth + original-note phrases).
- Rerank candidates by concept-relevance — BM25 by default, opt-in
  semantic embeddings — and cluster them into topic *directions*, surfaced
  as a positioning map in the report and a grouped sources gate in the UI.
- Run each phase on a cost-appropriate model tier (Haiku for
  derive / search, Sonnet for the report).
- Let the user edit the concept, select adopted candidates, and approve
  the final report.
- Compose a structured Markdown report with required sections (concept,
  referenced notes, similar related work, hedged assessment, development
  suggestions, references split into adopted vs not).
- Save the report with a date-based filename (suffix on same-day reruns),
  never overwriting, and append the filename to `.ignore`.
- Stream activity events to the frontend as the agent works.

### Doesn't (Future Work, with explicit seams left)

- **PDF / image ingestion** on the private side. The reader interface is
  ready to accept new file types; only Markdown is wired.
- **Public-source PDF body extraction**. arXiv returns title + abstract +
  metadata only in v1. The seam to add full-text retrieval is in the
  source interface.
- **Additional public sources**. PubMed, Semantic Scholar, Crossref,
  IEEE Xplore — each one is a new `PublicSource` implementation.
- **Web search**. The grounding side is designed to accept Text Fragments
  URLs as citations, but no web-search adapter is wired.
- **Structure-aware candidate selection & count-sizing.** Directions
  cluster the pool, but selecting a diverse top-set (MMR-style) and
  probing result counts to size the search up front are future work
  (see `plan.md` §6).
- **Multi-tenant runtime**. The seams are in (`tenant_id` on every
  record; authz check at the API boundary; ToolRegistry is per-context),
  but v1/v2.0 have one user. Per-user identity (Cognito) is v2.1.
- **AWS deployment**. v2.0 deploys the backend (Lambda + DynamoDB + S3 +
  Secrets Manager) — see the "Architecture (v2.0)" section and
  `docs/deploy.md`. Step Functions orchestration and per-tenant CMK are v2.2.
- **Bedrock-backed LLM**. The `LLMClient` Protocol is the swap point and the
  config slot is wired (`GAR_LLM_PROVIDER`, `BedrockLLM` stub); the Bedrock
  call itself is unimplemented.
- **Streaming token output**. The SSE endpoint streams audit events, not LLM
  tokens; the browser no longer consumes it (it polls).

---

## Seven scale seams baked into v1

These are conventions the codebase follows so that adding the missing
pieces above doesn't require a rewrite. None of them add meaningful code
volume; together they keep the door open.

1. **`tenant_id` everywhere.** Every audit record, `RunState`, and
   adopted source carries one. v1 hard-codes `"default"`.
2. **The UI never calls AWS directly.** Even in dev, the frontend talks
   only to the FastAPI backend. The data plane is one hop away from
   wherever the backend ends up running.
3. **Agent state is externalised.** `RunState` is a frozen dataclass
   saved to a `RunStore`. v1 uses an in-process dict; a DynamoDB
   implementation is a 1-class swap.
4. **HITL gates are durable state, not in-flight awaits.** When the
   agent reaches a gate, its state on disk is enough to resume from a
   different process. This is the Step-Functions-`wait-for-callback`
   pattern, written for in-process v1.
5. **The LLM client is a Protocol.** Anthropic ↔ Bedrock is a one-line
   constructor swap.
6. **Audit log carries `schema_version`.** Schema evolution doesn't
   strand old logs.
7. **Authorization is a function at the API boundary.** v1 always
   returns True; the call sites already exist.

---

## Running locally

Python 3.13 is pinned. The repo is a uv workspace (`backend/` and
`infra/` are members); the frontend is a separate npm project.

```bash
# Install
uv sync --all-packages
(cd frontend && npm install)

# Configure
cp .env.example .env
# Edit .env to add your ANTHROPIC_API_KEY

# Run (two terminals)
uv run --package gar-backend uvicorn gar_backend.main:app --reload --port 8000
(cd frontend && npm run dev)
# Open http://localhost:5173
```

The backend writes its audit log to `./audit.jsonl` (gitignored). The
frontend's Vite dev server proxies `/runs` and `/healthz` to the backend
so the page can use relative URLs.

### Deploying to AWS (v2.0)

The same backend runs on AWS — see **`docs/deploy.md`** for the full
deploy / re-deploy / destroy runbook. In short, from `infra/` with `deploy`
credentials exported and Docker running:

```bash
cdk deploy GarDataStack GarAuthStack GarBackendStack --require-approval never
```

then set the Anthropic key into its Secrets Manager secret and point the MCP
server at the Function URL with its Cognito M2M credentials
(`GAR_API_URL` + `GAR_COGNITO_*`). DynamoDB, S3 (state pool + audit log), the
Cognito pool, Lambda, and the JWT gate come up across the three stacks.

### CLI — local-mode shortcut

For terminal users who don't want a browser in the loop:

```bash
uv run --package gar-backend gar /path/to/vault
```

`gar` walks the vault on the local filesystem, drives the same agent
loop, and prompts at each HITL gate (concept review / source selection
/ report approval) as terminal interactions. Concept editing opens
`$EDITOR`; the final approved report is written into the vault folder
with the filename appended to `.ignore` (same behavior as the original
local-mode HTTP flow).

The browser UI uses the *content-upload* path instead (no filesystem
access on the backend side) and exists alongside the CLI; the agent
loop and governance layer are shared. See
[Three clients, one governed loop](#three-clients-one-governed-loop)
below.

### MCP server — drive GAR from an MCP client

`gar-mcp` speaks the Model Context Protocol over stdio, so Claude Code or
Claude Desktop can run a survey through the governed gates. It is a thin
client of the backend's REST API, so the backend must be running:

```bash
# Terminal 1: the backend
uv run --package gar-backend uvicorn gar_backend.main:app --port 8000

# Terminal 2: the MCP server (usually launched by the MCP client, not by hand)
GAR_API_URL=http://localhost:8000 uv run --package gar-backend gar-mcp
```

Configuration is by environment: `GAR_API_URL` (default
`http://localhost:8000`), `GAR_MCP_ROLE` (`public` by default), and — for the
cloud backend — the Cognito M2M variables (`GAR_COGNITO_TOKEN_ENDPOINT` /
`_CLIENT_ID` / `_CLIENT_SECRET` / `_SCOPE`), unneeded locally. For the client
config, copy
`.mcp.json.example` (Claude Code) or run
`./scripts/print-mcp-config.sh claude-desktop` to get a paste-ready block
with the absolute path filled in. See [`docs/mcp.md`](docs/mcp.md) for
both, the tool list, and why the surface is the gates rather than the raw
tools.

### Optional configuration (retrieval & cost)

All optional; sensible defaults shown. Set in `.env` (backend) or in the
MCP / CLI environment. With the defaults the backend needs no extra
service and no embeddings key.

| Variable | Default | Effect |
|---|---|---|
| `GAR_RERANKER` | `bm25` | `embedding` enables semantic rerank + directions clustering |
| `VOYAGE_API_KEY` / `GAR_EMBED_API_KEY` | _(unset)_ | embeddings API key (required for `embedding` mode) |
| `GAR_EMBED_MODEL` / `GAR_EMBED_URL` | `voyage-3.5` / Voyage | override the embeddings model / endpoint |
| `GAR_DIRECTIONS_K` | auto: `clamp(round(n/40),3,7)` | number of clusters |
| `GAR_DIRECTIONS_POOL` | `200` | top-relevance candidates to cluster (drops the off-topic tail) |
| `GAR_MODEL_DERIVE` / `_SEARCH` / `_COMPOSE` | Haiku / Haiku / Sonnet | per-phase model override |
| `GAR_THOROUGH` | _(off)_ | escalate the search phase to the compose-tier model |

### Tests

```bash
uv run --package gar-backend pytest backend/tests/   # 403 tests
(cd frontend && npm run build)                       # type-check + bundle
```

Tests are offline: the arXiv source is exercised via `httpx.MockTransport`;
the LLM client is mocked via a stub that returns pre-baked `LLMResponse`s;
AWS is mocked with `moto` (DynamoDB / S3 / Secrets Manager). No tests require
a real API key or live AWS.

---

## Repository tour

```
backend/src/gar_backend/
├── main.py             FastAPI app + Mangum handler (HTTP + async worker event)
├── secrets.py          resolve the Anthropic key (env or Secrets Manager)
├── api/                HTTP layer
│   ├── runs.py         POST /runs · GET /runs · GET/DELETE /runs/{id} · /report
│   ├── gates.py        POST /runs/{id}/gates/{concept,sources,report}
│   ├── stream.py       GET /runs/{id}/events  (SSE; unused by the v2 browser)
│   ├── segments.py     run a segment off the request (in-process / Lambda self-invoke)
│   ├── agent_wiring.py build AgentContext from a request or a stored run
│   ├── deps.py         DI providers (singletons; overridden in tests)
│   └── auth.py         Cognito JWT gate (disabled when no pool configured)
├── agent/
│   ├── loop.py         orchestrator + 3 phase functions
│   ├── prompts.py      system prompts per phase
│   ├── tools.py        AgentTool wrappers + dispatch w/ audit
│   └── llm.py          LLMClient Protocol + AnthropicLLM + BedrockLLM (seam)
├── governance/         audit (file + S3 sinks) / hitl / grounding / rbac
├── sources/
│   ├── base.py         PublicSource Protocol + SearchResult
│   └── arxiv.py        arXiv source w/ ToU-compliant rate limit + 429 retry
├── ideas/
│   ├── walker.py       .gitignore- and .ignore-aware folder walker
│   ├── reader.py       Markdown loader (PDF interface stubbed for future)
│   └── search.py       keyword search returning SearchResult
├── reports/
│   ├── naming.py       date-based filenames + .ignore accounting
│   ├── builder.py      save composed report to vault
│   └── linkify.py      turn citations into Markdown links
├── retrieval/
│   ├── rerank.py       Reranker Protocol + BM25 (default)
│   ├── embedding.py    opt-in semantic reranker + directions clustering
│   ├── directions.py   k-means over embeddings → topic directions
│   └── recall.py       recall@K instrument
├── state/
│   └── runs.py         RunStore Protocol + InMemory + DynamoDb (S3 pool offload)
└── mcp_server/         FastMCP gates over stdio — thin REST client
    ├── server.py       entry point (gar-mcp)
    ├── tools.py        gate tools + dispatch
    ├── client.py       httpx wrapper (X-GAR-Client + Cognito M2M bearer)
    └── models.py       Pydantic I/O shared with the API

frontend/src/
├── App.tsx             auth gate + status-driven view router
├── lib/
│   ├── api.ts          typed fetch wrappers
│   ├── config.ts       base URL (runtime config) + Cognito bearer header
│   ├── runtimeConfig.ts fetch /config.json (local → no-auth fallback)
│   ├── auth.ts         Cognito login (oidc-client-ts, auth-code + PKCE)
│   └── poll.ts         useRunProgress hook (polls until the next gate)
└── views/              Login / Start / ConceptReview / SourceSelection /
                        FinalReport / Completed / Processing (polling)

infra/                  AWS CDK (Python) — Data + Auth + Backend + Frontend deployed;
                        Workflow scaffold (see docs/deploy.md)
backend/tests/          426 tests, mirrors src/ layout
spec.md                 Working spec (Japanese)
CLAUDE.md               Notes for Claude Code working in this repo
```

---

## Provenance and credits

- Public-source data in any report is retrieved via the arXiv API under
  its [Terms of Use](https://info.arxiv.org/help/api/tou.html). The
  retrieval client respects the documented "no more than one request
  every three seconds, and a single connection at a time" limit and
  applies a 3 → 6 → 12-second exponential back-off on HTTP 429 and on
  read timeouts.
- LLM inference is via the Anthropic Claude API.
- When semantic reranking is enabled (`GAR_RERANKER=embedding`), candidate
  title+abstract text and the derived concept are embedded via the Voyage
  AI API. This is **opt-in**; the default BM25 path uses no external
  embeddings service. (The concept already goes to the LLM provider, so
  the marginal exposure is small — and it stays local under the default
  reranker.)
- This is a personal project. No employer code, customer data, or
  internal know-how is in this repository.

---

## License

MIT — see [LICENSE](LICENSE).
