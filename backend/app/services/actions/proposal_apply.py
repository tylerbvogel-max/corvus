"""Action: proposal.apply — root container action for a reviewed proposal apply.

This handler does no DB work itself. It exists so that every per-item write
inside the apply (neuron.create, neuron.refine, eventually edge mutations)
can be linked back to a single root via `parent_action_id`. The audit row
records which proposal was applied, by whom, and how many child writes were
expected.

Children are submitted by the apply endpoint after the root returns its id.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.rbac import UserIdentity
from app.models import Action


class ProposalApplyInput(BaseModel):
    proposal_id: int
    item_count: int
    applied_by: str


async def handle_proposal_apply(
    payload: ProposalApplyInput,
    actor: UserIdentity,
    db: AsyncSession,
    action_row: Action,
) -> dict[str, Any]:
    """Record the apply intent. No DB writes — children do the actual work."""
    return {
        "audit": {
            "proposal_id": payload.proposal_id,
            "item_count": payload.item_count,
            "applied_by": payload.applied_by,
        },
        "payload": {"root_action_id": action_row.id},
    }
