"""Cluster the candidate pool into semantic "directions" (v1.3 slice 3, part B).

The user's positioning goal: don't just list similar papers, but show the
*directions* the literature extends in and where the idea sits among them.
Embedding clusters ground those directions semantically (vs. the lexical
support signal, which slice 1 showed surfaces generic vocabulary). The report
LLM (part A) then names each cluster and writes the positioning prose.

Dependency-free: a small deterministic k-means over unit-normalized vectors
(so Euclidean distance is monotonic in cosine). Deterministic maximin
initialization (no RNG) keeps it reproducible and testable.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

DEFAULT_REPRESENTATIVES = 5
# Clusters smaller than this are dropped from the directions map. The maximin
# seeding deliberately starts from far-apart points, so off-topic outliers
# (e.g. a stray physics paper in the pool) tend to land in tiny singleton
# clusters; those are noise, not directions.
MIN_CLUSTER_SIZE = 3
MAX_ITERATIONS = 12


@dataclass(frozen=True)
class Direction:
    """One semantic cluster of the candidate pool."""

    candidate_ids: list[str]
    # Ids closest to the cluster centroid — what the report LLM names it from.
    representatives: list[str]
    # True for the cluster the concept embedding is nearest to.
    contains_concept: bool = False


@dataclass(frozen=True)
class Directions:
    directions: list[Direction] = field(default_factory=list)


def choose_k(n: int) -> int:
    """How many directions to cut the pool into.

    A handful is what a human can hold; scale gently with pool size. Override
    with GAR_DIRECTIONS_K."""
    override = os.environ.get("GAR_DIRECTIONS_K")
    if override:
        try:
            return max(1, int(override))
        except ValueError:
            pass
    return max(3, min(7, round(n / 40)))


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]


def _sq_dist(a: list[float], b: list[float]) -> float:
    return sum((x - y) * (x - y) for x, y in zip(a, b, strict=True))


def _nearest(point: list[float], centroids: list[list[float]]) -> int:
    best_i, best_d = 0, float("inf")
    for i, c in enumerate(centroids):
        d = _sq_dist(point, c)
        if d < best_d:
            best_i, best_d = i, d
    return best_i


def _maximin_init(points: list[list[float]], k: int) -> list[list[float]]:
    """Deterministic k-means++-style seeding: start at index 0, then repeatedly
    take the point farthest from its nearest chosen centroid (ties → lower
    index). No randomness, so clustering is reproducible."""
    centroids = [points[0]]
    while len(centroids) < k:
        far_i, far_d = 0, -1.0
        for i, p in enumerate(points):
            d = min(_sq_dist(p, c) for c in centroids)
            if d > far_d:
                far_i, far_d = i, d
        centroids.append(points[far_i])
    return centroids


def cluster_directions(
    concept_vec: list[float],
    doc_vecs: list[list[float]],
    ids: list[str],
    *,
    k: int | None = None,
    representatives: int = DEFAULT_REPRESENTATIVES,
    min_cluster_size: int = MIN_CLUSTER_SIZE,
) -> Directions:
    """k-means over the document embeddings; mark the cluster nearest the
    concept; pick each cluster's centroid-closest ids as representatives.
    Clusters smaller than ``min_cluster_size`` are dropped as noise (unless the
    concept's own cluster, which is always kept)."""
    if not doc_vecs:
        return Directions()
    points = [_normalize(v) for v in doc_vecs]
    k = max(1, min(k or choose_k(len(points)), len(points)))

    centroids = _maximin_init(points, k)
    assignments = [0] * len(points)
    for _ in range(MAX_ITERATIONS):
        new_assignments = [_nearest(p, centroids) for p in points]
        if new_assignments == assignments:
            break
        assignments = new_assignments
        for ci in range(k):
            members = [points[i] for i in range(len(points)) if assignments[i] == ci]
            if not members:
                continue  # keep the old centroid for an empty cluster
            dim = len(members[0])
            centroids[ci] = _normalize(
                [sum(m[d] for m in members) / len(members) for d in range(dim)]
            )

    concept = _normalize(concept_vec)
    concept_cluster = _nearest(concept, centroids)

    directions: list[Direction] = []
    for ci in range(k):
        members = [i for i in range(len(points)) if assignments[i] == ci]
        if not members:
            continue
        is_concept = ci == concept_cluster
        if len(members) < min_cluster_size and not is_concept:
            continue  # tiny cluster = outlier noise, not a direction
        members.sort(key=lambda i: _sq_dist(points[i], centroids[ci]))
        directions.append(
            Direction(
                candidate_ids=[ids[i] for i in members],
                representatives=[ids[i] for i in members[:representatives]],
                contains_concept=is_concept,
            )
        )
    return Directions(directions=directions)
