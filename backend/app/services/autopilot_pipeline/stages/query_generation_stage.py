"""Ask the LLM to craft a targeted query probing the detected gap."""

from typing import Any

from app.services.autopilot_pipeline.state import AutopilotState
from app.services.pipeline.context import PipelineContext


class QueryGenerationStage:
    """LLM call that turns a gap + directive into ONE natural-language query.

    Reads:  state.config.directive, state.recent_queries, state.focus_context, state.gap
    Writes: state.generated_query, state.total_cost (appended)
    """

    name = "query_generation"

    async def run(self, state: AutopilotState, _ctx: PipelineContext) -> AutopilotState:
        assert state is not None, "state is required"
        assert state.config is not None, "state.config is required"

        from app.routers.autopilot import _generate_query, _set_step

        _set_step("generate", "Generating targeted query from gap analysis...")

        generated_query, gen_cost = await _generate_query(
            state.config.directive,
            state.recent_queries,
            state.focus_context,
            state.gap,
        )
        state.generated_query = generated_query
        state.total_cost += gen_cost
        return state

    def describe(self, out: AutopilotState) -> dict[str, Any]:
        return {
            "query_chars": len(out.generated_query or ""),
            "cost_usd": round(out.total_cost, 6),
        }
