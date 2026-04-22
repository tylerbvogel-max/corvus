"""Stage proposed graph mutations as an AutopilotProposal awaiting human approval."""

from typing import Any

from app.database import async_session
from app.models import Query
from app.services.autopilot_pipeline.state import AutopilotState
from app.services.pipeline.context import PipelineContext


class ProposalCurationStage:
    """Load the assembled prompt for hashing, then stage the proposal row.

    Reads:  state.query_id, state.updates, state.new_neurons, state.scored_gap,
            state.refine_reasoning, state.eval_overall, state.eval_text,
            state.config.eval_model
    Writes: state.assembled_prompt, state.proposal_id
    """

    name = "proposal_curation"

    async def run(self, state: AutopilotState, _ctx: PipelineContext) -> AutopilotState:
        assert state is not None, "state is required"
        assert state.query_id is not None, "query_id must be set before proposal curation"

        from app.routers.autopilot import _create_proposal

        async with async_session() as sp:
            query = await sp.get(Query, state.query_id)
            state.assembled_prompt = query.assembled_prompt if query else None

        state.proposal_id = await _create_proposal(
            query_id=state.query_id,
            updates=state.updates,
            new_neurons=state.new_neurons,
            scored_gap=state.scored_gap,
            reasoning=state.refine_reasoning,
            eval_overall=state.eval_overall,
            eval_text=state.eval_text,
            llm_model=state.config.eval_model,
            assembled_prompt=state.assembled_prompt,
        )
        return state

    def describe(self, out: AutopilotState) -> dict[str, Any]:
        return {
            "proposal_id": out.proposal_id,
            "prompt_chars": len(out.assembled_prompt or ""),
        }
