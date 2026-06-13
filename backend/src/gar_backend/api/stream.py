"""SSE endpoint streaming agent progress events to the frontend.

The endpoint tails the audit log (filtered by ``run_id``) AND polls the
RunStore for status changes. Each new audit record is emitted as an
``audit`` event; each status transition is emitted as a ``state`` event.
The stream ends with a ``done`` event when the run reaches a terminal
status or an AWAITING_* gate.

Event format follows the EventSource specification:

    event: <name>
    data: <json>
    (blank line)

The audit log is the source of truth for fine-grained activity (each LLM
call and tool dispatch). Polling RunStore catches the coarser phase
transitions (DERIVING_CONCEPT → SEARCHING → ...). Combining both gives the
frontend enough material to show meaningful progress without instrumenting
the agent loop with a separate event sink.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from gar_backend.api.deps import get_audit_log_path, get_run_store
from gar_backend.api.runs import serialize_state
from gar_backend.governance.hitl import (
    is_awaiting_user,
    is_terminal,
)
from gar_backend.state.runs import RunStore

router = APIRouter(prefix="/runs/{run_id}/events", tags=["stream"])


# Poll interval for audit log + store state. Small enough that the
# frontend feels responsive, large enough not to burn CPU.
POLL_INTERVAL_SEC = 0.5

# Hard upper bound on stream lifetime. Beyond this we end with a timeout
# event; the frontend can reconnect if needed.
MAX_STREAM_ITERATIONS = 1200  # ≈ 10 minutes at 0.5 s


@router.get("")
async def stream_events(
    run_id: str,
    store: RunStore = Depends(get_run_store),
    audit_path: Path = Depends(get_audit_log_path),
) -> StreamingResponse:
    state = await store.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    async def event_generator() -> AsyncIterator[bytes]:
        yield _sse("state", serialize_state(state))

        # Start at byte 0 so a freshly-connected client receives the full
        # audit history for this run (LLM calls, tool dispatches, grounding
        # checks). On subsequent polls only the new tail is read.
        offset = 0
        last_status = state.status

        for _ in range(MAX_STREAM_ITERATIONS):
            await asyncio.sleep(POLL_INTERVAL_SEC)

            offset, new_records = _read_audit_since(audit_path, offset, run_id)
            for rec in new_records:
                yield _sse("audit", rec)

            current = await store.get(run_id)
            if current is None:
                yield _sse(
                    "error",
                    {"detail": f"Run {run_id} disappeared from store"},
                )
                return
            if current.status != last_status:
                yield _sse("state", serialize_state(current))
                last_status = current.status

            if is_terminal(current) or is_awaiting_user(current):
                # Drain any final audit records emitted just before the gate.
                offset, final_records = _read_audit_since(audit_path, offset, run_id)
                for rec in final_records:
                    yield _sse("audit", rec)
                yield _sse("done", {"status": current.status.value})
                return

        yield _sse("timeout", {"detail": "stream lifetime exceeded"})

    return StreamingResponse(event_generator(), media_type="text/event-stream")


def _sse(event: str, data: Any) -> bytes:
    """Encode one SSE message. Newlines in `data` (if it stringifies to one)
    are not specially escaped — `json.dumps` always emits single-line JSON."""
    payload = json.dumps(data, default=str)
    return f"event: {event}\ndata: {payload}\n\n".encode()


def _read_audit_since(
    path: Path, offset: int, run_id: str
) -> tuple[int, list[dict[str, Any]]]:
    """Return (new_byte_offset, records_for_run) from `path` past `offset`.

    Tolerates a truncated final line at the end of the read (race with the
    writer): such a line is skipped on this pass and re-read next pass
    when more bytes have arrived.
    """
    if not path.exists():
        return offset, []
    with path.open("rb") as f:
        f.seek(offset)
        content = f.read()
        new_offset = f.tell()
    if not content.endswith(b"\n"):
        # Roll back to the last complete newline so we don't lose a
        # partial line.
        last_nl = content.rfind(b"\n")
        if last_nl == -1:
            return offset, []  # nothing complete yet
        new_offset = offset + last_nl + 1
        content = content[: last_nl + 1]
    records: list[dict[str, Any]] = []
    for line in content.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("run_id") == run_id:
            records.append(rec)
    return new_offset, records
