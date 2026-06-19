"""Async segment runners + the worker entry point (no real AWS / no LLM)."""

import asyncio
import json
from typing import Any

import pytest
from gar_backend.api import segments
from gar_backend.governance.audit import AuditLogger, FileAuditSink
from gar_backend.governance.hitl import RunState, RunStatus
from gar_backend.state.runs import InMemoryRunStore


def _state(run_id: str = "r1", tenant: str = "t9") -> RunState:
    return RunState(
        run_id=run_id,
        tenant_id=tenant,
        status=RunStatus.DERIVING_CONCEPT,
        context={"notes_content": [{"path": "a.md", "content": "x"}]},
    )


# --------------------------- runner selection ---------------------------


def test_make_runner_defaults_to_in_process() -> None:
    # conftest clears AWS_LAMBDA_FUNCTION_NAME (i.e. "not on Lambda").
    assert isinstance(segments.make_segment_runner(), segments.InProcessRunner)


def test_make_runner_selects_lambda_on_lambda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The Lambda runtime sets this to the function's own name.
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "GarBackendStack-ApiFunctionXYZ")
    runner = segments.make_segment_runner()
    assert isinstance(runner, segments.LambdaRunner)
    assert runner._function_name == "GarBackendStack-ApiFunctionXYZ"


# --------------------------- LambdaRunner -------------------------------


class _FakeLambda:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def invoke(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"StatusCode": 202}


async def test_lambda_runner_async_invokes_with_worker_payload() -> None:
    fake = _FakeLambda()
    runner = segments.LambdaRunner("gar-api", lambda_client=fake)
    await runner.schedule("r1", ctx=object(), client="mcp")  # type: ignore[arg-type]

    (call,) = fake.calls
    assert call["FunctionName"] == "gar-api"
    assert call["InvocationType"] == "Event"  # async, fire-and-forget
    payload = json.loads(call["Payload"])
    assert payload == {segments.WORKER_EVENT_KEY: {"run_id": "r1", "client": "mcp"}}


# ------------------- InProcess / Inline runners -------------------------


async def test_in_process_runner_runs_segment_off_the_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran = asyncio.Event()
    seen: dict[str, Any] = {}

    async def _spy(*, run_id: str, ctx: Any) -> None:
        seen["run_id"] = run_id
        ran.set()

    monkeypatch.setattr(segments, "run_until_gate", _spy)
    runner = segments.InProcessRunner()
    await runner.schedule("r1", ctx=object(), client=None)  # type: ignore[arg-type]

    # schedule() returns before the segment runs; the task completes on the loop.
    await asyncio.wait_for(ran.wait(), timeout=1)
    assert seen["run_id"] == "r1"


async def test_inline_runner_runs_segment_before_returning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    async def _spy(*, run_id: str, ctx: Any) -> None:
        seen["run_id"] = run_id

    monkeypatch.setattr(segments, "run_until_gate", _spy)
    await segments.InlineRunner().schedule("r1", ctx=object(), client=None)  # type: ignore[arg-type]
    assert seen["run_id"] == "r1"  # already ran by the time schedule() returned


# --------------------------- worker segment -----------------------------


async def test_run_worker_segment_rebuilds_context_from_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    store = InMemoryRunStore()
    state = _state()
    await store.save(state)

    built: dict[str, Any] = {}
    sentinel = object()

    def _fake_build(**kwargs: Any) -> Any:
        built.update(kwargs)
        return sentinel

    async def _spy_run(*, run_id: str, ctx: Any) -> RunState:
        built["run_id"] = run_id
        built["ctx"] = ctx
        return state

    monkeypatch.setattr(segments, "get_run_store", lambda: store)
    monkeypatch.setattr(
        segments,
        "get_audit_logger",
        lambda: AuditLogger(FileAuditSink(tmp_path / "a.jsonl")),
    )
    monkeypatch.setattr(segments, "get_llm_client", lambda: "LLM")
    monkeypatch.setattr(segments, "get_public_source", lambda: "SRC")
    monkeypatch.setattr(segments, "build_agent_context", _fake_build)
    monkeypatch.setattr(segments, "run_until_gate", _spy_run)

    result = await segments.run_worker_segment("r1", client="web")

    assert result is state
    assert built["ctx"] is sentinel and built["run_id"] == "r1"
    assert built["store"] is store
    assert built["llm"] == "LLM"
    assert built["public_source"] == "SRC"
    # tenant comes from the stored run; client is bound onto the audit logger.
    assert built["access"].tenant_id == "t9"
    assert built["audit"]._client == "web"


async def test_run_worker_segment_unknown_run_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(segments, "get_run_store", lambda: InMemoryRunStore())
    with pytest.raises(ValueError, match="Unknown run"):
        await segments.run_worker_segment("missing", client=None)
