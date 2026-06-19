"""FastAPI application entry point.

Run via uvicorn locally; on AWS Lambda the same `app` is served through the
`handler` below (Mangum adapts the ASGI app to the Lambda event model).

`.env` is loaded at import time so `ANTHROPIC_API_KEY` (and any future
configuration env vars) is available before dependency providers run.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Coroutine
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from mangum import Mangum

from gar_backend.api import gates, runs, stream
from gar_backend.api.deps import get_access_context
from gar_backend.api.segments import WORKER_EVENT_KEY, run_worker_segment

# Searches cwd and parents for `.env`; harmless if not present.
load_dotenv()


app = FastAPI(title="gar-backend", version="0.1.0")

# Every run/gate/stream route is gated by Cognito-token verification (no-op
# when no pool is configured — local/dev). Resolving the AccessContext here
# means an unauthenticated request is rejected before any handler runs.
# /healthz below stays open for load-balancer checks.
_gated = [Depends(get_access_context)]
app.include_router(runs.router, dependencies=_gated)
app.include_router(gates.router, dependencies=_gated)
app.include_router(stream.router, dependencies=_gated)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# AWS Lambda entry point. Two kinds of event reach this function:
# - a Function URL HTTP event → Mangum adapts it to the ASGI app;
# - an async self-invoke worker event ({WORKER_EVENT_KEY: {...}}) → run one
#   agent segment to its next gate with the full Lambda timeout (the HTTP
#   request that scheduled it has already returned). See api/segments.py.
_asgi_handler = Mangum(app)


def _run_isolated(coro: Coroutine[Any, Any, Any]) -> Any:
    """Run a worker coroutine to completion on a private event loop in a
    separate thread.

    Calling ``asyncio.run`` on the main thread would set its current event
    loop to ``None`` on exit; Mangum reuses the main thread's loop (via the
    deprecated ``get_event_loop()``) across warm invocations, so the next HTTP
    request on the same container would fail with "no current event loop".
    Isolating the worker in its own thread leaves Mangum's loop untouched.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def handler(event: Any, context: Any) -> Any:
    if isinstance(event, dict) and WORKER_EVENT_KEY in event:
        job = event[WORKER_EVENT_KEY]
        run_id = job["run_id"]
        state = _run_isolated(run_worker_segment(run_id, client=job.get("client")))
        return {"ok": True, "run_id": run_id, "status": state.status.value}
    return _asgi_handler(event, context)
