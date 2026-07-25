"""Action: proposal.reconsolidate — apply one approved FusionPlan.

Thin action wrapper around reconsolidation.apply.run_reconsolidation so
the whole synthesis is ONE auditable child under the proposal.apply root,
with its own creation/stat-rebuild/refinement/link/rewire children
(parent_action_id = this action). Submitted with idempotency_key
`fusionplan:<plan_hash>`: replaying the same plan returns this action's
recorded receipt instead of re-executing — and because a failed apply is
never committed, the idempotency record only ever exists for successful
applies.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.rbac import UserIdentity
from app.models import Action


class ProposalReconsolidateInput(BaseModel):
    proposal_id: int
    item_id: int | None = None
    fusion_plan: dict
    member_state_hash: str
    total_queries: int = 0
    actor_type: str = "user"


async def handle_proposal_reconsolidate(
    payload: ProposalReconsolidateInput,
    actor: UserIdentity,
    db: AsyncSession,
    action_row: Action,
) -> dict[str, Any]:
    from app.services.reconsolidation.apply import run_reconsolidation
    from app.services.reconsolidation.plan import FusionPlan

    plan = FusionPlan.model_validate(payload.fusion_plan)
    receipt = await run_reconsolidation(
        db, plan, payload.member_state_hash,
        proposal_id=payload.proposal_id, item_id=payload.item_id,
        identity=actor, actor_type=payload.actor_type,
        total_queries=payload.total_queries,
        parent_action_id=action_row.id,
    )
    return {"audit": receipt, "payload": receipt}
