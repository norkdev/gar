"""k-means directions clustering tests (v1.3 slice 3, part B)."""

import pytest
from gar_backend.retrieval.directions import choose_k, cluster_directions


def test_cluster_splits_separable_groups() -> None:
    docs = [[1.0, 0.0], [0.95, 0.05], [0.0, 1.0], [0.05, 0.95]]
    ids = ["a1", "a2", "b1", "b2"]
    result = cluster_directions([1.0, 0.05], docs, ids, k=2, min_cluster_size=1)
    clusters = {frozenset(d.candidate_ids) for d in result.directions}
    assert clusters == {frozenset({"a1", "a2"}), frozenset({"b1", "b2"})}


def test_concept_nearest_cluster_marked() -> None:
    docs = [[1.0, 0.0], [0.95, 0.05], [0.0, 1.0], [0.05, 0.95]]
    ids = ["a1", "a2", "b1", "b2"]
    result = cluster_directions(
        [1.0, 0.02], docs, ids, k=2, min_cluster_size=1
    )  # concept ~ A group
    concept_dir = next(d for d in result.directions if d.contains_concept)
    assert set(concept_dir.candidate_ids) == {"a1", "a2"}
    assert sum(d.contains_concept for d in result.directions) == 1


def test_representatives_are_centroid_closest() -> None:
    docs = [[1.0, 0.0], [0.7, 0.3], [0.0, 1.0]]
    ids = ["tight", "loose", "other"]
    result = cluster_directions([1.0, 0.0], docs, ids, k=2, min_cluster_size=1)
    a_dir = next(d for d in result.directions if "tight" in d.candidate_ids)
    assert a_dir.representatives[0] == "tight"  # closer to the A centroid


def test_drops_tiny_outlier_clusters() -> None:
    """An off-topic outlier lands in its own singleton cluster and is dropped as
    noise; the real cluster survives."""
    docs = [[1.0, 0.0]] * 4 + [[0.0, 1.0]]  # 4 on-theme + 1 outlier
    ids = ["a1", "a2", "a3", "a4", "noise"]
    result = cluster_directions([1.0, 0.0], docs, ids, k=2, min_cluster_size=3)
    all_ids = {i for d in result.directions for i in d.candidate_ids}
    assert "noise" not in all_ids
    assert {"a1", "a2", "a3", "a4"} <= all_ids


def test_empty_pool() -> None:
    assert cluster_directions([1.0], [], []).directions == []


def test_choose_k_heuristic() -> None:
    assert choose_k(280) == 7  # capped at 7
    assert choose_k(10) == 3  # floored at 3


def test_choose_k_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GAR_DIRECTIONS_K", "4")
    assert choose_k(280) == 4
    monkeypatch.setenv("GAR_DIRECTIONS_K", "not-a-number")
    assert choose_k(280) == 7  # falls back to heuristic
