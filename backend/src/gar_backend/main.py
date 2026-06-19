"""FastAPI application entry point.

Run via uvicorn locally; on AWS Lambda the same `app` is served through the
`handler` below (Mangum adapts the ASGI app to the Lambda event model).

`.env` is loaded at import time so `ANTHROPIC_API_KEY` (and any future
configuration env vars) is available before dependency providers run.
"""

from __future__ import annotations

from dotenv import load_dotenv
from fastapi import FastAPI
from mangum import Mangum

from gar_backend.api import gates, runs, stream

# Searches cwd and parents for `.env`; harmless if not present.
load_dotenv()


app = FastAPI(title="gar-backend", version="0.1.0")

app.include_router(runs.router)
app.include_router(gates.router)
app.include_router(stream.router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# AWS Lambda entry point (Function URL → Mangum → this ASGI app).
handler = Mangum(app)
