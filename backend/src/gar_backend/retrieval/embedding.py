"""Embedding-based reranker over an external embeddings API (v1.3 slice 2).

The lexical signals (BM25, cross-query support) reward vocabulary overlap, so
they surface generic, high-frequency-term papers and bury relevant work phrased
differently (measured in slices 1's live run). A semantic reranker scores by
*meaning*, not term overlap, fixing that bias.

Why an external API, not a local model: it needs no heavy dependency (just the
httpx already in use), stays Lambda-friendly for the AWS migration, and gives
high quality. The concept it embeds already goes to the LLM provider, so a
second trusted-compute provider is a small marginal exposure — and the whole
reranker is opt-in (BM25 stays the dependency-free default; see make_reranker).

Defaults target Voyage AI (Anthropic's recommended embeddings provider), but
base_url / model are configurable, and the request/response shape is the common
OpenAI-style ``data[].embedding`` so other providers work too.
"""

from __future__ import annotations

import math
from typing import Any

import httpx

from gar_backend.retrieval.rerank import BM25Reranker, Reranker, _candidate_text

DEFAULT_EMBED_URL = "https://api.voyageai.com/v1/embeddings"
DEFAULT_EMBED_MODEL = "voyage-3.5"
# Per-request text cap. Providers limit batch size; 100 is well within Voyage's.
EMBED_BATCH = 100
EMBED_TIMEOUT_SEC = 60.0


class EmbeddingError(Exception):
    """The embeddings API was unreachable or returned an error. The reranker
    catches this and falls back to lexical ranking rather than failing the run."""


class EmbeddingClient:
    """Sync client over an OpenAI-style embeddings endpoint.

    Sync on purpose: the Reranker Protocol is sync (called once per search
    phase). ``transport`` is injectable so tests drive it with
    ``httpx.MockTransport`` — no live API or key needed.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_EMBED_MODEL,
        base_url: str = DEFAULT_EMBED_URL,
        transport: httpx.BaseTransport | None = None,
        batch_size: int = EMBED_BATCH,
    ) -> None:
        self._model = model
        self._url = base_url
        self._batch = batch_size
        self._client = httpx.Client(
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            transport=transport,
            timeout=EMBED_TIMEOUT_SEC,
        )

    def close(self) -> None:
        self._client.close()

    def embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        """Embed ``texts`` (batched). ``input_type`` is ``query`` or ``document``
        for asymmetric retrieval (Voyage); providers that ignore it are fine."""
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch):
            vectors.extend(
                self._embed_batch(texts[start : start + self._batch], input_type)
            )
        return vectors

    def _embed_batch(self, batch: list[str], input_type: str) -> list[list[float]]:
        try:
            resp = self._client.post(
                self._url,
                json={"model": self._model, "input": batch, "input_type": input_type},
            )
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"embedding request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise EmbeddingError(
                f"embedding API returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
        data = resp.json().get("data", [])
        # The API may return embeddings out of order; restore input order by index.
        ordered = sorted(data, key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in ordered]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class EmbeddingReranker:
    """Order candidates by cosine similarity between the concept and each
    candidate's title+abstract embedding. Falls back to a lexical reranker if
    the embeddings API errors, so a rerank failure never fails the run."""

    def __init__(
        self, client: EmbeddingClient, *, fallback: Reranker | None = None
    ) -> None:
        self._client = client
        self._fallback = fallback or BM25Reranker()

    def rank(
        self, query: str, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not candidates:
            return []
        try:
            doc_vecs = self._client.embed(
                [_candidate_text(c) for c in candidates], input_type="document"
            )
            query_vec = self._client.embed([query], input_type="query")[0]
        except EmbeddingError:
            return self._fallback.rank(query, candidates)
        scores = [_cosine(query_vec, doc) for doc in doc_vecs]
        order = sorted(range(len(candidates)), key=lambda i: -scores[i])
        return [candidates[i] for i in order]
