"""recall@K metric tests, plus a demonstration that rerank lifts recall@K."""

from typing import Any

from gar_backend.retrieval.recall import known_item_recall, rank_of, recall_at_k
from gar_backend.retrieval.rerank import BM25Reranker


def test_recall_at_k_partial() -> None:
    ranked = ["a", "b", "c", "d"]
    assert recall_at_k(ranked, {"a", "d"}, 2) == 0.5  # only "a" in top 2
    assert recall_at_k(ranked, {"a", "b"}, 2) == 1.0


def test_recall_at_k_empty_relevant_is_one() -> None:
    assert recall_at_k(["a"], set(), 5) == 1.0


def test_recall_at_k_nonpositive_k_is_zero() -> None:
    assert recall_at_k(["a"], {"a"}, 0) == 0.0


def test_rank_of() -> None:
    assert rank_of(["a", "b", "c"], "b") == 2
    assert rank_of(["a"], "missing") is None


def test_known_item_recall_flags_each_decisive_paper() -> None:
    result = known_item_recall(["a", "b", "c"], ["b", "z"], k=2)
    assert result == {"b": True, "z": False}


def _cand(ext_id: str, title: str, snippet: str) -> dict[str, Any]:
    return {
        "source_name": "arxiv",
        "external_id": ext_id,
        "title": title,
        "snippet": snippet,
    }


def test_rerank_lifts_recall_at_k() -> None:
    """The point of rerank: arXiv returns results in no relevance order, so a
    decisive paper can sit at the bottom. Measured with recall@K, rerank lifts
    the buried relevant work into the top-K a human will read."""
    query = (
        "decentralized multi-agent privacy-preserving user profile sharing "
        "with confidence scoring"
    )
    # Relevant papers planted at the BOTTOM, mimicking non-relevance ordering.
    noise = [
        _cand(f"n{i}", "An unrelated study", "lorem ipsum dolor sit amet")
        for i in range(8)
    ]
    relevant = [
        _cand(
            "R1",
            "Privacy-preserving user profile sharing",
            "decentralized sharing of user profile data with consent",
        ),
        _cand(
            "R2",
            "Confidence-scored multi-agent cooperation",
            "agents exchange profiles and compute confidence scores",
        ),
    ]
    pool = noise + relevant
    relevant_ids = {"R1", "R2"}

    before = recall_at_k([c["external_id"] for c in pool], relevant_ids, k=5)
    ranked = BM25Reranker().rank(query, pool)
    after = recall_at_k([c["external_id"] for c in ranked], relevant_ids, k=5)

    assert before == 0.0  # buried below the top 5
    assert after == 1.0  # rerank pulls both into the top 5
