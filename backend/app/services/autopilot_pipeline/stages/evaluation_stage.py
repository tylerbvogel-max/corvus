"""Self-evaluate the neuron-enhanced response quality for the generated query."""

from typing import Any

from app.services.autopilot_pipeline.state import AutopilotState
from app.services.pipeline.context import PipelineContext


class EvaluationStage:
    """LLM self-evaluation — accuracy/completeness/clarity/faithfulness/overall.

    Reads:  state.query_id, state.config.eval_model
    Writes: state.eval_overall, state.eval_text, state.total_cost (appended)
    """

    name = "evaluation"

    async def run(self, state: AutopilotState, _ctx: PipelineContext) -> AutopilotState:
        assert state is not None, "state is required"
        assert state.query_id is not None, "query_id must be set before evaluation"

        from app.routers.autopilot import _evaluate_response

        eval_overall, eval_text, eval_cost = await _evaluate_response(
            state.query_id, state.config.eval_model,
        )
        state.eval_overall = eval_overall
        state.eval_text = eval_text
        state.total_cost += eval_cost
        return state

    def describe(self, out: AutopilotState) -> dict[str, Any]:
        return {
            "eval_overall": out.eval_overall,
            "cost_usd": round(out.total_cost, 6),
        }
