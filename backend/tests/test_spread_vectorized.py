"""Equivalence + hop-reach tests for vectorized spread activation.

Proves the numpy scatter-max spread (_spread_neighbors_vectorized) is behaviorally
identical to the reference frontier-BFS (_spread_neighbors_python) — same promoted
neighbors and activations — and that multi-hop "neuron hopping" (3+ hops,
max-across-paths, edge-type decays) is preserved, not lost, by vectorizing.

Hermetic: loads controlled graphs into the in-memory adjacency cache; no DB.
"""

import random

import pytest

from app.config import settings
from app.services import adjacency_cache
from app.services.scoring_engine import NeuronScoreBreakdown
from app.services.neuron_service import (
    _spread_neighbors_python,
    _spread_neighbors_vectorized,
)


def _load(edges):
    """Load directed (src, tgt, weight, edge_type) edges (cache makes them bidirectional)."""
    adjacency_cache._cache.load(edges)


def _seeds(pairs):
    """(id, combined) pairs -> NeuronScoreBreakdown list."""
    return [
        NeuronScoreBreakdown(
            neuron_id=i, burst=0.0, impact=0.0, precision=0.0,
            novelty=0.0, recency=0.0, relevance=0.0, combined=c,
        )
        for i, c in pairs
    ]


def _both(scored, k):
    return _spread_neighbors_python(scored, k), _spread_neighbors_vectorized(scored, k)


def _assert_equivalent(py, vec):
    assert set(py.keys()) == set(vec.keys()), f"key mismatch: {set(py) ^ set(vec)}"
    for k in py:
        assert py[k] == pytest.approx(vec[k], rel=1e-9, abs=1e-12), f"value mismatch at {k}"


@pytest.fixture(autouse=True)
def _clean_cache():
    yield
    adjacency_cache.invalidate_adjacency_cache()


def test_three_hop_reach_preserved(monkeypatch):
    """A node reachable ONLY at hop 3 is discovered by both paths — and lost at 2 hops."""
    monkeypatch.setattr(settings, "spread_max_hops", 3)
    # chain 1->2->3->4, strong pyramidal edges + strong seed so hop-3 clears the floor
    _load([(1, 2, 0.9, "pyramidal"), (2, 3, 0.9, "pyramidal"), (3, 4, 0.9, "pyramidal")])
    scored = _seeds([(1, 2.0)])
    py, vec = _both(scored, 1)
    _assert_equivalent(py, vec)
    assert 4 in py, "node 4 (3 hops away) must be discovered"
    assert 2 in py and 3 in py

    # The 3-hop reach is load-bearing: at 2 hops, node 4 disappears in BOTH.
    monkeypatch.setattr(settings, "spread_max_hops", 2)
    py2, vec2 = _both(scored, 1)
    _assert_equivalent(py2, vec2)
    assert 4 not in py2 and 4 not in vec2


def test_max_across_paths_not_sum(monkeypatch):
    """A node reachable by two paths gets the MAX activation, not the sum — in both paths."""
    monkeypatch.setattr(settings, "spread_max_hops", 3)
    # diamond: 1->2, 1->3, 2->4, 3->4. Path via 2 is stronger than via 3.
    _load([
        (1, 2, 0.95, "pyramidal"), (1, 3, 0.60, "pyramidal"),
        (2, 4, 0.95, "pyramidal"), (3, 4, 0.60, "pyramidal"),
    ])
    scored = _seeds([(1, 3.0)])
    py, vec = _both(scored, 1)
    _assert_equivalent(py, vec)
    path_via_2 = 3.0 * 0.95 * 0.5 * 0.95 * 0.5
    path_via_3 = 3.0 * 0.60 * 0.5 * 0.60 * 0.5
    assert py[4] == pytest.approx(max(path_via_2, path_via_3), rel=1e-9)
    assert py[4] < path_via_2 + path_via_3  # not summed


def test_edge_type_decays_and_weight_gates(monkeypatch):
    """Per-edge-type decay + the pyramidal min-weight gate match in both paths."""
    monkeypatch.setattr(settings, "spread_max_hops", 1)
    # Seed=3.0 so activations clear the 0.15 floor and the WEIGHT gate is isolated.
    # From seed 1: stellate(0.3), pyramidal(0.5), instantiates(0.6); plus a pyramidal
    # edge at weight 0.18 (below pyramidal min 0.20 -> gated even though its activation
    # would clear the floor) and a stellate at 0.18 (>= 0.15 global -> passes).
    _load([
        (1, 2, 0.8, "stellate"),
        (1, 3, 0.8, "pyramidal"),
        (1, 4, 0.8, "instantiates"),
        (1, 5, 0.18, "pyramidal"),
        (1, 6, 0.18, "stellate"),
    ])
    scored = _seeds([(1, 3.0)])
    py, vec = _both(scored, 1)
    _assert_equivalent(py, vec)
    assert py[2] == pytest.approx(3.0 * 0.8 * settings.spread_stellate_decay, rel=1e-9)
    assert py[3] == pytest.approx(3.0 * 0.8 * settings.spread_decay, rel=1e-9)
    assert py[4] == pytest.approx(3.0 * 0.8 * settings.spread_instantiate_decay, rel=1e-9)
    # node 5: pyramidal @0.18 — activation 3.0*0.18*0.5=0.27 clears the floor, but the
    # 0.20 pyramidal weight gate excludes it; node 6: stellate @0.18 passes.
    assert 5 not in py, "pyramidal edge below 0.20 must be gated out by weight"
    assert 6 in py, "stellate edge at 0.18 (>= 0.15 global) must pass"


def test_topk_never_promoted(monkeypatch):
    """Seed (top-k) nodes are never returned as promoted neighbors."""
    monkeypatch.setattr(settings, "spread_max_hops", 3)
    _load([(1, 2, 0.9, "pyramidal"), (2, 1, 0.9, "pyramidal"), (2, 3, 0.9, "pyramidal")])
    scored = _seeds([(1, 2.0), (2, 2.0)])
    py, vec = _both(scored, 2)  # both 1 and 2 are seeds
    _assert_equivalent(py, vec)
    assert 1 not in py and 2 not in py


@pytest.mark.parametrize("trial", range(12))
def test_equivalence_random_graphs(monkeypatch, trial):
    """Fuzz: on random graphs + seeds, vectorized == reference exactly."""
    monkeypatch.setattr(settings, "spread_max_hops", 3)
    rng = random.Random(1000 + trial)
    n_nodes = rng.randint(30, 250)
    n_edges = rng.randint(n_nodes, n_nodes * 6)
    etypes = ["pyramidal", "stellate", "instantiates", "regulatory"]
    edges = []
    for _ in range(n_edges):
        s, t = rng.randint(1, n_nodes), rng.randint(1, n_nodes)
        if s != t:
            edges.append((s, t, round(rng.uniform(0.05, 1.0), 3), rng.choice(etypes)))
    _load(edges)
    seeds = _seeds([
        (rng.randint(1, n_nodes), round(rng.uniform(0.5, 3.0), 3))
        for _ in range(rng.randint(1, 20))
    ])
    # dedupe seed ids (frontier is keyed by id)
    seen, uniq = set(), []
    for s in seeds:
        if s.neuron_id not in seen:
            seen.add(s.neuron_id)
            uniq.append(s)
    py, vec = _both(uniq, len(uniq))
    _assert_equivalent(py, vec)
