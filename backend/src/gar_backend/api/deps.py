"""FastAPI dependency providers.

Module-level lazy singletons. Tests override these via FastAPI's
``app.dependency_overrides[get_xxx] = lambda: test_instance`` so the real
constructors (which may need ANTHROPIC_API_KEY etc.) are never invoked
under test.
"""

from __future__ import annotations

import os
from pathlib import Path

from anthropic import AsyncAnthropic
from fastapi import Depends, HTTPException, Request

from gar_backend.agent.llm import AnthropicLLM, BedrockLLM, LLMClient
from gar_backend.api.auth import AuthError, bearer_token, get_verifier
from gar_backend.governance.audit import (
    KNOWN_CLIENTS,
    AuditLogger,
    FileAuditSink,
    S3AuditSink,
)
from gar_backend.governance.rbac import AccessContext
from gar_backend.secrets import resolve_anthropic_api_key
from gar_backend.sources.arxiv import ArxivSource
from gar_backend.sources.base import PublicSource
from gar_backend.state.runs import RunStore, make_run_store

# When set (on Lambda), the audit log is written durably to this S3 bucket
# instead of a local file. Distinct from GAR_STATE_BUCKET so enabling the
# DynamoDB/S3 run store doesn't implicitly move the audit log off-file.
AUDIT_BUCKET_ENV = "GAR_AUDIT_BUCKET"

DEFAULT_AUDIT_LOG_PATH = Path("audit.jsonl")

# Header the calling surface sets to identify itself in the audit log (D-106).
# The web UI, CLI, and MCP server each send their name; an absent or
# unrecognized value records as null rather than polluting the log.
CLIENT_HEADER = "X-GAR-Client"


_run_store: RunStore | None = None
_audit_logger: AuditLogger | None = None
_llm_client: LLMClient | None = None
_public_source: PublicSource | None = None


def get_run_store() -> RunStore:
    global _run_store
    if _run_store is None:
        _run_store = make_run_store()
    return _run_store


def get_audit_log_path() -> Path:
    """Path to the audit log file used by FileAuditSink (local dev). Source of
    truth shared by the file sink (which writes) and the SSE endpoint (which
    tails). Override with ``GAR_AUDIT_LOG_PATH``. On Lambda the durable
    S3AuditSink is selected instead (``GAR_AUDIT_BUCKET``); this path then only
    matters if the file sink is forced."""
    return Path(os.environ.get("GAR_AUDIT_LOG_PATH") or DEFAULT_AUDIT_LOG_PATH)


def get_audit_logger() -> AuditLogger:
    """Process-wide base logger (holds the sink). Callers without an HTTP
    request — the CLI — use this directly and bind their own client via
    ``.for_client(...)``. HTTP routes use ``get_request_audit_logger`` instead."""
    global _audit_logger
    if _audit_logger is None:
        bucket = os.environ.get(AUDIT_BUCKET_ENV)
        sink = S3AuditSink(bucket) if bucket else FileAuditSink(get_audit_log_path())
        _audit_logger = AuditLogger(sink)
    return _audit_logger


def client_from_request(request: Request) -> str | None:
    """Resolve the calling surface from the X-GAR-Client header (D-106).

    Whitelisted to the known surfaces so an arbitrary header value can't be
    written verbatim into the audit log; anything else records as null.
    """
    value = request.headers.get(CLIENT_HEADER)
    return value if value in KNOWN_CLIENTS else None


def get_client(request: Request) -> str | None:
    """The calling surface (web / cli / mcp) as a plain value, for callers that
    need it apart from the audit logger — e.g. the segment runner, which carries
    it across the async worker boundary so the worker can attribute the run."""
    return client_from_request(request)


def get_request_audit_logger(
    request: Request,
    base: AuditLogger = Depends(get_audit_logger),
) -> AuditLogger:
    """Request-scoped audit logger bound to the calling surface (D-106).

    Shares the process-wide sink but stamps each record with the client that
    drove the request, so the audit log attributes every run to its surface.

    ``base`` is resolved through Depends (not by calling get_audit_logger
    directly) so test overrides of the base logger flow through here."""
    return base.for_client(client_from_request(request))


def make_llm_client() -> LLMClient:
    """Select the LLM provider (spec seam #5). ``GAR_LLM_PROVIDER=bedrock``
    picks the Bedrock seam (stub today); anything else is Anthropic, with the
    key resolved from env or Secrets Manager."""
    if os.environ.get("GAR_LLM_PROVIDER", "anthropic").lower() == "bedrock":
        return BedrockLLM()
    key = resolve_anthropic_api_key()
    inner = AsyncAnthropic(api_key=key) if key else AsyncAnthropic()
    return AnthropicLLM(inner)


def get_llm_client() -> LLMClient:
    """Process-wide singleton LLM client.

    Resolved once here, not per request, so the secret is fetched once per cold
    start rather than on every run."""
    global _llm_client
    if _llm_client is None:
        _llm_client = make_llm_client()
    return _llm_client


def get_public_source() -> PublicSource:
    """Process-wide singleton for the public retrieval source.

    Public-source providers typically impose per-process rate limits.
    Holding the rate-limit clock and connection pool on ONE shared instance
    enforces compliance across all callers.

    v1 wires a single concrete ``PublicSource`` implementation here. Adding
    or swapping sources is a localized change to this function (and, if
    multiple are needed at once, a return-type promotion to a collection).
    """
    global _public_source
    if _public_source is None:
        _public_source = ArxivSource()
    return _public_source


def get_access_context(request: Request) -> AccessContext:
    """Identity from the verified Cognito token (D-203).

    When auth is disabled (no pool configured) returns a default-owner
    context — local dev and tests. Otherwise requires a valid bearer token;
    401 on a missing or invalid one. This is the single auth point: every
    run/gate/stream route depends on it (see main.py)."""
    verifier = get_verifier()
    if verifier is None:
        return AccessContext(tenant_id="default", user_id="local-owner", role="owner")
    token = bearer_token(request)
    if token is None:
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        return verifier.verify(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=f"invalid token: {exc}") from exc
