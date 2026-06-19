"""FastAPI application entry point.

Run via uvicorn locally; on AWS Lambda the same `app` is served through the
`handler` below (Mangum adapts the ASGI app to the Lambda event model).

`.env` is loaded at import time so `ANTHROPIC_API_KEY` (and any future
configuration env vars) is available before dependency providers run.
"""

from __future__ import annotations

import asyncio
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from mangum import Mangum

from gar_backend.api import gates, runs, stream
from gar_backend.api.segments import WORKER_EVENT_KEY, run_worker_segment

# Searches cwd and parents for `.env`; harmless if not present.
load_dotenv()


app = FastAPI(title="gar-backend", version="0.1.0")

app.include_router(runs.router)
app.include_router(gates.router)
app.include_router(stream.router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# AWS Lambda entry point. Two kinds of event reach this function:
# - a Function URL HTTP event → Mangum adapts it to the ASGI app;
# - an async self-invoke worker event ({WORKER_EVENT_KEY: {...}}) → run one
#   agent segment to its next gate with the full Lambda timeout (the HTTP
#   request that scheduled it has already returned). See api/segments.py.
_asgi_handler = Mangum(app)


def handler(event: Any, context: Any) -> Any:
    if isinstance(event, dict) and WORKER_EVENT_KEY in event:
        job = event[WORKER_EVENT_KEY]
        run_id = job["run_id"]
        state = asyncio.run(run_worker_segment(run_id, client=job.get("client")))
        return {"ok": True, "run_id": run_id, "status": state.status.value}
    return _asgi_handler(event, context)
