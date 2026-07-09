"""Semantic near-duplicate clustering of proposals (gov-polish-cluster).

Unit-level checks on the greedy clustering math; the DB/embedding path is
exercised live against the running server (embedding is deterministic local
MiniLM, no external calls).
"""
import numpy as np

from app.services.proposal_dedup import MAX_PROPOSALS, greedy_clusters


def _unit(v):
    a = np.asarray(v, dtype=np.float32)
    return a / np.linalg.norm(a)


def test_near_duplicates_cluster_and_distinct_stay_out():
    # Two near-identical directions, one orthogonal
    matrix = np.stack([
        _unit([1.0, 0.0, 0.01]),
        _unit([1.0, 0.0, 0.03]),
        _unit([0.0, 1.0, 0.0]),
    ])
    clusters = greedy_clusters([10, 11, 12], matrix, threshold=0.88)
    assert clusters == [[10, 11]], "near-duplicates cluster; orthogonal stays out"


def test_singletons_are_not_reported():
    matrix = np.stack([_unit([1, 0, 0]), _unit([0, 1, 0]), _unit([0, 0, 1])])
    assert greedy_clusters([1, 2, 3], matrix, threshold=0.88) == []


def test_seed_anchored_no_transitive_chaining():
    """A cluster is 'everything similar to the SEED' — b joins a's cluster,
    and c (similar to b but not to a) must not chain in."""
    import math
    # a=(1,0); b at 25deg (cos to a ≈ .906, joins); c at 50deg (cos to a ≈ .643
    # — out; but cos to b ≈ .906 — would chain if clustering were transitive)
    a = _unit([1.0, 0.0])
    b = _unit([math.cos(math.radians(25)), math.sin(math.radians(25))])
    c = _unit([math.cos(math.radians(50)), math.sin(math.radians(50))])
    matrix = np.stack([a, b, c])
    clusters = greedy_clusters([1, 2, 3], matrix, threshold=0.88)
    assert clusters == [[1, 2]], f"c must not chain through b: {clusters}"


def test_members_are_claimed_once():
    # Three mutually similar: one cluster, no reuse in later clusters
    base = [_unit([1.0, 0.02 * i]) for i in range(3)]
    clusters = greedy_clusters([7, 8, 9], np.stack(base), threshold=0.95)
    assert clusters == [[7, 8, 9]]
    flat = [m for c in clusters for m in c]
    assert len(flat) == len(set(flat)), "no proposal may appear in two clusters"


def test_request_work_is_bounded():
    assert 0 < MAX_PROPOSALS <= 5000, "per-request clustering must stay bounded (JPL-2)"
