"""Query-generation dispatch — Pattern #6 BranchStage consumer.

Two sibling sub-stages (one per prompt template), dispatched by a
BranchStage router that picks between them based on `state.gap_source`.
Replaces the earlier single `QueryGenerationStage` that hid the branch
inside `_generate_query` as an `if gap.source != "directive"` conditional.
"""

from typing import Any, Mapping, Sequence

from app.services.autopilot_pipeline.state import AutopilotState
from app.services.pipeline.context import PipelineContext


class GapTargetedGenerationStage:
    """Craft a query targeted at the gap detected this tick.

    Reads:  state.config.directive, state.recent_queries, state.focus_context, state.gap
    Writes: state.generated_query, state.total_cost (appended)
    """

    name = "query_generation_gap_targeted"

    def __init__(self) -> None:
        self._last_cost: float = 0.0

    async def run(self, state: AutopilotState, _ctx: PipelineContext) -> AutopilotState:
        assert state is not None, "state is required"
        assert state.gap is not None, "gap_targeted stage requires state.gap to be set"

        from app.routers.autopilot import _generate_query_gap_targeted, _set_step

        _set_step("generate", "Generating gap-targeted query...")
        text, cost = await _generate_query_gap_targeted(
            state.config.directive,
            state.recent_queries,
            state.focus_context,
            state.gap,
        )
        self._last_cost = cost
        state.generated_query = text
        state.total_cost += cost
        return state

    def describe(self, out: AutopilotState) -> dict[str, Any]:
        return {
            "query_chars": len(out.generated_query or ""),
            "cost_usd": round(self._last_cost, 6),
        }


class DirectiveGenerationStage:
    """Fallback: craft a directive-based exploratory query (no gap to probe).

    Reads:  state.config.directive, state.recent_queries, state.focus_context
    Writes: state.generated_query, state.total_cost (appended)
    """

    name = "query_generation_directive"

    def __init__(self) -> None:
        self._last_cost: float = 0.0

    async def run(self, state: AutopilotState, _ctx: PipelineContext) -> AutopilotState:
        assert state is not None, "state is required"
        assert state.config is not None, "state.config is required"

        from app.routers.autopilot import _generate_query_directive, _set_step

        _set_step("generate", "Generating directive-based query...")
        text, cost = await _generate_query_directive(
            state.config.directive,
            state.recent_queries,
            state.focus_context,
        )
        self._last_cost = cost
        state.generated_query = text
        state.total_cost += cost
        return state

    def describe(self, out: AutopilotState) -> dict[str, Any]:
        return {
            "query_chars": len(out.generated_query or ""),
            "cost_usd": round(self._last_cost, 6),
        }


class QueryGenerationRouter:
    """BranchStage — pick between gap-targeted and directive generation.

    Routes by `state.gap_source`: anything other than `""`/`"directive"` picks
    the gap-targeted sub-pipeline; otherwise the directive fallback runs.

    Reads:  state.gap, state.gap_source
    Writes: (via whichever sub-pipeline runs) state.generated_query, total_cost
    """

    name = "query_generation_router"

    def __init__(self) -> None:
        self.branches: Mapping[str, Sequence[Any]] = {
            "gap_targeted": [GapTargetedGenerationStage()],
            "directive": [DirectiveGenerationStage()],
        }

    async def route(self, state: AutopilotState, _ctx: PipelineContext) -> str:
        assert state is not None, "state is required"
        if state.gap is not None and state.gap_source not in ("", "directive"):
            return "gap_targeted"
        return "directive"

    def describe(self, _out: AutopilotState) -> dict[str, Any]:
        return {}
