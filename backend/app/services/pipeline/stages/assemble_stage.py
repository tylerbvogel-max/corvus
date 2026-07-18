"""Select top-k neurons, hydrate them, and assemble the system prompt."""

from typing import Any

from app.config import settings
from app.services.pipeline.context import PipelineContext
from app.services.pipeline.state import PipelineState


class AssembleStage:
    """Assemble the final system prompt from the top-k scored neurons.

    Reads:  state.all_scored, effective_top_k, intent, effective_budget,
            prior_neuron_ids, resolved_regulations
    Writes: state.top_slice, neuron_map, system_prompt, hop_map
    """

    name = "assemble_prompt"

    async def run(self, state: PipelineState, ctx: PipelineContext) -> PipelineState:
        from app.services.executor import _assemble_top_slice
        top_slice, neuron_map, system_prompt, hop_map, telemetry = await _assemble_top_slice(
            ctx.db,
            state.all_scored,
            state.effective_top_k,
            state.intent,
            state.effective_budget,
            state.prior_neuron_ids,
            state.resolved_regulations,
            requester=state.requester,
        )
        state.top_slice = top_slice
        state.neuron_map = neuron_map
        state.system_prompt = system_prompt
        state.hop_map = hop_map
        state.neurons_delivered = telemetry["neurons_delivered"]
        state.estimated_memory_tokens = telemetry["estimated_memory_tokens"]
        state.memory_context_chars = telemetry["memory_context_chars"]
        state.memory_context_utf8_bytes = telemetry["memory_context_utf8_bytes"]
        state.memory_context_text = telemetry["memory_context_text"]
        state.memory_token_budget = telemetry["memory_token_budget"]
        state.assembly_stop_reason = telemetry["assembly_stop_reason"]
        state.oversized_first_neuron = telemetry["oversized_first_neuron"]
        state.token_estimator_version = telemetry["token_estimator_version"]
        state.memory_representations = telemetry["memory_representations"]
        return state

    def describe(self, out: PipelineState) -> dict[str, Any]:
        return {
            "neurons_activated": (
                out.neurons_activated if settings.token_bounded_assembly_enabled
                else len(out.top_slice)
            ),
            "neurons_delivered": out.neurons_delivered,
            "estimated_memory_tokens": out.estimated_memory_tokens,
            "memory_context_chars": out.memory_context_chars,
            "memory_context_utf8_bytes": out.memory_context_utf8_bytes,
            "memory_token_budget": out.memory_token_budget,
            "assembly_stop_reason": out.assembly_stop_reason,
            "oversized_first_neuron": out.oversized_first_neuron,
            "token_estimator_version": out.token_estimator_version,
            "engrams_resolved": len(out.resolved_regulations),
            "prompt_chars": len(out.system_prompt),
        }
