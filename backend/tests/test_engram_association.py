"""Tests for the EngramEdge neuron<->regulation association (record + boost)."""

import asyncio
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.services.engram_service import record_engram_cofiring
from app.services.scoring_engine import NeuronScoreBreakdown
from app.services.pipeline.state import PipelineState
from app.services.pipeline.context import PipelineContext
from app.services.pipeline.stages.engram_edge_boost_stage import EngramEdgeBoostStage


# ---- batched co-fire recording ----

def test_record_cofiring_batches_all_pairs():
    db = AsyncMock()
    asyncio.run(record_engram_cofiring(db, [1, 2], [10, 11, 12], 7))
    assert db.execute.await_count == 1, "must be a single batched round-trip"
    args, _ = db.execute.await_args
    pairs = args[1]
    assert len(pairs) == 6  # 2 engrams x 3 neurons
    assert {"eid": 1, "nid": 10, "q": 7} in pairs
    assert {"eid": 2, "nid": 12, "q": 7} in pairs


def test_record_cofiring_noop_when_empty():
    db = AsyncMock()
    asyncio.run(record_engram_cofiring(db, [], [10], 7))
    asyncio.run(record_engram_cofiring(db, [1], [], 7))
    assert db.execute.await_count == 0


# ---- engram edge boost stage ----

def _nsb(nid, combined):
    return NeuronScoreBreakdown(
        neuron_id=nid, burst=0.0, impact=0.0, precision=0.0,
        novelty=0.0, recency=0.0, relevance=0.0, combined=combined,
    )


def _state():
    st = PipelineState(
        user_message="q", effective_top_k=30, effective_pool=100, effective_budget=4000,
    )
    st.all_scored = [_nsb(100, 0.9), _nsb(101, 0.5)]        # fired neurons
    st.scored_engrams = [_nsb(5, 0.2), _nsb(6, 0.3)]        # engram ids 5, 6
    return st


def test_boost_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "engram_edge_boost_enabled", False)
    st = _state()
    out = asyncio.run(EngramEdgeBoostStage().run(st, PipelineContext(db=AsyncMock())))
    assert [s.combined for s in out.scored_engrams] == [0.2, 0.3]


def test_boost_lifts_linked_engram_and_resorts(monkeypatch):
    monkeypatch.setattr(settings, "engram_edge_boost_enabled", True)
    monkeypatch.setattr(settings, "engram_edge_boost_scale", 0.3)
    st = _state()
    # neuron 100 (score 0.9) links to engram 5 (adjacency key -5) at weight 0.8
    with patch(
        "app.services.adjacency_cache.get_graph_neighbors",
        new=AsyncMock(return_value={100: [(-5, 0.8, "regulatory")], 101: []}),
    ):
        out = asyncio.run(EngramEdgeBoostStage().run(st, PipelineContext(db=AsyncMock())))
    by_id = {s.neuron_id: s for s in out.scored_engrams}
    assert by_id[5].combined == round(0.2 + 0.8 * 0.9 * 0.3, 4)  # 0.416
    assert round(by_id[5].spread_boost, 4) == round(0.8 * 0.9 * 0.3, 4)
    assert by_id[6].combined == 0.3  # unlinked engram unchanged
    assert out.scored_engrams[0].neuron_id == 5  # boosted engram re-sorted to top


def test_boost_noop_without_engrams(monkeypatch):
    monkeypatch.setattr(settings, "engram_edge_boost_enabled", True)
    st = _state()
    st.scored_engrams = []
    out = asyncio.run(EngramEdgeBoostStage().run(st, PipelineContext(db=AsyncMock())))
    assert out.scored_engrams == []
