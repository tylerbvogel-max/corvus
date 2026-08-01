"""Horizontal-scaling coherence and growth-bound honeypots.

These tests attack the failure modes that a normal single-process unit suite
cannot see: stale per-worker semantic replicas, graph traversal through a
private adjacency copy, and firing-history memory that grows forever.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.services import adjacency_cache, neuron_index, semantic_prefilter
from app.services.neuron_service import _spread_neighbors_database
from app.services.scoring_engine import NeuronScoreBreakdown


def _result(rows=(), scalar=None):
    result = MagicMock()
    result.all.return_value = list(rows)
    result.scalar_one.return_value = scalar
    result.scalar_one_or_none.return_value = scalar
    return result


@pytest.fixture(autouse=True)
def _clean_replicas():
    semantic_prefilter.invalidate_cache()
    adjacency_cache.invalidate_adjacency_cache()
    neuron_index.invalidate_index()
    yield
    semantic_prefilter.invalidate_cache()
    adjacency_cache.invalidate_adjacency_cache()
    neuron_index.invalidate_index()


@pytest.mark.asyncio
async def test_semantic_replica_reloads_only_when_shared_revision_advances(
    monkeypatch,
):
    """A worker with a loaded matrix observes another worker's committed write."""
    monkeypatch.setattr(settings, "cache_coherence_mode", "database")
    db = AsyncMock()
    db.execute.side_effect = [
        _result(scalar=7),
        _result(scalar=7),
        _result([(1, "[1.0, 0.0]")]),
        _result([]),
        _result(scalar=7),  # unchanged: no matrix table reads
        _result(scalar=8),  # another worker advanced the shared clock
        _result(scalar=8),
        _result([(1, "[1.0, 0.0]"), (2, "[0.0, 1.0]")]),
        _result([]),
    ]

    await semantic_prefilter.ensure_cache_loaded(db)
    assert semantic_prefilter._cache.revision == 7
    assert semantic_prefilter._cache._entity_ids == [1]

    await semantic_prefilter.ensure_cache_loaded(db)
    assert db.execute.await_count == 5

    await semantic_prefilter.ensure_cache_loaded(db)
    assert semantic_prefilter._cache.revision == 8
    assert semantic_prefilter._cache._entity_ids == [1, 2]


@pytest.mark.asyncio
async def test_concurrent_stale_semantic_reads_collapse_to_one_reload(
    monkeypatch,
):
    """A per-worker request burst performs one matrix reload, not one per request."""
    monkeypatch.setattr(settings, "cache_coherence_mode", "database")
    reads = {"neurons": 0, "engrams": 0}

    class FakeDb:
        async def execute(self, statement, params=None):
            sql = str(statement)
            if "cache_versions" in sql:
                return _result(scalar=11)
            if "FROM neurons" in sql:
                reads["neurons"] += 1
                await asyncio.sleep(0.02)
                return _result([(1, "[1.0, 0.0]")])
            if "FROM engrams" in sql:
                reads["engrams"] += 1
                return _result([])
            raise AssertionError(sql)

    await asyncio.gather(*[
        semantic_prefilter.ensure_cache_loaded(FakeDb())
        for _ in range(8)
    ])

    assert reads == {"neurons": 1, "engrams": 1}
    assert semantic_prefilter._cache.revision == 11


@pytest.mark.asyncio
async def test_database_graph_backend_ignores_stale_local_adjacency(monkeypatch):
    """Canonical graph reads win even if this worker holds an obsolete replica."""
    monkeypatch.setattr(settings, "cache_coherence_mode", "database")
    monkeypatch.setattr(settings, "spread_edges_max_per_hop", 123)
    adjacency_cache._cache.load([(1, 99, 0.9, "pyramidal")])
    db = AsyncMock()
    db.execute.return_value = _result([
        (1, 2, 0.8, "pyramidal"),
        (1, -5, 0.7, "regulatory"),
    ])
    metrics = {}

    neighbors = await adjacency_cache.get_graph_neighbors(
        db, {1}, 0.15, metrics,
    )

    assert neighbors == {
        1: [(2, 0.8, "pyramidal"), (-5, 0.7, "regulatory")],
    }
    assert 99 not in {nid for nid, _weight, _etype in neighbors[1]}
    _stmt, params = db.execute.await_args.args
    assert params["edge_limit"] == 123
    assert metrics == {
        "edge_rows": 2,
        "max_edge_rows_per_hop": 2,
    }


