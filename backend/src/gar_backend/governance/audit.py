"""Structured audit log (JSONL) of every tool call.

Each record carries `schema_version` (spec §10 seam #6) so the log can evolve
safely. v1: writes to a local file via FileAuditSink. Phase 1+: pluggable
sinks (S3, CloudWatch) implementing the same AuditSink Protocol.

Sync API on purpose for v1: file writes are short and called from async tool
handlers without blocking the event loop meaningfully. The Protocol stays
sync-shaped so swapping in async sinks later is an explicit decision.
"""

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class AuditRecord:
    """One tool-call entry in the audit log."""

    run_id: str
    tenant_id: str
    tool_name: str
    input: dict[str, Any]
    output: dict[str, Any] | None = None
    duration_ms: float | None = None
    status: str = "ok"
    error: str | None = None
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class AuditSink(Protocol):
    """Pluggable destination for serialized audit records."""

    def write(self, payload: dict[str, Any]) -> None: ...


class FileAuditSink:
    """Append-only JSONL writer to a local file. Thread-safe."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def write(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(line)


class AuditLogger:
    """Attaches schema_version + serializes records, then pushes to a sink."""

    def __init__(self, sink: AuditSink) -> None:
        self._sink = sink

    def log(self, record: AuditRecord) -> None:
        self._sink.write({
            "schema_version": SCHEMA_VERSION,
            "timestamp": record.timestamp.isoformat(),
            "run_id": record.run_id,
            "tenant_id": record.tenant_id,
            "tool_name": record.tool_name,
            "input": record.input,
            "output": record.output,
            "duration_ms": record.duration_ms,
            "status": record.status,
            "error": record.error,
        })
