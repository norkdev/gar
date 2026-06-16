"""Embedding reranker + client tests (v1.3 slice 2). Offline via MockTransport."""

import json
from typing import Any

import httpx
import pytest
from gar_backend.retrieval.embedding import (
    EmbeddingClient,
    EmbeddingError,
    EmbeddingReranker,
    _cosine,
)
from gar_backend.retrieval.rerank import BM25Reranker, make_reranker


def _client(handler: Any, **kw: Any) -> EmbeddingClient:
    return EmbeddingClient(api_key="k", transport=httpx.MockTransport(handler), **kw)


def _embed_response(vectors: list[list[float]]) -> httpx.Response:
    # Return out of order to exercise the index-based reordering.
    data = [{"embedding": vec, "index": i} for i, vec in enumerate(vectors)]
    return httpx.Response(200, json={"object": "list", "data": list(reversed(data))})


# ---------- EmbeddingClient ----------


def test_embed_posts_model_input_and_type() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _embed_response([[1.0, 0.0]])

    client = _client(handler)
    out = client.embed(["hello"], input_type="document")
    body = json.loads(seen[0].content)
    assert body["input"] == ["hello"]
    assert body["input_type"] == "document"
    assert "model" in body
    assert out == [[1.0, 0.0]]
    client.close()


def test_embed_restores_input_order_by_index() -> None:
    client = _client(lambda r: _embed_response([[1.0], [2.0], [3.0]]))
    out = client.embed(["a", "b", "c"], input_type="document")
    assert out == [[1.0], [2.0], [3.0]]  # despite the reversed response
    client.close()


def test_embed_batches_large_inputs() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        batch = json.loads(request.content)["input"]
        calls.append(len(batch))
        return _embed_response([[1.0] for _ in batch])

    client = _client(handler, batch_size=2)
    out = client.embed(["a", "b", "c", "d", "e"], input_type="document")
    assert calls == [2, 2, 1]  # three batches
    assert len(out) == 5
    client.close()


def test_embed_raises_on_http_error_status() -> None:
    client = _client(lambda r: httpx.Response(401, text="bad key"))
    with pytest.raises(EmbeddingError) as ei:
        client.embed(["x"], input_type="query")
    assert "401" in str(ei.value)
    client.close()


def test_embed_raises_on_transport_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = _client(handler)
    with pytest.raises(EmbeddingError):
        client.embed(["x"], input_type="query")
    client.close()


# ---------- _cosine ----------


def test_cosine_identical_and_orthogonal() -> None:
    assert _cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0  # zero vector guarded


# ---------- EmbeddingReranker ----------


class _StubEmbeddingClient:
    """Returns preset vectors keyed by text; ``input_type`` is accepted/ignored."""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors

    def embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        return [self._vectors[t] for t in texts]


def _cand(ext_id: str, title: str, snippet: str = "") -> dict[str, Any]:
    return {
        "source_name": "arxiv",
        "external_id": ext_id,
        "title": title,
        "snippet": snippet,
    }


def test_embedding_reranker_orders_by_cosine() -> None:
    near = _cand("near", "near")
    far = _cand("far", "far")
    vectors = {
        "concept query": [1.0, 0.0],
        "near ": [0.9, 0.1],  # aligned with the query
        "far ": [0.0, 1.0],  # orthogonal
    }
    reranker = EmbeddingReranker(_StubEmbeddingClient(vectors))  # type: ignore[arg-type]
    ranked = reranker.rank("concept query", [far, near])
    assert ranked[0]["external_id"] == "near"


def test_embedding_reranker_empty_pool() -> None:
    reranker = EmbeddingReranker(_StubEmbeddingClient({}))  # type: ignore[arg-type]
    assert reranker.rank("q", []) == []


class _FailingClient:
    def embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        raise EmbeddingError("api down")


def test_embedding_reranker_falls_back_on_error() -> None:
    """If embeddings fail, the run still gets a (lexical) ranking, not a crash."""
    cands = [_cand("a", "Sourdough"), _cand("b", "Widget concept system", "widget")]
    reranker = EmbeddingReranker(_FailingClient())  # type: ignore[arg-type]
    ranked = reranker.rank("widget concept", cands)
    # Fell back to BM25: the concept-matching candidate ranks first.
    assert ranked[0]["external_id"] == "b"
    assert {c["external_id"] for c in ranked} == {"a", "b"}


# ---------- make_reranker (env selection) ----------


def test_make_reranker_defaults_to_bm25(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GAR_RERANKER", raising=False)
    assert isinstance(make_reranker(), BM25Reranker)


def test_make_reranker_embedding_without_key_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GAR_RERANKER", "embedding")
    monkeypatch.delenv("GAR_EMBED_API_KEY", raising=False)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    assert isinstance(make_reranker(), BM25Reranker)


def test_make_reranker_embedding_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAR_RERANKER", "embedding")
    monkeypatch.setenv("GAR_EMBED_API_KEY", "secret")
    reranker = make_reranker()
    assert isinstance(reranker, EmbeddingReranker)
