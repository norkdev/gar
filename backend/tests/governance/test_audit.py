"""Audit logger / file sink unit tests."""

import json
from datetime import UTC, datetime
from pathlib import Path

from gar_backend.governance.audit import (
    SCHEMA_VERSION,
    AuditLogger,
    AuditRecord,
    FileAuditSink,
)


def test_audit_record_defaults_status_to_ok() -> None:
    record = AuditRecord(
        run_id="r1",
        tenant_id="default",
        tool_name="x",
        input={},
    )
    assert record.status == "ok"
    assert record.output is None
    assert record.error is None
    assert record.duration_ms is None


def test_audit_record_timestamp_defaults_to_now_in_utc() -> None:
    before = datetime.now(UTC)
    record = AuditRecord(
        run_id="r1",
        tenant_id="default",
        tool_name="x",
        input={},
    )
    after = datetime.now(UTC)
    assert before <= record.timestamp <= after
    assert record.timestamp.tzinfo is not None


def test_file_audit_sink_writes_one_jsonl_line_per_record(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    sink = FileAuditSink(path)
    logger = AuditLogger(sink)

    logger.log(
        AuditRecord(
            run_id="r1",
            tenant_id="default",
            tool_name="public_src.search",
            input={"query": "graphene"},
            output={"count": 3},
            duration_ms=42.0,
        )
    )

    lines = path.read_text().splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["tool_name"] == "public_src.search"
    assert payload["input"] == {"query": "graphene"}
    assert payload["output"] == {"count": 3}
    assert payload["duration_ms"] == 42.0


def test_file_audit_sink_appends_multiple_records(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    sink = FileAuditSink(path)
    logger = AuditLogger(sink)

    for i in range(3):
        logger.log(
            AuditRecord(
                run_id=f"r{i}",
                tenant_id="default",
                tool_name="x",
                input={},
                output={},
            )
        )

    lines = path.read_text().splitlines()
    assert [json.loads(line)["run_id"] for line in lines] == ["r0", "r1", "r2"]


def test_logger_records_error_status_and_message(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(FileAuditSink(path))

    logger.log(
        AuditRecord(
            run_id="r1",
            tenant_id="default",
            tool_name="public_src.search",
            input={"query": "x"},
            output=None,
            status="error",
            error="HTTPError: 503",
        )
    )

    payload = json.loads(path.read_text())
    assert payload["status"] == "error"
    assert payload["error"] == "HTTPError: 503"
    assert payload["output"] is None


def test_logger_timestamp_serializes_to_iso_8601(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(FileAuditSink(path))
    logger.log(
        AuditRecord(
            run_id="r1",
            tenant_id="default",
            tool_name="x",
            input={},
        )
    )
    payload = json.loads(path.read_text())
    parsed = datetime.fromisoformat(payload["timestamp"])
    assert parsed.tzinfo is not None


def test_every_record_carries_schema_version(tmp_path: Path) -> None:
    """Spec §10 seam #6: every record must carry schema_version."""
    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(FileAuditSink(path))
    logger.log(
        AuditRecord(
            run_id="r1",
            tenant_id="default",
            tool_name="x",
            input={},
        )
    )
    payload = json.loads(path.read_text())
    assert payload["schema_version"] == SCHEMA_VERSION


def test_logger_coerces_unknown_types_to_string(tmp_path: Path) -> None:
    """Non-JSON-serializable values must not crash the logger."""
    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(FileAuditSink(path))

    logger.log(
        AuditRecord(
            run_id="r1",
            tenant_id="default",
            tool_name="x",
            input={"opaque": object()},
        )
    )

    payload = json.loads(path.read_text())
    assert "opaque" in payload["input"]
    assert isinstance(payload["input"]["opaque"], str)


def test_tenant_id_field_is_persisted(tmp_path: Path) -> None:
    """Spec §10 seam #1: every record must carry tenant_id."""
    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(FileAuditSink(path))
    logger.log(
        AuditRecord(
            run_id="r1",
            tenant_id="acme-corp",
            tool_name="x",
            input={},
        )
    )
    payload = json.loads(path.read_text())
    assert payload["tenant_id"] == "acme-corp"
