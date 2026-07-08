"""Per-query spread-activation overrides (Query Lab / hero spread controls).

Covers: hop-cap and activation-floor overrides in the reference BFS,
schema bounds, and the executor's grouping of slots into one context
prep per distinct spread config.
"""
import pytest
from pydantic import ValidationError

import app.services.executor as ex
import app.services.neuron_service as ns
from app.schemas import QuerySlotRequest
from app.services.scoring_engine import NeuronScoreBreakdown


def _mk(nid, combined=1.0):
    return NeuronScoreBreakdown(
        neuron_id=nid, burst=0, impact=0, precision=0, novelty=0,
        recency=0, relevance=0, combined=combined,
    )


# Chain 1 -> 2 -> 3 -> 4 -> 5 over instantiates edges (decay 0.6, weight 1.0):
# hop activations from seed 1.0 are 0.6, 0.36, 0.216, 0.1296.
_CHAIN = {
    1: [(2, 1.0, "instantiates")],
    2: [(3, 1.0, "instantiates")],
    3: [(4, 1.0, "instantiates")],
    4: [(5, 1.0, "instantiates")],
}


@pytest.fixture
def chain_adjacency(monkeypatch):
    monkeypatch.setattr(
        ns, "_fetch_frontier_neighbors_cached",
        lambda ids: {i: _CHAIN.get(i, []) for i in ids},
    )
    monkeypatch.setattr(ns.settings, "spread_instantiate_decay", 0.6)
    monkeypatch.setattr(ns.settings, "spread_instantiate_min_weight", 0.1)


def test_spread_hop_cap_override(chain_adjacency):
    one_hop = ns._spread_neighbors_python([_mk(1)], 1, max_hops=1, min_activation=0.0)
    assert set(one_hop) == {2}
    five_hops = ns._spread_neighbors_python([_mk(1)], 1, max_hops=5, min_activation=0.0)
    assert set(five_hops) == {2, 3, 4, 5}


def test_spread_floor_override_prunes_depth(chain_adjacency):
    strict = ns._spread_neighbors_python([_mk(1)], 1, max_hops=5, min_activation=0.5)
    assert set(strict) == {2}, "0.36 hop-2 activation must fall below a 0.5 floor"
    default_ish = ns._spread_neighbors_python([_mk(1)], 1, max_hops=5, min_activation=0.15)
    assert set(default_ish) == {2, 3, 4}, "hop-4 (0.1296) dies at the 0.15 floor"


def test_spread_none_falls_back_to_settings(chain_adjacency, monkeypatch):
    monkeypatch.setattr(ns.settings, "spread_max_hops", 2)
    monkeypatch.setattr(ns.settings, "spread_min_activation", 0.15)
    out = ns._spread_neighbors_python([_mk(1)], 1)
    assert set(out) == {2, 3}


def test_spread_hop_cap_bounds_enforced(chain_adjacency):
    with pytest.raises(AssertionError, match="max_hops"):
        ns._spread_neighbors_python([_mk(1)], 1, max_hops=11)


# ── Schema bounds ────────────────────────────────────────────────────────

def test_slot_schema_accepts_valid_spread_values():
    slot = QuerySlotRequest(mode="haiku_neuron", spread_hops=6, spread_floor=0.0)
    assert (slot.spread_hops, slot.spread_floor) == (6, 0.0)
    assert QuerySlotRequest(mode="haiku_neuron").spread_hops is None


@pytest.mark.parametrize("bad", [{"spread_hops": 0}, {"spread_hops": 7},
                                 {"spread_floor": -0.1}, {"spread_floor": 0.51}])
def test_slot_schema_rejects_out_of_range(bad):
    with pytest.raises(ValidationError):
        QuerySlotRequest(mode="haiku_neuron", **bad)


# ── Executor grouping: one context prep per distinct spread config ───────

@pytest.mark.asyncio
async def test_prepare_slot_contexts_groups_by_spread_cfg(monkeypatch):
    calls = []

    async def fake_pipeline(db, msg, group, top_k, on_stage,
                            prior_neuron_ids=None, spread_hops=None, spread_floor=None):
        calls.append({"cfg": (spread_hops, spread_floor), "top_k": top_k,
                      "on_stage": on_stage, "n_slots": len(group)})
        return object(), {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.01}

    monkeypatch.setattr(ex, "_run_neuron_pipeline", fake_pipeline)
    stage_cb = object()
    slots = [
        {"mode": "haiku_neuron", "top_k": 60},                                  # defaults
        {"mode": "opus_neuron", "top_k": 80},                                   # same cfg
        {"mode": "haiku_neuron", "top_k": 60, "spread_hops": 5, "spread_floor": 0.05},
        {"mode": "haiku_raw"},                                                   # no prep
    ]
    ctx_by_cfg, totals = await ex._prepare_slot_contexts(
        None, "question", slots, stage_cb, None)

    assert len(calls) == 2, "two distinct spread configs -> two context preps"
    assert set(ctx_by_cfg) == {(None, None), (5, 0.05)}
    default_call = next(c for c in calls if c["cfg"] == (None, None))
    assert default_call["n_slots"] == 2 and default_call["top_k"] == 80
    assert calls[0]["on_stage"] is stage_cb and calls[1]["on_stage"] is None, \
        "stage events must stream only for the first prep"
    assert totals["input_tokens"] == 20 and totals["cost_usd"] == pytest.approx(0.02)


@pytest.mark.asyncio
async def test_prepare_slot_contexts_raw_only_skips_prep(monkeypatch):
    async def boom(*_a, **_k):
        raise AssertionError("must not prepare context for raw-only slots")
    monkeypatch.setattr(ex, "_run_neuron_pipeline", boom)
    ctx_by_cfg, totals = await ex._prepare_slot_contexts(
        None, "question", [{"mode": "haiku_raw"}], None, None)
    assert ctx_by_cfg == {}
    assert totals["input_tokens"] == 0