@pytest.mark.asyncio
async def test_database_graph_backend_reports_edge_limit_saturation(monkeypatch):
    monkeypatch.setattr(settings, "cache_coherence_mode", "database")
    monkeypatch.setattr(settings, "spread_edges_max_per_hop", 2)
    db = AsyncMock()
    db.execute.return_value = _result([
        (1, 2, 0.8, "pyramidal"),
        (1, 3, 0.7, "pyramidal"),
    ])
    metrics = {}

    await adjacency_cache.get_graph_neighbors(db, {1}, 0.15, metrics)

    assert metrics["edge_rows"] == 2
    assert metrics["max_edge_rows_per_hop"] == 2
    assert metrics["edge_limit_saturated_hops"] == 1


@pytest.mark.asyncio
async def test_database_spread_caps_the_next_frontier(monkeypatch):
    """A high-degree hub cannot make the next PostgreSQL read unbounded."""
    monkeypatch.setattr(settings, "cache_coherence_mode", "database")
    monkeypatch.setattr(settings, "spread_hops_auto", False)
    monkeypatch.setattr(settings, "spread_max_hops", 2)
    monkeypatch.setattr(settings, "spread_frontier_max_nodes", 25)
    monkeypatch.setattr(settings, "spread_max_neurons", 100)
    monkeypatch.setattr(settings, "genesis_mode", False)
    monkeypatch.setattr(settings, "spread_instantiate_decay", 0.6)
    monkeypatch.setattr(settings, "spread_instantiate_min_weight", 0.1)
    first_hop = {
        1: [(1000 + i, 1.0 - i / 1000, "instantiates") for i in range(40)]
    }
    fetch = AsyncMock(side_effect=[first_hop, {}])
    monkeypatch.setattr(adjacency_cache, "get_graph_neighbors", fetch)
    scored = [
        NeuronScoreBreakdown(
            neuron_id=1,
            burst=0.0,
            impact=0.0,
            precision=0.0,
            novelty=0.0,
            recency=0.0,
            relevance=1.0,
            combined=1.0,
        )
    ]

    metrics = {}
    found = await _spread_neighbors_database(
        AsyncMock(), scored, 1, max_hops=2, min_activation=0.0,
        traversal_metrics=metrics,
    )

    assert len(found) == 40
    assert fetch.await_count == 2
    assert len(fetch.await_args_list[1].args[1]) == 25
    assert metrics["frontier_cap_hits"] == 1
    assert metrics["frontier_nodes_dropped"] == 15
    assert metrics["max_frontier_nodes"] == 25
    assert metrics["hops_queried"] == 2


def test_neuron_index_memory_is_independent_of_all_time_firing_count():
    """Ten million historical firings remain scalar counters, not ten million IDs."""
    idx = neuron_index.get_index()
    meta = [{
        "id": 1,
        "label": "bounded",
        "summary": None,
        "department": "Memory",
        "role_key": None,
        "avg_utility": 0.5,
        "invocations": 10_000_000,
        "created_at_query_count": 0,
        "authority_level": None,
        "centrality": 0.0,
        "is_active": True,
        "freshness_days": 0.0,
    }]
    idx.load_aggregates(
        meta,
        [(1, list(range(950, 1001)))],
        [(1, 10_000_000, 1000, 10_000_000)],
        [("Memory", 10_000_000, 10_000_000)],
        retention_queries=50,
    )

    assert idx.fire_stats([1]) == ({1: 10_000_000}, {1: 1000})
    assert isinstance(idx._fire_counts[1], int)
    assert len(idx._offsets[1]) == 51

    for qid in range(10_000_001, 10_001_001):
        idx.on_firing(1, qid, 1000 + qid - 10_000_000)
    assert len(idx._offsets[1]) <= 51
    assert idx.fire_stats([1])[0][1] == 10_001_000


def test_regulatory_seed_completeness_follows_the_tenant_tree():
    """An intentionally empty memory-tenant tree is complete at one anchor."""
    from app.composition.startup import _expected_regulatory_seed_count

    assert _expected_regulatory_seed_count([]) == 1
    tree = [(
        "role",
        "role-key",
        [],
        None,
        [
            ("task-a", "summary", "content", [], []),
            ("task-b", "summary", "content", [], [
                ("system-a", "summary", "content"),
                ("system-b", "summary", "content"),
            ]),
        ],
    )]
    assert _expected_regulatory_seed_count(tree) == 6
