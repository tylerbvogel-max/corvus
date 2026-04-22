"""Fetch live regulatory text for fired engrams via eCFR API (cached)."""

from typing import Any

from app.config import settings
from app.services.pipeline.context import PipelineContext
from app.services.pipeline.state import PipelineState


class RegulatoryResolveStage:
    """Resolve the top-N scored engrams to regulatory text (cached, budgeted).

    Reads:  state.scored_engrams, state.effective_budget
    Writes: state.resolved_regulations
    """

    name = "regulatory_resolve"

    async def run(self, state: PipelineState, ctx: PipelineContext) -> PipelineState:
        if not state.scored_engrams or not settings.engram_resolve_enabled:
            state.resolved_regulations = []
            return state
        from sqlalchemy import select
        from app.models import Engram
        from app.services.regulatory_resolve import resolve_engrams
        engram_ids = [s.neuron_id for s in state.scored_engrams[:10]]
        engram_rows = (await ctx.db.execute(
            select(Engram).where(Engram.id.in_(engram_ids))
        )).scalars().all()
        engram_map = {e.id: e for e in engram_rows}
        fired_pairs = [
            (engram_map[s.neuron_id], s.combined)
            for s in state.scored_engrams[:10] if s.neuron_id in engram_map
        ]
        engram_budget = int(state.effective_budget * settings.engram_token_budget_fraction)
        state.resolved_regulations = await resolve_engrams(ctx.db, fired_pairs, engram_budget)
        return state

    def describe(self, out: PipelineState) -> dict[str, Any]:
        resolved = out.resolved_regulations
        return {
            "resolved": len(resolved),
            "cached": sum(1 for r in resolved if getattr(r, "source", None) == "cache"),
            "live": sum(1 for r in resolved if getattr(r, "source", None) == "live_api"),
            "fallback": sum(1 for r in resolved if getattr(r, "source", None) == "fallback_summary"),
        }
