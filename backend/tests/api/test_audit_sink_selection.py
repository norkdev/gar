"""get_audit_logger sink selection (construction only — no AWS calls)."""

import pytest
from gar_backend.api import deps
from gar_backend.governance.audit import FileAuditSink, S3AuditSink


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    # Both the logger and the sink memoize in module globals; clear both so
    # each test re-runs the selection logic.
    monkeypatch.setattr(deps, "_audit_logger", None)
    monkeypatch.setattr(deps, "_audit_sink", None)


def test_defaults_to_file_sink() -> None:
    # conftest clears GAR_AUDIT_BUCKET → nothing configured → local file.
    assert isinstance(deps.get_audit_logger()._sink, FileAuditSink)


def test_selects_s3_when_bucket_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAR_AUDIT_BUCKET", "gar-state")
    sink = deps.get_audit_logger()._sink
    assert isinstance(sink, S3AuditSink)
