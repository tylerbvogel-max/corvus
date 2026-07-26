"""RetrievalTelemetryStage — observe-only payload shape and variance.

Step 01 of the forensics plan: the payload must VARY with retrieval quality
(the certificate's n_hits=10 constant is the failure mode being fixed) and
must never mutate the scores it observes.
"""

import asyncio
from types import SimpleNamespace

import pytest

from app.services.pipeline.stages.retrieval_telemetry_stage import (
    RetrievalTelemetryStage, _build_payload, _entity_coverage,
)
from app.services.pipeline.state import PipelineState
from app.services.scoring_engine import NeuronScoreBreakdown


def _score(nid: int, combined: float, relevance: float = 0.0,
           spread_boost: float = 0.0) -> NeuronScoreBreakdown:
    return NeuronScoreBreakdown(
        neuron_id=nid, burst=0.0, impact=0.0, precision=0.0, novelty=0.0,
        recency=0.0, relevance=relevance, combined=combined,
        spread_boost=spread_boost,
    )


def _neuron(nid: int, label: str, content: str = "") -> SimpleNamespace:
    return SimpleNamespace(id=nid, label=label, content=content, summary=None)


def _state(**overrides) -> PipelineState:
    defaults = dict(user_message="What did Wrenfield glaze?",
                    effective_top_k=10, effective_pool=150,
                    effective_budget=4000)
    defaults.update(overrides)
    return PipelineState(**defaults)


def test_payload_orders_scores_and_computes_margin():
    state = _state()
    state.scored = [_score(1, 0.9, relevance=0.8), _score(2, 0.5, relevance=0.2),
                    _score(3, 0.7, relevance=0.4)]
    state.top_slice = state.scored[:2]
    payload = _build_payload(state)
    assert payload["score_vector"] == [0.9, 0.7, 0.5]
    assert payload["top1_score"] == 0.9
    assert payload["top1_top2_margin"] == pytest.approx(0.2)
    assert payload["n_candidates"] == 3
    assert payload["n_delivered"] == 2


def test_payload_varies_across_queries():
    """The certificate failure mode: a constant proxy. Two different retrieval
    outcomes must produce different payloads."""
    strong = _state()
    strong.scored = [_score(1, 0.95, relevance=0.9), _score(2, 0.3, relevance=0.1)]
    strong.top_slice = strong.scored[:1]
    weak = _state()
    weak.scored = [_score(1, 0.31, relevance=0.28), _score(2, 0.30, relevance=0.27)]
    weak.top_slice = weak.scored
    p_strong, p_weak = _build_payload(strong), _build_payload(weak)
    assert p_strong["top1_score"] != p_weak["top1_score"]
    assert p_strong["top1_top2_margin"] != p_weak["top1_top2_margin"]
    assert p_strong["n_above_threshold"] != p_weak["n_above_threshold"]


def test_threshold_mass_uses_relevance_not_combined():
    state = _state()
    state.scored = [_score(1, 0.9, relevance=0.5), _score(2, 0.8, relevance=0.1)]
    state.top_slice = state.scored
    payload = _build_payload(state)
    assert payload["n_above_threshold"] == 1
    assert payload["mass_above_threshold"] == pytest.approx(0.5)
    assert payload["relevance_threshold"] > 0


def test_lane_attribution_includes_spread_and_uses_string_keys():
    state = _state()
    state.scored = [_score(1, 0.9), _score(2, 0.6, spread_boost=0.1)]
    state.top_slice = state.scored
    state.lane_hits = {"embedding": [1, 2], "keyword": [1]}
    payload = _build_payload(state)
    assert payload["delivered_lanes"]["1"] == ["embedding", "keyword"]
    assert payload["delivered_lanes"]["2"] == ["embedding", "spread"]
    assert payload["lane_candidates"] == {"embedding": 2, "keyword": 1, "spread": 1}


def test_raw_sims_survive_rrf_saturation():
    """The fused top-1 score is rank-pinned; the raw cosine fields must carry
    the actual magnitudes so downstream policies have a confidence signal."""
    state = _state()
    state.scored = [_score(1, 1.0269), _score(2, 0.98)]
    state.top_slice = state.scored
    state.embedding_sims = {1: 0.83, 2: 0.41}
    payload = _build_payload(state)
    assert payload["sim_vector"] == [0.83, 0.41]
    assert payload["top1_sim"] == 0.83
    assert payload["sim_margin"] == pytest.approx(0.42)
    assert payload["sim_top5_mean"] == pytest.approx(0.62)


def test_no_embedding_lane_yields_none_sims():
    state = _state()
    state.scored = [_score(1, 0.9)]
    state.top_slice = state.scored
    payload = _build_payload(state)
    assert payload["sim_vector"] == []
    assert payload["top1_sim"] is None
    assert payload["sim_margin"] is None
    assert payload["sim_top5_mean"] is None


def test_entity_coverage_distinguishes_none_from_zero():
    delivered = [_score(1, 0.9)]
    neuron_map = {1: _neuron(1, "Wrenfield's kiln", "Wrenfield glazed a teal ewer")}
    assert _entity_coverage([], delivered, neuron_map) is None
    assert _entity_coverage(["Wrenfield"], delivered, neuron_map) == 1.0
    assert _entity_coverage(["Ostrander"], delivered, neuron_map) == 0.0
    assert _entity_coverage(["Wrenfield", "Ostrander"], delivered, neuron_map) == 0.5


def test_stage_is_observe_only():
    """Running the stage must not change scores, order, or delivery."""
    state = _state()
    state.scored = [_score(1, 0.9, relevance=0.7), _score(2, 0.4, relevance=0.2)]
    state.top_slice = list(state.scored)
    state.lane_hits = {"embedding": [1, 2]}
    before = [(s.neuron_id, s.combined, s.relevance, s.spread_boost)
              for s in state.scored]
    out = asyncio.run(RetrievalTelemetryStage().run(state, ctx=None))
    after = [(s.neuron_id, s.combined, s.relevance, s.spread_boost)
             for s in out.scored]
    assert before == after
    assert out.retrieval_telemetry["n_candidates"] == 2
    assert RetrievalTelemetryStage().describe(out) == out.retrieval_telemetry


def test_empty_retrieval_yields_zeroes_not_crash():
    state = _state()
    payload = _build_payload(state)
    assert payload["n_candidates"] == 0
    assert payload["top1_score"] == 0.0
    assert payload["top1_top2_margin"] is None
    assert payload["delivered_lanes"] == {}
