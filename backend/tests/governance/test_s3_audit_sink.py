"""S3AuditSink tests — fully offline via moto (no real AWS)."""

import json
from typing import Any

import boto3
import pytest
from gar_backend.governance.audit import AuditLogger, AuditRecord, S3AuditSink
from moto import mock_aws

BUCKET = "gar-state"


@pytest.fixture
def s3() -> Any:
    with mock_aws():
        client = boto3.client("s3", region_name="ap-northeast-1")
        client.create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": "ap-northeast-1"},
        )
        yield client


def _objects(s3: Any, prefix: str = "audit/") -> list[dict[str, Any]]:
    resp = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    out = []
    for obj in resp.get("Contents", []):
        body = s3.get_object(Bucket=BUCKET, Key=obj["Key"])["Body"].read()
        out.append({"key": obj["Key"], "payload": json.loads(body)})
    return out


def test_writes_one_object_per_record(s3: Any) -> None:
    sink = S3AuditSink(BUCKET, s3=s3)
    sink.write({"run_id": "r1", "tenant_id": "default", "tool_name": "a"})
    sink.write({"run_id": "r1", "tenant_id": "default", "tool_name": "b"})

    objs = _objects(s3)
    assert len(objs) == 2
    assert all(o["key"].startswith("audit/default/r1/") for o in objs)
    assert {o["payload"]["tool_name"] for o in objs} == {"a", "b"}


def test_keys_are_unique_for_same_timestamp(s3: Any) -> None:
    sink = S3AuditSink(BUCKET, s3=s3)
    payload = {
        "run_id": "r1",
        "tenant_id": "default",
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    for _ in range(5):
        sink.write(dict(payload))
    # Same timestamp, but the seq counter keeps every object distinct (no
    # record silently overwrites another).
    assert len(_objects(s3)) == 5


def test_separate_runs_go_to_separate_prefixes(s3: Any) -> None:
    sink = S3AuditSink(BUCKET, s3=s3)
    sink.write({"run_id": "r1", "tenant_id": "t1"})
    sink.write({"run_id": "r2", "tenant_id": "t2"})
    assert len(_objects(s3, "audit/t1/r1/")) == 1
    assert len(_objects(s3, "audit/t2/r2/")) == 1


def test_through_audit_logger_stamps_schema_and_persists(s3: Any) -> None:
    logger = AuditLogger(S3AuditSink(BUCKET, s3=s3)).for_client("web")
    logger.log(
        AuditRecord(
            run_id="r1",
            tenant_id="default",
            tool_name="search_public",
            input={"q": "x"},
        )
    )
    (obj,) = _objects(s3)
    assert obj["payload"]["schema_version"]
    assert obj["payload"]["client"] == "web"
    assert obj["payload"]["tool_name"] == "search_public"
