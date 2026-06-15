"""Recall@K metrics — the instrument for evaluating retrieval/rerank (spec §14).

GAR's purpose (judging an idea's novelty against the literature) makes recall
the priority: missing a relevant prior work yields a false "novel". The right
measure is therefore not corpus-wide F1 but **recall@K** — did the relevant
work land within the top-K a human will actually read — together with a
known-item check for the decisive papers that must not be missed.

These are pure functions over an ordered list of candidate ids. A run produces
that order (search → rerank); a labeled relevant set comes from a seeded
evaluation concept. Operates on ids so it is independent of candidate shape.
"""

from __future__ import annotations

from collections.abc import Iterable


def recall_at_k(ranked_ids: list[str], relevant_ids: Iterable[str], k: int) -> float:
    """Fraction of the relevant set that appears in the top-K of ``ranked_ids``.

    Returns 1.0 when there is nothing to find (empty relevant set) and 0.0 for
    a non-positive K.
    """
    relevant = set(relevant_ids)
    if not relevant:
        return 1.0
    if k <= 0:
        return 0.0
    hits = sum(1 for rid in ranked_ids[:k] if rid in relevant)
    return hits / len(relevant)


def rank_of(ranked_ids: list[str], target_id: str) -> int | None:
    """1-based position of ``target_id`` in the ranking, or None if absent."""
    for index, rid in enumerate(ranked_ids):
        if rid == target_id:
            return index + 1
    return None


def known_item_recall(
    ranked_ids: list[str], known_ids: Iterable[str], k: int
) -> dict[str, bool]:
    """Per decisive paper: is it within the top-K? The novelty-killer check —
    these are the items whose omission would wrongly imply novelty."""
    topk = set(ranked_ids[:k]) if k > 0 else set()
    return {kid: kid in topk for kid in known_ids}
