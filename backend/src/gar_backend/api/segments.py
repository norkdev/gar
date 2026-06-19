"""Run agent segments off the HTTP request thread (spec §9 async execution).

A "segment" is one ``run_until_gate`` pass: the agent advances from its current
status through phases until it hits a HITL gate or a terminal state, persisting
to the store as it goes. Segments can take minutes (LLM compose), so they must
not block the HTTP response — the Function URL caps a request at 30 s.

The endpoints therefore *schedule* a segment and return immediately; the client
polls ``GET /runs/{id}``. Two runners implement the seam:

- ``InProcessRunner`` — local/dev: fire-and-forget ``asyncio`` task on the
  serving loop. The process stays alive (uvicorn), so the task runs to the gate.
- ``LambdaRunner`` — cloud: invoke this same function asynchronously
  (``InvocationType="Event"``) with a worker event. The worker invocation
  rebuilds the context from the store and runs the segment with the full Lambda
  timeout. Step Functions orchestration is the later (v2.2) evolution of this
  seam.

The Lambda runner is selected when ``AWS_LAMBDA_FUNCTION_NAME`` is present — the
Lambda runtime always sets it to the function's own name, so the runner targets
itself with no CDK self-reference (which would be a CloudFormation dependency
cycle) and no extra config. Tests override the runner with an inline one so
endpoint behavior stays deterministic.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Protocol

from gar_backend.agent.loop import AgentContext, run_until_gate
from gar_backend.api.agent_wiring import build_agent_context, ideas_source_for_state
from gar_backend.api.deps import (
    get_audit_logger,
    get_llm_client,
    get_public_source,
    get_run_store,
)
from gar_backend.governance.hitl import RunState
from gar_backend.governance.rbac import AccessContext

# Set by the Lambda runtime to the function's own name. Its presence means
# "running on Lambda" and supplies the self-invoke target in one signal.
WORKER_FUNCTION_ENV = "AWS_LAMBDA_FUNCTION_NAME"

# Marker key on an async-invoke event payload identifying a worker dispatch
# (vs. a Function URL HTTP event, which Mangum handles). See main.handler.
WORKER_EVENT_KEY = "gar_worker"


class SegmentRunner(Protocol):
    """Schedules a segment to run, returning once it is *scheduled* (not done)."""

    async def schedule(
        self, run_id: str, *, ctx: AgentContext, client: str | None
    ) -> None: ...


class InlineRunner:
    """Runs the segment synchronously before returning. For tests, and any
    caller that wants the old blocking semantics. ``ctx`` is used directly."""

    async def schedule(
        self, run_id: str, *, ctx: AgentContext, client: str | None
    ) -> None:
        await run_until_gate(run_id=run_id, ctx=ctx)


class InProcessRunner:
    """Fire-and-forget on the serving event loop (single-process local mode).

    Holds strong refs to in-flight tasks so they aren't garbage-collected
    mid-run (asyncio only keeps weak refs), clearing each on completion.
    """

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()

    async def schedule(
        self, run_id: str, *, ctx: AgentContext, client: str | None
    ) -> None:
        task = asyncio.create_task(self._run(run_id, ctx))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @staticmethod
    async def _run(run_id: str, ctx: AgentContext) -> None:
        await run_until_gate(run_id=run_id, ctx=ctx)


class LambdaRunner:
    """Self-invoke this function asynchronously to run the segment.

    The in-memory ``ctx`` cannot cross the invocation boundary, so it is
    ignored here; the worker invocation rebuilds it from the stored run via
    ``run_worker_segment``. Only the run id + client travel in the event.
    """

    def __init__(
        self, function_name: str, *, lambda_client: object | None = None
    ) -> None:
        self._function_name = function_name
        self._lambda = lambda_client

    def _client(self) -> object:
        if self._lambda is None:
            import boto3

            self._lambda = boto3.client("lambda")
        return self._lambda

    async def schedule(
        self, run_id: str, *, ctx: AgentContext, client: str | None
    ) -> None:
        payload = json.dumps(
            {WORKER_EVENT_KEY: {"run_id": run_id, "client": client}}
        ).encode("utf-8")
        # boto3 is sync; keep the event loop free while the invoke round-trips.
        await asyncio.to_thread(
            self._client().invoke,  # type: ignore[attr-defined]
            FunctionName=self._function_name,
            InvocationType="Event",  # async: returns once queued, doesn't wait
            Payload=payload,
        )


def make_segment_runner() -> SegmentRunner:
    name = os.environ.get(WORKER_FUNCTION_ENV)
    if name:
        return LambdaRunner(name)
    return InProcessRunner()


_segment_runner: SegmentRunner | None = None


def get_segment_runner() -> SegmentRunner:
    """Process-wide singleton runner (FastAPI dependency). Tests override it."""
    global _segment_runner
    if _segment_runner is None:
        _segment_runner = make_segment_runner()
    return _segment_runner


async def run_worker_segment(run_id: str, *, client: str | None) -> RunState:
    """Rebuild the agent context from the stored run and drive one segment.

    The worker entry point (main.handler's worker branch) and any out-of-band
    resume use this. It pulls the process singletons directly — there is no
    HTTP request to inject dependencies from.
    """
    store = get_run_store()
    state = await store.get(run_id)
    if state is None:
        raise ValueError(f"Unknown run: {run_id}")
    ctx = build_agent_context(
        ideas=ideas_source_for_state(state),
        store=store,
        audit=get_audit_logger().for_client(client),
        llm=get_llm_client(),
        access=AccessContext(tenant_id=state.tenant_id, role="owner"),
        public_source=get_public_source(),
    )
    return await run_until_gate(run_id=run_id, ctx=ctx)
