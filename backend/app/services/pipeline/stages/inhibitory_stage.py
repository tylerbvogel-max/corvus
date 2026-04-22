"""GABAergic / chandelier / neuromodulatory regulation + project-path boost."""

from typing import Any

from app.services.pipeline.context import PipelineContext
from app.services.pipeline.state import PipelineState


class InhibitoryStage:
    """Three-pass inhibition + optional project-path boost.

    Reads:  state.scored, state.effective_top_k, state.project_path
    Writes: state.all_scored, state.effective_top_k
    """

    name = "inhibitory"

    async def run(self, state: PipelineState, ctx: PipelineContext) -> PipelineState:
        from app.services.executor import _apply_inhibition_and_boost
        all_scored, new_top_k = await _apply_inhibition_and_boost(
            ctx.db, state.scored, state.effective_top_k, state.project_path,
        )
        state.all_scored = all_scored
        state.effective_top_k = new_top_k
        return state

    def describe(self, out: PipelineState) -> dict[str, Any]:
        return {
            "neurons_activated": len(out.all_scored),
            "effective_top_k": out.effective_top_k,
        }
