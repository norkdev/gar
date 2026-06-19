"""Secret-resolution tests — fully offline via moto (no real AWS)."""

import json
from typing import Any

import boto3
import pytest
from gar_backend.secrets import resolve_anthropic_api_key, resolve_api_key
from moto import mock_aws


@pytest.fixture(autouse=True)
def _aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GAR_ANTHROPIC_SECRET_ARN", raising=False)
    monkeypatch.delenv("GAR_API_KEY", raising=False)
    monkeypatch.delenv("GAR_API_KEY_SECRET_ARN", raising=False)


def _put_secret(value: str) -> str:
    client = boto3.client("secretsmanager", region_name="ap-northeast-1")
    arn: Any = client.create_secret(Name="gar/anthropic", SecretString=value)["ARN"]
    return arn


def test_env_key_wins_without_touching_aws(monkeypatch: pytest.MonkeyPatch) -> None:
    # No mock_aws here: if it reached Secrets Manager it would error, proving
    # the env key short-circuits the AWS path entirely.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    monkeypatch.setenv("GAR_ANTHROPIC_SECRET_ARN", "arn:aws:secretsmanager:x:y:z")
    assert resolve_anthropic_api_key() == "sk-from-env"


def test_returns_none_when_nothing_configured() -> None:
    assert resolve_anthropic_api_key() is None


def test_fetches_raw_secret_string(monkeypatch: pytest.MonkeyPatch) -> None:
    with mock_aws():
        arn = _put_secret("sk-raw-secret")
        monkeypatch.setenv("GAR_ANTHROPIC_SECRET_ARN", arn)
        assert resolve_anthropic_api_key() == "sk-raw-secret"


def test_fetches_json_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    with mock_aws():
        arn = _put_secret(json.dumps({"ANTHROPIC_API_KEY": "sk-json-secret"}))
        monkeypatch.setenv("GAR_ANTHROPIC_SECRET_ARN", arn)
        assert resolve_anthropic_api_key() == "sk-json-secret"


# --- resolve_api_key (the app boundary key) shares the same machinery ---


def test_api_key_none_when_unconfigured() -> None:
    assert resolve_api_key() is None


def test_api_key_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAR_API_KEY", "k-from-env")
    assert resolve_api_key() == "k-from-env"


def test_api_key_fetched_from_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    with mock_aws():
        client = boto3.client("secretsmanager", region_name="ap-northeast-1")
        arn = client.create_secret(Name="gar/api-key", SecretString="k-secret")["ARN"]
        monkeypatch.setenv("GAR_API_KEY_SECRET_ARN", arn)
        assert resolve_api_key() == "k-secret"
