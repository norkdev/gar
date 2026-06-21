"""Resolve runtime credentials from the environment or AWS Secrets Manager.

Local dev keeps secrets in ``.env`` (loaded at import). On Lambda they must not
live in an env var or the image, so the function is deployed with a Secrets
Manager ARN per credential; the value is fetched once at first use.

A secret value may be the raw string or a JSON object carrying it under a named
key — both shapes are accepted so a secret can be managed either way without a
code change.
"""

from __future__ import annotations

import json
import os

# Anthropic key: explicit env wins (local/tests), else this Secrets Manager ARN.
ANTHROPIC_SECRET_ARN_ENV = "GAR_ANTHROPIC_SECRET_ARN"
ANTHROPIC_API_KEY_ENV = "ANTHROPIC_API_KEY"

# Embedding (Voyage) key for the opt-in semantic reranker. retrieval/rerank
# reads VOYAGE_API_KEY / GAR_EMBED_API_KEY from the env; on Lambda the value
# comes from this Secrets Manager ARN and is hydrated into the env at startup.
EMBED_SECRET_ARN_ENV = "GAR_EMBED_SECRET_ARN"


def resolve_anthropic_api_key() -> str | None:
    """The Anthropic API key, or None to defer to the SDK's own env lookup."""
    return _resolve_secret(
        env_var=ANTHROPIC_API_KEY_ENV,
        arn_var=ANTHROPIC_SECRET_ARN_ENV,
        json_key=ANTHROPIC_API_KEY_ENV,
    )


def resolve_embed_key() -> str | None:
    """The embedding (Voyage) API key: either env vars the reranker already
    reads, or the Secrets Manager secret. None when not configured."""
    env = os.environ.get("GAR_EMBED_API_KEY") or os.environ.get("VOYAGE_API_KEY")
    if env:
        return env
    return _resolve_secret(env_var="", arn_var=EMBED_SECRET_ARN_ENV)


def hydrate_embed_key() -> None:
    """If an embedding key is resolvable from a secret, put it in the env so
    ``retrieval.rerank.make_reranker`` (which reads VOYAGE_API_KEY) finds it.
    No-op when the key is already in the env (local dev) or unconfigured."""
    if os.environ.get("VOYAGE_API_KEY") or os.environ.get("GAR_EMBED_API_KEY"):
        return
    key = resolve_embed_key()
    if key:
        os.environ["VOYAGE_API_KEY"] = key


def _resolve_secret(
    *, env_var: str, arn_var: str, json_key: str | None = None
) -> str | None:
    """Env var wins (so nothing hits AWS off-Lambda); else fetch the secret at
    ``arn_var`` from Secrets Manager. None means "not configured"."""
    value = os.environ.get(env_var)
    if value:
        return value

    arn = os.environ.get(arn_var)
    if not arn:
        return None

    # Lazy import: boto3 is provided by the Lambda runtime and excluded from the
    # deployment bundle, so importing it at module load would slow cold starts.
    import boto3

    client = boto3.client("secretsmanager")
    secret = client.get_secret_value(SecretId=arn)["SecretString"]
    return _extract(secret, json_key)


def _extract(secret: str, json_key: str | None) -> str:
    """Accept either a raw secret or a JSON object carrying it under ``json_key``."""
    secret = secret.strip()
    if json_key and secret.startswith("{"):
        try:
            data = json.loads(secret)
        except json.JSONDecodeError:
            return secret
        value = data.get(json_key)
        if isinstance(value, str) and value:
            return value
    return secret
