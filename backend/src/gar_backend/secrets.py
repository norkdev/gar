"""Resolve runtime credentials from the environment or AWS Secrets Manager.

Local dev keeps ``ANTHROPIC_API_KEY`` in ``.env`` (loaded at import). On
Lambda the key must not live in an env var or the image, so the function is
deployed with ``GAR_ANTHROPIC_SECRET_ARN`` pointing at a Secrets Manager
secret; the value is fetched once at cold start (see ``deps.get_llm_client``).

The secret value may be the raw key or a JSON object carrying it under
``ANTHROPIC_API_KEY`` — both shapes are accepted so the secret can be managed
either way without a code change.
"""

from __future__ import annotations

import json
import os

# Env var holding the Secrets Manager ARN (set by the BackendStack). When the
# key is already present in the environment (local dev), the secret is not
# consulted at all.
SECRET_ARN_ENV = "GAR_ANTHROPIC_SECRET_ARN"
API_KEY_ENV = "ANTHROPIC_API_KEY"


def resolve_anthropic_api_key() -> str | None:
    """Return the Anthropic API key, or None to defer to the SDK's own lookup.

    Precedence: an explicit ``ANTHROPIC_API_KEY`` (local dev / tests) wins, so
    nothing hits AWS off-Lambda. Otherwise, if ``GAR_ANTHROPIC_SECRET_ARN`` is
    set, fetch the secret. None means "no override" — ``AsyncAnthropic()`` will
    do its usual env lookup and raise a clear error if the key is truly absent.
    """
    key = os.environ.get(API_KEY_ENV)
    if key:
        return key

    arn = os.environ.get(SECRET_ARN_ENV)
    if not arn:
        return None

    # Lazy import: boto3 is provided by the Lambda runtime and excluded from the
    # deployment bundle, so importing it at module load would slow cold starts
    # and pointlessly couple non-AWS code paths to boto3.
    import boto3

    client = boto3.client("secretsmanager")
    secret = client.get_secret_value(SecretId=arn)["SecretString"]
    return _extract_key(secret)


def _extract_key(secret: str) -> str:
    """Accept either a raw key or a JSON object carrying it."""
    secret = secret.strip()
    if secret.startswith("{"):
        try:
            data = json.loads(secret)
        except json.JSONDecodeError:
            return secret
        value = data.get(API_KEY_ENV)
        if isinstance(value, str) and value:
            return value
    return secret
