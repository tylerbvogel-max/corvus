"""Semantic prefilter + score (these are tightly coupled today — one stage)."""

from typing import Any

from app.services.neuron_service import get_system_state
from app.services.pipeline.context import PipelineContext
from app.services.pipeline.state import PipelineState


class PrefilterScoreStage:
    """Narrow the candidate pool and compute 5-signal scores.

    Reads:  state.query_embedding, keywords, departments, role_keys, effective_pool
    Writes: state.total_queries, state.scored, state.scored_engrams,
            state.lane_hits, state.embedding_sims
    """

    name = "prefilter_score"

    async def run(self, state: PipelineState, ctx: PipelineContext) -> PipelineState:
        from app.services.recall_primitives import _select_and_score_candidates
        system_state = await get_system_state(ctx.db)
        state.total_queries = system_state.total_queries
        scored, scored_engrams, lane_hits, embedding_sims = await _select_and_score_candidates(
            ctx.db,
            state.query_embedding,
            state.effective_pool,
            state.keywords,
            state.departments,
            state.role_keys,
            state.total_queries,
            requester=state.requester,
            user_message=state.user_message,
        )
        state.scored = scored
        state.scored_engrams = scored_engrams
        state.candidates_considered = len(scored)
        state.lane_hits = lane_hits
        state.embedding_sims = embedding_sims
        return state

    def describe(self, out: PipelineState) -> dict[str, Any]:
        return {
            "candidates": len(out.scored),
            "engram_candidates": len(out.scored_engrams),
        }
