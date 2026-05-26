# GAR — Guided Agentic Retrieval for Literature Survey

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

> Status: v1 backend complete; 221 unit / integration tests passing;
> end-to-end smoke runs against the live arXiv API and the Anthropic
> Claude API have produced complete, cited reports. Frontend is a minimal
> React/TypeScript shell with a rendered Markdown preview. AWS deployment
> scaffolding (CDK) is wired but resources are stubs.

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

We've watched this loop fire in production: on the most recent smoke run,
the first compose attempt produced 2 unknown citations out of 24; the
re-prompt produced a fully-valid report on attempt 2.

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
                                  (rate-      (Claude         (Markdown
                                  limited,    Sonnet 4.6)     files + .ignore)
                                  back-off)
```

- **Frontend**: 5 views routed by `RunState.status`; SSE during long POSTs
  for live activity feed; the final report renders as Markdown.
- **Backend**: agent loop runs synchronously within each gate POST; state
  persists across requests via `RunStore` (in-memory in v1).
- **Public source**: arXiv via its public API. The implementation is
  generic (`PublicSource` Protocol) so adding PubMed / Semantic Scholar /
  Crossref later is a localized change.
- **Private source**: the user's Markdown idea notes (an Obsidian vault
  or any folder). v1 reads `.md` only; `.gitignore` and `.ignore` are
  honored.

The intended deployment target is AWS (Lambda + Function URLs + Step
Functions + DynamoDB + S3 + Cognito + KMS) — see `infra/`. Resources are
stubs at the moment, but the seven scale seams listed below are in the
v1 code already.

---

## Retrieval treated as a design judgement, not a hardcoded choice

Different retrieval methods (keyword, semantic search, rerank, ...) have
different failure modes. v1 ships only keyword search — but the **structure
of the codebase treats retrieval as an interchangeable tool inside the
agent loop**, not as a fixed step before generation:

- `PublicSource` is a Protocol. arXiv is the v1 implementation; future
  sources (semantic search, vector indexes, other catalogues) plug in
  behind the same shape.
- Every retrieval call produces a `SearchResult` with a stable
  `(source_name, external_id)` pair so the grounding validator works
  uniformly across sources.
- Each tool call is audited with input, output count, and duration, so
  later evaluation phases can compare retrieval techniques head-to-head.

The audit log was built with this in mind: rerunning a saved set of
queries against a different retrieval implementation is a future-work
direction the data already supports.

---

## Scope of v1

### Does

- Read a single Markdown file or a folder of them; honor `.gitignore` and
  a sibling `.ignore` (used by the tool itself to skip its own reports).
- Derive a concise concept from the user's notes via Claude.
- Search arXiv iteratively with the agent loop deciding queries.
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
- **Multi-tenant runtime**. The seams are in (`tenant_id` on every
  record; authz check at the API boundary; ToolRegistry is per-context),
  but v1 has one user.
- **AWS deployment**. CDK stacks are scaffolded; resources are not yet
  defined.
- **Bedrock-backed LLM**. The `LLMClient` Protocol is the swap point;
  v1 only ships the Anthropic SDK implementation.
- **Streaming token output**. SSE today streams audit events, not LLM
  tokens.

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

Python 3.14 is pinned. The repo is a uv workspace (`backend/` and
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

### Tests

```bash
uv run --package gar-backend pytest backend/tests/   # 221 tests
(cd frontend && npm run build)                       # type-check + bundle
```

Tests are offline: the arXiv source is exercised via `httpx.MockTransport`;
the LLM client is mocked via a stub that returns pre-baked `LLMResponse`s.
No tests require a real API key.

---

## Repository tour

```
backend/src/gar_backend/
├── main.py             FastAPI app + Mangum hook for Lambda
├── api/                HTTP layer
│   ├── runs.py         POST /runs, GET /runs, GET /runs/{id}
│   ├── gates.py        POST /runs/{id}/gates/{concept,sources,report}
│   ├── stream.py       GET /runs/{id}/events  (SSE)
│   ├── deps.py         DI providers (singletons; overridden in tests)
│   └── auth.py         v1 pass-through; the call site for future auth
├── agent/
│   ├── loop.py         orchestrator + 3 phase functions
│   ├── prompts.py      system prompts per phase
│   ├── tools.py        AgentTool wrappers + dispatch w/ audit
│   └── llm.py          LLMClient Protocol + AnthropicLLM
├── governance/         audit / hitl / grounding / rbac  (one file each)
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
└── state/
    └── runs.py         RunStore Protocol + InMemoryRunStore

frontend/src/
├── App.tsx             status-driven view router
├── lib/
│   ├── api.ts          typed fetch wrappers
│   └── sse.ts          useRunStream hook
└── views/              Start / ConceptReview / SourceSelection /
                        FinalReport / Completed / Activity (SSE feed)

infra/                  AWS CDK (Python) — 5 stacks, currently scaffolded
backend/tests/          221 tests, mirrors src/ layout
spec.md                 Working spec (Japanese)
implementation_brief.md Original input contract (Japanese)
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
- This is a personal project. No employer code, customer data, or
  internal know-how is in this repository.
