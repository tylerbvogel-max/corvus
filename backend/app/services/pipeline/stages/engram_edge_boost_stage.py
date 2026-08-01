"""Boost regulatory engrams linked to the fired neurons (EngramEdge association).

Realises the neuron -> regulation association: a neuron that is about a topic
pulls in the regulations it has co-fired with. Reads the top fired neurons and
the scored engrams; adds a bounded boost to each engram proportional to its
strongest edge to a fired neuron, then re-sorts so RegulatoryResolve picks the
boosted set. Feature-flagged (scoring change, opt-in) — a no-op when disabled,
when no engrams scored, or when no engram edges exist yet.
"""

from typing import Any

from app.config import settings
from app.services.pipeline.context import PipelineContext
from app.services.pipeline.state import PipelineState


class EngramEdgeBoostStage:
    """Boost scored engrams via their EngramEdge links to the fired neurons.

    Reads:  state.all_scored (fired neurons), state.scored_engrams
    Writes: state.scored_engrams (boosted + re-sorted)
    """

    name = "engram_edge_boost"

    async def run(self, state: PipelineState, ctx: PipelineContext) -> PipelineState:
        if not settings.engram_edge_boost_enabled:
            return state
        if not state.scored_engrams or not state.all_scored:
            return state

        from app.services.adjacency_cache import (
            get_graph_neighbors,
            is_engram_key, key_to_engram_id,
        )

        top_neurons = state.all_scored[:settings.top_k_neurons]
        score_by_neuron = {s.neuron_id: s.combined for s in top_neurons}
        neighbors = await get_graph_neighbors(
            ctx.db, set(score_by_neuron.keys()), settings.spread_min_edge_weight,
        )

        # engram_id -> strongest boost across its links to fired neurons
        boost_by_engram: dict[int, float] = {}
        for nid, nbrs in neighbors.items():
            nscore = score_by_neuron.get(nid, 0.0)
            for neighbor_key, weight, _etype in nbrs:
                if not is_engram_key(neighbor_key):
                    continue
                eid = key_to_engram_id(neighbor_key)
                boost = weight * nscore * settings.engram_edge_boost_scale
                if boost > boost_by_engram.get(eid, 0.0):
                    boost_by_engram[eid] = boost

        if not boost_by_engram:
            return state
        # scored_engrams reuse NeuronScoreBreakdown; neuron_id field holds the engram id.
        for s in state.scored_engrams:
            boost = boost_by_engram.get(s.neuron_id)
            if boost:
                s.spread_boost = (s.spread_boost or 0.0) + boost
                s.combined = round(s.combined + boost, 4)
        state.scored_engrams.sort(key=lambda s: s.combined, reverse=True)
        return state

    def describe(self, out: PipelineState) -> dict[str, Any]:
        return {"engrams_scored": len(out.scored_engrams)}
