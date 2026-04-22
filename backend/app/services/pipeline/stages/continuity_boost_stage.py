"""Multi-turn conversation continuity: 1.3x score boost for neurons from prior turns."""

from typing import Any

from app.services.pipeline.context import PipelineContext
from app.services.pipeline.state import PipelineState


class ContinuityBoostStage:
    """Boost neurons whose ids were fired in prior conversation turns.

    Reads:  state.scored, state.prior_neuron_ids
    Writes: state.scored (re-sorted with boosted `combined` values)
    """

    name = "continuity_boost"

    async def run(self, state: PipelineState, ctx: PipelineContext) -> PipelineState:
        prior = state.prior_neuron_ids
        if not prior:
            return state
        prior_set = set(prior)
        boosted = 0
        # JPL-2 bounded loop: iterating a finite scored list
        for s in state.scored:
            if s.neuron_id in prior_set:
                s.combined = round(s.combined * 1.3, 4)
                boosted += 1
        state.scored.sort(key=lambda s: s.combined, reverse=True)
        state.continuity_boosted_count = boosted
        return state

    def describe(self, out: PipelineState) -> dict[str, Any]:
        return {"boosted": out.continuity_boosted_count}
