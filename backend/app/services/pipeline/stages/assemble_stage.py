"""Select top-k neurons, hydrate them, and assemble the system prompt."""

from typing import Any

from app.services.pipeline.context import PipelineContext
from app.services.pipeline.state import PipelineState


class AssembleStage:
    """Assemble the final system prompt from the top-k scored neurons.

    Reads:  state.all_scored, effective_top_k, intent, effective_budget,
            prior_neuron_ids, resolved_regulations
    Writes: state.top_slice, neuron_map, system_prompt
    """

    name = "assemble_prompt"

    async def run(self, state: PipelineState, ctx: PipelineContext) -> PipelineState:
        from app.services.executor import _assemble_top_slice
        top_slice, neuron_map, system_prompt = await _assemble_top_slice(
            ctx.db,
            state.all_scored,
            state.effective_top_k,
            state.intent,
            state.effective_budget,
            state.prior_neuron_ids,
            state.resolved_regulations,
        )
        state.top_slice = top_slice
        state.neuron_map = neuron_map
        state.system_prompt = system_prompt
        return state

    def describe(self, out: PipelineState) -> dict[str, Any]:
        return {
            "neurons_activated": len(out.top_slice),
            "engrams_resolved": len(out.resolved_regulations),
            "prompt_chars": len(out.system_prompt),
        }
