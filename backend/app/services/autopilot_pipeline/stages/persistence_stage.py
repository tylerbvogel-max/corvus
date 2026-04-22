"""Record the AutopilotRun row and back-link the staged proposal to it."""

from typing import Any

from app.database import async_session
from app.models import AutopilotProposal
from app.services.autopilot_pipeline.state import AutopilotState
from app.services.pipeline.context import PipelineContext


class PersistenceStage:
    """Write AutopilotRun + back-link the proposal; mark the tick complete.

    Reads:  state.generated_query, state.total_cost, state.proposal_id,
            and the projected fields from state.run_fields()
    Writes: state.run_id
    """

    name = "persistence"

    async def run(self, state: AutopilotState, _ctx: PipelineContext) -> AutopilotState:
        assert state is not None, "state is required"
        assert state.proposal_id is not None, "proposal_id must be set before persistence"

        from app.routers.autopilot import _record_run

        run = await _record_run(
            "completed",
            generated_query=state.generated_query,
            cost_usd=state.total_cost,
            **state.run_fields(),
        )
        state.run_id = run.id

        async with async_session() as link:
            prop = await link.get(AutopilotProposal, state.proposal_id)
            if prop is not None:
                prop.autopilot_run_id = run.id
                await link.commit()

        return state

    def describe(self, out: AutopilotState) -> dict[str, Any]:
        return {
            "run_id": out.run_id,
            "proposal_id": out.proposal_id,
            "cost_usd": round(out.total_cost, 6),
        }
