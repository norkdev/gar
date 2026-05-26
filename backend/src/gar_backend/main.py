"""FastAPI application entry point.

The Lambda handler (Mangum) will be wired in when deployment to AWS Lambda
is added; for v1 we run via uvicorn locally.

`.env` is loaded at import time so `ANTHROPIC_API_KEY` (and any future
configuration env vars) is available before dependency providers run.
"""

from dotenv import load_dotenv
from fastapi import FastAPI

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
