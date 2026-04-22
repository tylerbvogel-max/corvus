"""Run the generated query through the main query-prep pipeline + executor."""

from typing import Any

from app.services.autopilot_pipeline.state import AutopilotState
from app.services.pipeline.context import PipelineContext


class PipelineExecutionStage:
    """Execute the generated query and remember the resulting Query id.

    Reads:  state.generated_query
    Writes: state.query_id, state.neurons_activated, state.total_cost (appended)
    """

    name = "pipeline_execution"

    async def run(self, state: AutopilotState, _ctx: PipelineContext) -> AutopilotState:
        assert state is not None, "state is required"
        assert state.generated_query, "generated_query must be set before execution"

        from app.routers.autopilot import _execute_pipeline

        query_id, neurons_activated, exec_cost = await _execute_pipeline(state.generated_query)
        state.query_id = query_id
        state.neurons_activated = neurons_activated
        state.total_cost += exec_cost
        return state

    def describe(self, out: AutopilotState) -> dict[str, Any]:
        return {
            "query_id": out.query_id,
            "neurons_activated": out.neurons_activated,
            "cost_usd": round(out.total_cost, 6),
        }
