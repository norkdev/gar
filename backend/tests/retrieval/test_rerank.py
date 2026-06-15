"""BM25 reranker tests."""

from typing import Any

from gar_backend.retrieval.rerank import BM25Reranker


def _cand(ext_id: str, title: str, snippet: str = "") -> dict[str, Any]:
    return {
        "source_name": "arxiv",
        "external_id": ext_id,
        "title": title,
        "snippet": snippet,
    }


def test_empty_pool_returns_empty() -> None:
    assert BM25Reranker().rank("anything", []) == []


def test_relevant_candidate_ranked_above_irrelevant() -> None:
    cands = [
        _cand("noise", "Sourdough bread recipes", "how to bake bread at home"),
        _cand(
            "hit",
            "Decentralized multi-agent profile matching",
            "agents share sub-profiles and compute confidence scores",
        ),
    ]
    ranked = BM25Reranker().rank(
        "decentralized multi-agent profile confidence matching", cands
    )
    assert ranked[0]["external_id"] == "hit"


def test_more_query_matches_ranks_higher() -> None:
    cands = [
        _cand("low", "Gossip protocol overview", "a gossip protocol"),
        _cand(
            "high",
            "Gossip federated privacy profile sharing",
            "gossip federated privacy profile sharing among agents",
        ),
    ]
    ranked = BM25Reranker().rank("gossip federated privacy profile sharing", cands)
    assert ranked[0]["external_id"] == "high"


def test_no_matching_terms_preserves_input_order() -> None:
    """A no-signal rerank is a no-op (stable sort), so order-sensitive callers
    aren't disturbed when nothing matches."""
    cands = [_cand("a", "Alpha", "x"), _cand("b", "Beta", "y"), _cand("c", "Z", "w")]
    ranked = BM25Reranker().rank("quantum chromodynamics", cands)
    assert [c["external_id"] for c in ranked] == ["a", "b", "c"]


def test_rank_does_not_drop_or_duplicate_candidates() -> None:
    cands = [
        _cand(str(i), f"title {i}", f"abstract about agents {i}") for i in range(10)
    ]
    ranked = BM25Reranker().rank("agents", cands)
    assert sorted(c["external_id"] for c in ranked) == sorted(
        c["external_id"] for c in cands
    )
    assert len(ranked) == len(cands)
