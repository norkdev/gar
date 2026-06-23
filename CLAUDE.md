# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project state

**v2.1 deployed to AWS + verified live (Cognito auth + public browser hosting on a custom domain); backend complete against live arXiv + Anthropic APIs.** Monorepo as a uv workspace; three siblings:

- `backend/` — FastAPI + agent loop + governance + sources/ideas/state/reports + `retrieval/`, plus an MCP server (`mcp_server/`, entry point `gar-mcp`) that exposes the governed gates over stdio as a third client (see `plan.md` / `docs/mcp.md`). Retrieval has a swappable `Reranker` (dependency-free BM25 default; opt-in Voyage embeddings via `GAR_RERANKER=embedding`), embedding **directions clustering** (report positioning map + grouped sources gate), a `recall@K` instrument, and **per-phase model tiers** (Haiku for derive/search, Sonnet for compose; `GAR_MODEL_*` / `GAR_THOROUGH`). **v2.0 cloud seams**: `DynamoDbRunStore` (+ S3 candidate-pool offload), `S3AuditSink`, async self-invoke worker (`api/segments.py`, `main.handler` worker branch), **Cognito JWT gate** (`auth.py` — one auth path; M2M client-credentials for MCP/CLI, sign-in for the browser; replaces the v2.0 `X-GAR-API-Key` gate; disabled when `GAR_COGNITO_ISSUER` is unset, so local dev / tests run open), Secrets Manager key resolution (`secrets.py`), Bedrock provider seam (`GAR_LLM_PROVIDER`). 453 unit / integration tests passing (moto for AWS). End-to-end smoke produced a complete, cited Markdown report; the grounding-retry path fired and recovered in production.
- `frontend/` — Vite + React + TypeScript. Views: `Login` (pre-sign-in) + Start / ConceptReview / SourceSelection / FinalReport / Completed, plus a `Processing` view that **polls** `GET /runs/{id}` for state and `GET /runs/{id}/activity` for a progress feed (the SSE stream can't work behind the Function URL, so the live activity feed was rebuilt as polling). Auth + base URL come from a runtime `config.json` (`apiUrl` + `cognito`); the browser signs in via **Cognito** (oidc-client-ts, auth-code + PKCE) and sends `Authorization: Bearer <jwt>`. Builds cleanly. **Publicly hosted** on S3 + CloudFront behind a custom domain (`infra/` `FrontendStack`); local dev with no `config.json` runs open against `localhost:8000`.
- `infra/` — AWS CDK (Python). `DataStack` (DynamoDB `gar-runs` + state S3 bucket), `AuthStack` (Cognito user pool + M2M app client), `BackendStack` (Lambda arm64 + Function URL `NONE` + CORS, async self-invoke IAM, two Secrets Manager secrets), and `FrontendStack` (S3 + CloudFront + OAC, runtime `config.json`, custom domain via an ACM cert in `us-east-1`) are all **deployed** in `ap-northeast-1` (account `733287383921`). Only `GarWorkflowStack` is still a scaffold. Deploy creds: `deploy` profile (short-lived; `eval "$(aws configure export-credentials --profile deploy --format env)"` before `cdk`). **Deploy / re-deploy / destroy runbook: `docs/deploy.md`.**

Public-facing design narrative lives in `README.md`; design constraints / non-negotiables in `spec.md`.

## Required reading before designing or coding

**`spec.md` is the current working spec** (written in Japanese). Read it in full before proposing architecture, picking dependencies, or writing non-trivial code. `implementation_brief.md` is the **original input contract** kept for historical reference — when the two disagree, `spec.md` wins.

Summary of the non-negotiables `spec.md` establishes (see the file for full detail):

- **What's being built:** *Guided Agentic Retrieval* for literature survey — it helps a researcher compare published literature against their own in-progress idea, but **never decides novelty or contribution itself**. The agent presents grounded candidates; the human judges. ("Guided" is the surface label; the four governance mechanisms below are how that guidance is enforced internally.)
- **Hard separation between public sources and private (unpublished) ideas.** Mixing them defeats the purpose and risks leaking the user's private ideas into a public-knowledge context (which would compromise the originality they're working to refine).
- **Governance layer — four pillars that must show up in the implementation, not just the README:**
  1. **Grounding required** — every statement about a paper cites a retrieved source; if it can't be cited, the agent must say so rather than fabricate.
  2. **Human-in-the-loop approval** — gating access to private ideas, any external transmission, and any comparative conclusion.
  3. **Audit log** — every tool call recorded (what, when, which source) so a run can be replayed.
  4. **Role-based access** — private-idea tools must be *invisible/uncallable* to non-owner roles, not just refused at call time.
