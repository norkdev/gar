"""Shared pytest fixtures for backend tests."""

import pytest


@pytest.fixture(autouse=True)
def _hermetic_reranker_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the suite hermetic w.r.t. the developer's .env.

    main.py runs ``load_dotenv()`` at import, so a real ``.env`` with
    ``GAR_RERANKER=embedding`` + a Voyage key would leak into the test process
    and make the default reranker hit the live embeddings API. Clear those vars
    for every test so the default is dependency-free BM25; tests that exercise
    embedding set the vars themselves. The GAR_MODEL_* / GAR_THOROUGH vars are
    cleared so the per-phase model policy resolves to its defaults under test.
    """
    for var in (
        "GAR_RERANKER",
        "GAR_EMBED_API_KEY",
        "VOYAGE_API_KEY",
        "GAR_EMBED_URL",
        "GAR_EMBED_MODEL",
        "GAR_DIRECTIONS_K",
        "GAR_DIRECTIONS_POOL",
        "GAR_MODEL_DERIVE",
        "GAR_MODEL_SEARCH",
        "GAR_MODEL_COMPOSE",
        "GAR_THOROUGH",
        "GAR_RUNS_TABLE",
        "GAR_STATE_BUCKET",
        "GAR_AUDIT_LOG_PATH",
        "GAR_AUDIT_BUCKET",
        "GAR_ANTHROPIC_SECRET_ARN",
        "AWS_LAMBDA_FUNCTION_NAME",
        "GAR_API_KEY",
        "GAR_API_KEY_SECRET_ARN",
    ):
        monkeypatch.delenv(var, raising=False)
