"""Scan the neuron graph for the most promising gap this tick should target."""

from typing import Any

from app.services.autopilot_pipeline.state import AutopilotState
from app.services.pipeline.context import PipelineContext
from app.services.pipeline.stage import ShortCircuit


class GapDetectionStage:
    """Detect one gap + gather focus-area context + recent-query dedup list.

    Reads:  state.config.focus_neuron_id
    Writes: state.gap, state.scored_gap, state.focus_label, state.focus_context,
            state.recent_queries, state.gap_source, state.gap_target_desc

    Short-circuit: if the detector refuses to emit a target AND the directive
    is empty, the tick becomes a no-op — no query to generate. Current policy
    is to always fall back to directive-based queries, so we never raise
    ShortCircuit today; the hook is kept for future no-op policies.
    """

    name = "gap_detection"

    async def run(self, state: AutopilotState, _ctx: PipelineContext) -> AutopilotState:
        assert state is not None, "state is required"
        assert state.config is not None, "state.config is required"

        from app.routers.autopilot import _detect_and_gather_context

        gap, scored_gap, focus_label, focus_ctx, recent_qs, gap_src, gap_desc = (
            await _detect_and_gather_context(
                state.config.focus_neuron_id,
                region=getattr(state.config, "region", None),
            )
        )

        state.gap = gap
        state.scored_gap = scored_gap
        state.focus_label = focus_label
        state.focus_context = focus_ctx
        state.recent_queries = recent_qs
        state.gap_source = gap_src
        state.gap_target_desc = gap_desc

        if gap is None and not (state.config.directive or "").strip():
            raise ShortCircuit(state)

        return state

    def describe(self, out: AutopilotState) -> dict[str, Any]:
        return {
            "gap_source": out.gap_source,
            "has_gap": out.gap is not None,
            "focus_label": out.focus_label,
            "recent_count": len(out.recent_queries),
        }
