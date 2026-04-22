"""Spread activation: propagate scores across graph neighbors."""

from typing import Any

from app.config import settings
from app.services.neuron_service import spread_activation
from app.services.pipeline.context import PipelineContext
from app.services.pipeline.state import PipelineState


class SpreadActivationStage:
    """Lift graph neighbors of high-scoring neurons.

    Reads:  state.scored, state.effective_top_k
    Writes: state.scored (propagated)
    """

    name = "spread_activation"

    async def run(self, state: PipelineState, ctx: PipelineContext) -> PipelineState:
        if settings.spread_enabled:
            from app.services.adjacency_cache import ensure_adjacency_loaded
            await ensure_adjacency_loaded(ctx.db)
        state.scored = await spread_activation(
            ctx.db, state.scored, state.effective_top_k,
        )
        return state

    def describe(self, out: PipelineState) -> dict[str, Any]:
        return {"propagated": len(out.scored)}
