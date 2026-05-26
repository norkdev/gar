"""FastAPI dependency providers.

Module-level lazy singletons. Tests override these via FastAPI's
``app.dependency_overrides[get_xxx] = lambda: test_instance`` so the real
constructors (which may need ANTHROPIC_API_KEY etc.) are never invoked
under test.
"""

from pathlib import Path

from gar_backend.agent.llm import AnthropicLLM, LLMClient
from gar_backend.governance.audit import AuditLogger, FileAuditSink
from gar_backend.governance.rbac import AccessContext
from gar_backend.sources.arxiv import ArxivSource
from gar_backend.sources.base import PublicSource
from gar_backend.state.runs import InMemoryRunStore, RunStore


DEFAULT_AUDIT_LOG_PATH = Path("audit.jsonl")


_run_store: RunStore | None = None
_audit_logger: AuditLogger | None = None
_llm_client: LLMClient | None = None
_public_source: PublicSource | None = None


def get_run_store() -> RunStore:
    global _run_store
    if _run_store is None:
        _run_store = InMemoryRunStore()
    return _run_store


def get_audit_log_path() -> Path:
    """Path to the audit log file. Source of truth shared by the file sink
    (which writes) and the SSE endpoint (which tails)."""
    return DEFAULT_AUDIT_LOG_PATH


def get_audit_logger() -> AuditLogger:
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = AuditLogger(FileAuditSink(get_audit_log_path()))
    return _audit_logger


def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = AnthropicLLM()
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


def get_access_context() -> AccessContext:
    """v1: single user, fixed default tenant + owner role.

    When auth is added (Phase 1+) this resolves the authenticated user's
    tenant + role from the request context.
    """
    return AccessContext(tenant_id="default", role="owner")