- **Agent loop**, not a fixed retrieve→generate pipeline. The LLM plans tool use, executes, accumulates results, decides whether to keep going.
- **Retrieval sources behind one abstract interface** so further specialised databases (PubMed, Semantic Scholar, Crossref, ...) can be slotted in later. Additional public sources are *Future Work* — leave the seam, don't build it.
- **Retrieval techniques (semantic search, rerank, keyword) are tools inside the loop**, not a fixed stack — leave room to compare them in a later evaluation phase.
- **v1 scope is tight on purpose** — see `spec.md §11`. Private side is Markdown-only; public side (arXiv) takes title + abstract + metadata only. PDF parsing, images, additional public sources, multi-tenant runtime, Bedrock LLM, per-tenant CMK are all **Future Work** with explicit seams (interfaces / config slots) but no implementation.
- **Source identifiers are kept generic.** The public-source interface (`PublicSource` Protocol in `sources/base.py`) carries `name` / `tool_name` / `tool_description` as class attributes; the agent loop never hard-codes any specific source. `arxiv` appears only inside `sources/arxiv.py`, its tests, and the one wiring line in `api/deps.py` that selects which concrete `PublicSource` to instantiate. Adding a second public source is a localized change (new file, edit one line in `deps.py`, register).
- **Architecture is React + FastAPI + AWS** — Vite + React + TypeScript frontend (static on S3+CloudFront), FastAPI on Lambda+Function URLs via Mangum, Step Functions for agent orchestration with wait-for-callback for HITL gates, DynamoDB for state/checkpoint, S3 for audit log (JSONL) and private content and search cache. No VPC / NAT Gateway in v1.
- **Seven scale seams from v1** (spec.md §10): `tenant_id` on every record, UI never calls AWS directly, agent state mirrored to DynamoDB, HITL as durable state, LLM client abstracted (Anthropic↔Bedrock-swappable), `schema_version` on audit logs, auth check at the API boundary (stub today).
- **Personal project, public repo.** No employer code, customer data, or internal know-how may enter this repo. Credentials stay out of git.

When a design choice is ambiguous, defer to the spec's intent over convenience. The README is expected to **explain the design judgments** (why agent loop, why the agent prepares material rather than a judgement, how each governance pillar is realized, retrieval-technique tradeoffs, limits, Future Work) — keep notes as you build so this can be written without archeology later.

## Environment & commands

Python **3.13** is pinned via `.python-version` (chosen to match the AWS Lambda managed `python3.13` runtime — see deployment notes); the venv at `.venv/` is **uv-managed** (uv 0.9.17). The repo is a uv workspace — `backend/` and `infra/` are members.

### Setup

```bash
uv sync --all-packages              # install all Python workspace deps
(cd frontend && npm install)        # install frontend deps
```

`ANTHROPIC_API_KEY` lives in `.env` (gitignored). Use `.env.example` as a template.

### Run / verify each sub-project

```bash
# Backend
uv run --package gar-backend pytest backend/tests/                   # 453 tests
uv run --package gar-backend uvicorn gar_backend.main:app --reload   # dev server
# → http://localhost:8000/healthz; OpenAPI at /docs

# Frontend
(cd frontend && npm run dev)        # dev server on http://localhost:5173
(cd frontend && npm run build)      # type-check + production build
# The dev server proxies /runs and /healthz to the backend on :8000

# Infra (CDK CLI not yet installed — install separately with `npm install -g aws-cdk` or `brew install aws-cdk`)
(cd infra && uv run --package gar-infra python app.py)   # bare synth, writes ./cdk.out/
(cd infra && cdk synth)                                  # once CDK CLI is available
```

### Smoke test against live APIs

The backend has been smoke-tested against the live arXiv and Anthropic
APIs end-to-end (cost ~$0.15–0.25 per run). See `audit.jsonl` after a
real run for the full trace of LLM calls, tool dispatches, grounding
validations, and retry events.

### Adding dependencies

- Backend: `uv add --package gar-backend <pkg>`
- Infra: `uv add --package gar-infra <pkg>`
- Frontend: `(cd frontend && npm install <pkg>)`

### Lint / format

Backend + infra (ruff config lives at the root `pyproject.toml`):

```bash
uv run --package gar-backend ruff check backend/ infra/         # lint
uv run --package gar-backend ruff format backend/ infra/        # format in-place
uv run --package gar-backend ruff format --check backend/ infra/   # CI mode
```

Frontend (ESLint flat config + Prettier):

```bash
(cd frontend && npm run lint)            # ESLint
(cd frontend && npm run format)          # Prettier write
(cd frontend && npm run format:check)    # CI mode
```

All four checks run in CI on every push and PR; the workflow lives at
`.github/workflows/ci.yml`.
