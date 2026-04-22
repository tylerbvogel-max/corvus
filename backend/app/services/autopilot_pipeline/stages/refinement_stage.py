"""LLM pass — propose graph mutations (updates + new neurons) from eval + gap."""

from typing import Any

from app.services.autopilot_pipeline.state import AutopilotState
from app.services.pipeline.context import PipelineContext


class RefinementStage:
    """Invoke the refine prompt and stash its structured output on the state.

    Reads:  state.query_id, state.config.max_layer, state.config.focus_neuron_id,
            state.config.eval_model, state.gap
    Writes: state.refine_reasoning, state.updates, state.new_neurons,
            state.total_cost (appended)
    """

    name = "refinement"

    async def run(self, state: AutopilotState, _ctx: PipelineContext) -> AutopilotState:
        assert state is not None, "state is required"
        assert state.query_id is not None, "query_id must be set before refinement"

        from app.routers.autopilot import _refine_neurons

        reasoning, updates, new_neurons, refine_cost = await _refine_neurons(
            state.query_id,
            state.config.max_layer,
            state.config.focus_neuron_id,
            state.config.eval_model,
            state.gap,
        )
        state.refine_reasoning = reasoning
        state.updates = updates or []
        state.new_neurons = new_neurons or []
        state.total_cost += refine_cost
        return state

    def describe(self, out: AutopilotState) -> dict[str, Any]:
        return {
            "updates": len(out.updates),
            "new_neurons": len(out.new_neurons),
            "cost_usd": round(out.total_cost, 6),
        }
