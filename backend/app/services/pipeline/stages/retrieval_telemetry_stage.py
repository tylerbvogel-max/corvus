"""Terminal observe-only stage: per-query retrieval-quality telemetry.

Step 01 of the post-certificate forensics plan (mind-retrieval-telemetry).
The certificate logged n_hits=10 on 1,986/1,986 questions — the only recorded
retrieval-quality proxy had zero variance, so no downstream policy (adaptive
depth, evidence-gated abstention) could condition on how well a lookup went.
This stage records the missing signals. It must never change them: any
ranking, threshold, or depth decision belongs to later plan steps.
"""

from typing import Any

from app.config import settings
from app.models import Neuron
from app.services.pipeline.context import PipelineContext
from app.services.pipeline.state import PipelineState
from app.services.scoring_engine import NeuronScoreBreakdown


class RetrievalTelemetryStage:
    """Observe-only: score distribution, lane attribution, entity coverage.

    Reads:  state.scored, top_slice, neuron_map, lane_hits, embedding_sims,
            user_message
    Writes: state.retrieval_telemetry (never the scores themselves)
    """

    name = "retrieval_telemetry"

    async def run(self, state: PipelineState, ctx: PipelineContext) -> PipelineState:
        state.retrieval_telemetry = _build_payload(state)
        return state

    def describe(self, out: PipelineState) -> dict[str, Any]:
        return out.retrieval_telemetry


def _entity_coverage(
    entities: list[str],
    delivered: list[NeuronScoreBreakdown],
    neuron_map: dict[int, Neuron],
) -> float | None:
    """Fraction of query entities present in the delivered neurons' text.

    None (not 0.0) when the query names no entities, so analysis can separate
    "nothing to cover" from "covered nothing".
    """
    if not entities:
        return None
    blob = " ".join(
        f"{n.label or ''} {n.content or ''} {n.summary or ''}"
        for n in (neuron_map.get(s.neuron_id) for s in delivered)
        if n is not None
    ).lower()
    covered = sum(1 for e in entities if e.lower() in blob)
    return round(covered / len(entities), 4)


def _build_payload(state: PipelineState) -> dict[str, Any]:
    from app.services.recall_lanes import extract_query_entities

    ranked = sorted((s.combined for s in state.scored), reverse=True)
    threshold = settings.relevance_gate_threshold
    above = [s.relevance for s in state.scored if s.relevance >= threshold]

    lane_ids: dict[str, set[int]] = {
        lane: set(ids) for lane, ids in (state.lane_hits or {}).items()
    }
    lane_ids["spread"] = {s.neuron_id for s in state.scored if s.spread_boost > 0}
    delivered = state.top_slice
    delivered_lanes = {
        str(s.neuron_id): sorted(
            lane for lane, ids in lane_ids.items() if s.neuron_id in ids
        )
        for s in delivered
    }

    # Raw pre-RRF cosines: RRF rank normalization pins the fused top-1 score
    # to a constant (observed: 1.0269 on every smoke question), so only these
    # magnitudes can express how well the lookup actually went.
    sims = sorted(state.embedding_sims.values(), reverse=True)

    entities = extract_query_entities(state.user_message)
    return {
        "n_candidates": len(state.scored),
        "n_delivered": len(delivered),
        "score_vector": [round(v, 4) for v in ranked],
        "top1_score": round(ranked[0], 4) if ranked else 0.0,
        "top1_top2_margin": (
            round(ranked[0] - ranked[1], 4) if len(ranked) > 1 else None
        ),
        "sim_vector": [round(v, 4) for v in sims],
        "top1_sim": round(sims[0], 4) if sims else None,
        "sim_margin": round(sims[0] - sims[1], 4) if len(sims) > 1 else None,
        "sim_top5_mean": (
            round(sum(sims[:5]) / min(5, len(sims)), 4) if sims else None
        ),
        "relevance_threshold": threshold,
        "n_above_threshold": len(above),
        "mass_above_threshold": round(sum(above), 4),
        "query_entity_count": len(entities),
        "entity_coverage": _entity_coverage(entities, delivered, state.neuron_map),
        "lane_candidates": {lane: len(ids) for lane, ids in sorted(lane_ids.items())},
        "delivered_lanes": delivered_lanes,
    }
