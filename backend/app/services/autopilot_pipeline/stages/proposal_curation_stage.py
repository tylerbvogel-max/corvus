"""Stage proposed graph mutations as an AutopilotProposal, then consult the
tiered write gate: observational writes auto-commit with audit; authoritative
writes stay in the human queue (plat-write-gate)."""

from typing import Any

from app.database import async_session
from app.models import AutopilotProposal, Query
from app.services.autopilot_pipeline.state import AutopilotState
from app.services.pipeline.context import PipelineContext


class ProposalCurationStage:
    """Load the assembled prompt for hashing, stage the proposal row, then
    route it through the write gate.

    Reads:  state.query_id, state.updates, state.new_neurons, state.scored_gap,
            state.refine_reasoning, state.eval_overall, state.eval_text,
            state.config.eval_model
    Writes: state.assembled_prompt, state.proposal_id, state.gate_route
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
        state.gate_route = await self._route_through_gate(state)
        return state

    async def _route_through_gate(self, state: AutopilotState) -> str:
        """Consult the write gate. Autopilot writes carry the self-eval score
        as confidence; guardrails are not applicable to this path (None)."""
        if state.proposal_id is None:
            return "queue"
        from app.services.write_gate import route_proposal
        async with async_session() as sp:
            proposal = await sp.get(AutopilotProposal, state.proposal_id)
            if proposal is None or proposal.state != "proposed":
                return "queue"
            decision = await route_proposal(
                sp, proposal,
                guardrails_passed=None,
                confidence=(state.eval_overall or 0) / 5.0,
                region=getattr(state.config, "region", None),
            )
            await sp.commit()
            return decision.route

    def describe(self, out: AutopilotState) -> dict[str, Any]:
        return {
            "proposal_id": out.proposal_id,
            "gate_route": getattr(out, "gate_route", None),
            "prompt_chars": len(out.assembled_prompt or ""),
        }
