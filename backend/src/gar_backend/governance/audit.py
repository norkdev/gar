"""Structured audit log (JSONL) of every tool call.

Each record carries `schema_version` (spec §10 seam #6) so the log can evolve
safely. Two sinks implement the same AuditSink Protocol: FileAuditSink (local
dev) and S3AuditSink (durable, on Lambda); selected in `api/deps.py` by env.

Sync API on purpose for v1: file writes are short and called from async tool
handlers without blocking the event loop meaningfully. The Protocol stays
sync-shaped so swapping in async sinks later is an explicit decision.
"""

from __future__ import annotations

import itertools
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

# schema_version 1.1 (was 1.0): adds the `client` field identifying which
# surface drove the run (web / cli / mcp). Backward-compatible — a field
# addition only; readers of 1.0 logs see `client` absent, readers of 1.1
# logs see it null when the surface didn't declare one.
SCHEMA_VERSION = "1.1"

# The surfaces that may drive a run. Recorded on every audit record so the
# log shows there is no shadow path — every run is attributable to a client.
KNOWN_CLIENTS = frozenset({"web", "cli", "mcp"})


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
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


class AuditSink(Protocol):
    """Pluggable destination for serialized audit records."""

    def write(self, payload: dict[str, Any]) -> None: ...


class AuditReader(Protocol):
    """A sink that can also read a run's records back, for the activity feed
    (and, later, run replay). Returns ``(total, records[since:])`` in
    chronological order — ``total`` is the full count so a polling client can
    show a running tally while fetching only records it hasn't seen yet."""

    def read_for_run(
        self, tenant_id: str, run_id: str, since: int = 0
    ) -> tuple[int, list[dict[str, Any]]]: ...


class FileAuditSink:
    """Append-only JSONL writer to a local file. Thread-safe."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()

    def write(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
        with self._lock, self._path.open("a", encoding="utf-8") as f:
            f.write(line)

    def read_for_run(
        self, tenant_id: str, run_id: str, since: int = 0
    ) -> tuple[int, list[dict[str, Any]]]:
        if not self._path.exists():
            return 0, []
        matching: list[dict[str, Any]] = []
        with self._lock, self._path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("run_id") == run_id and (
                    not tenant_id or rec.get("tenant_id") == tenant_id
                ):
                    matching.append(rec)
        return len(matching), matching[since:]


class S3AuditSink:
    """Durable append-only audit log in S3 — one immutable object per record.

    S3 has no append, and a run is split across multiple Lambda invocations by
    the HITL gates (each gate ends one invocation; approval starts the next).
    A single per-run object would therefore be *overwritten* by a later
    invocation that only holds the later records. Writing one object per record
    instead is naturally append-only: no read-modify-write, no lost segments
    across invocation boundaries, no cross-writer races.

    Keys are ``{prefix}/{tenant_id}/{run_id}/{timestamp}-{nonce}-{seq}.json``.
    ``read_for_run`` reassembles a run's log by listing the
    ``{prefix}/{tenant}/{run}/`` prefix and sorting by key: the timestamp leads
    the key, so lexicographic order is chronological (the nonce + seq only
    break ties / guarantee uniqueness). Drives the polled activity feed.

    Thread-safe. boto3 is imported lazily (the Lambda runtime provides it; it is
    excluded from the deployment bundle).
    """

    def __init__(
        self, bucket: str, *, prefix: str = "audit", s3: Any | None = None
    ) -> None:
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._s3 = s3
        self._lock = threading.Lock()
        self._counter = itertools.count()
        # Per-instance nonce disambiguates records emitted in the same
        # millisecond by different warm containers writing to the same run.
        self._nonce = uuid.uuid4().hex[:8]

    def _client(self) -> Any:
        if self._s3 is None:
            import boto3

            self._s3 = boto3.client("s3")
        return self._s3

    def write(self, payload: dict[str, Any]) -> None:
        tenant_id = payload.get("tenant_id") or "default"
        run_id = payload.get("run_id") or "unknown"
        timestamp = payload.get("timestamp") or datetime.now(UTC).isoformat()
        with self._lock:
            seq = next(self._counter)
        # Colons in the ISO timestamp are valid in S3 keys but awkward in
        # tooling; swap for a filesystem-friendly separator.
        stamp = str(timestamp).replace(":", "").replace("+", "Z")
        key = (
            f"{self._prefix}/{tenant_id}/{run_id}/{stamp}-{self._nonce}-{seq:06d}.json"
        )
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._client().put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )

    def read_for_run(
        self, tenant_id: str, run_id: str, since: int = 0
    ) -> tuple[int, list[dict[str, Any]]]:
        prefix = f"{self._prefix}/{tenant_id or 'default'}/{run_id}/"
        s3 = self._client()
        keys: list[str] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self._bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = s3.list_objects_v2(**kwargs)
            keys.extend(obj["Key"] for obj in resp.get("Contents", []))
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        keys.sort()  # timestamp leads the key → chronological
        records: list[dict[str, Any]] = []
        # Fetch only the keys past `since` — the client already has the rest.
        for key in keys[since:]:
            obj = s3.get_object(Bucket=self._bucket, Key=key)
            try:
                records.append(json.loads(obj["Body"].read()))
            except json.JSONDecodeError:
                continue
        return len(keys), records


class AuditLogger:
    """Attaches schema_version + serializes records, then pushes to a sink.

    `client` is the surface that drove the run (web / cli / mcp). It is a
    per-request attribute, not a property of any single tool call, so — like
    schema_version — it is stamped here at serialization rather than carried
    on every AuditRecord. Bind it per request with ``for_client``; the bound
    logger shares this logger's sink.
    """

    def __init__(self, sink: AuditSink, *, client: str | None = None) -> None:
        self._sink = sink
        self._client = client

    def for_client(self, client: str | None) -> AuditLogger:
        """Return a logger that stamps `client` on every record it writes,
        sharing this logger's sink. ``None`` if the surface didn't declare one."""
        return AuditLogger(self._sink, client=client)

    def log(self, record: AuditRecord) -> None:
        self._sink.write(
            {
                "schema_version": SCHEMA_VERSION,
                "timestamp": record.timestamp.isoformat(),
                "run_id": record.run_id,
                "tenant_id": record.tenant_id,
                "client": self._client,
                "tool_name": record.tool_name,
                "input": record.input,
                "output": record.output,
                "duration_ms": record.duration_ms,
                "status": record.status,
                "error": record.error,
            }
        )
