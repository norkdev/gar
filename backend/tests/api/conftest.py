"""Shared fixtures for api/ tests."""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from gar_backend.agent.llm import LLMResponse, Message
from gar_backend.api.deps import (
    get_access_context,
    get_audit_log_path,
    get_audit_logger,
    get_llm_client,
    get_run_store,
)
from gar_backend.api.segments import InlineRunner, get_segment_runner
from gar_backend.governance.audit import AuditLogger, FileAuditSink
from gar_backend.governance.rbac import AccessContext
from gar_backend.main import app
from gar_backend.state.runs import InMemoryRunStore


class StubLLM:
    """LLM that returns canned responses; tests append before each call."""

    def __init__(self) -> None:
        self.responses: list[LLMResponse] = []
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[Any],
        model: str,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        self.calls.append(
            {
                "system": system,
                "messages": messages,
                "tools": tools,
                "model": model,
            }
        )
        if not self.responses:
            raise RuntimeError("StubLLM out of canned responses")
        return self.responses.pop(0)


def text_response(text: str) -> LLMResponse:
    return LLMResponse(text_blocks=(text,), tool_uses=(), stop_reason="end_turn")


@pytest.fixture
def api_setup(tmp_path: Path) -> Any:
    """Build a fresh isolated API environment per test.

    Returns a dict with:
    - vault: tmp_path containing one .md file
    - store, audit, llm, access: the overridden dependency instances
    - client: TestClient bound to `app`
    """
    (tmp_path / "idea.md").write_text("a half-formed idea about widgets")

    audit_path = tmp_path / "audit.jsonl"
    store = InMemoryRunStore()
    audit = AuditLogger(FileAuditSink(audit_path))
    llm = StubLLM()
    access = AccessContext(tenant_id="default", role="owner")

    app.dependency_overrides[get_run_store] = lambda: store
    app.dependency_overrides[get_audit_logger] = lambda: audit
    app.dependency_overrides[get_audit_log_path] = lambda: audit_path
    app.dependency_overrides[get_llm_client] = lambda: llm
    app.dependency_overrides[get_access_context] = lambda: access
    # Run segments inline so endpoint tests see the gate state synchronously;
    # the async runners have their own dedicated tests.
    app.dependency_overrides[get_segment_runner] = lambda: InlineRunner()

    client = TestClient(app)
    try:
        yield {
            "vault": tmp_path,
            "audit_path": audit_path,
            "store": store,
            "audit": audit,
            "llm": llm,
            "access": access,
            "client": client,
        }
    finally:
        app.dependency_overrides.clear()
