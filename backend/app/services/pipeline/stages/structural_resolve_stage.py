"""Fast-path stage: if the query has a direct structural match, short-circuit."""

from typing import Any

from app.services.pipeline.context import PipelineContext
from app.services.pipeline.stage import ShortCircuit
from app.services.pipeline.state import PipelineState
from app.services.structural_resolver import try_structural_resolve


class StructuralResolveStage:
    """Attempt a zero-cost structural match.

    Reads:  state.user_message
    Writes: nothing on the state; on match, raises `ShortCircuit(prepared_context)`
            so the remaining stages do not run.
    """

    name = "structural_resolve"

    async def run(self, state: PipelineState, ctx: PipelineContext) -> PipelineState:
        structural = await try_structural_resolve(ctx.db, state.user_message)
        if structural is not None:
            raise ShortCircuit(structural)
        return state

    def describe(self, _out: PipelineState) -> dict[str, Any]:
        return {"matched": False}
