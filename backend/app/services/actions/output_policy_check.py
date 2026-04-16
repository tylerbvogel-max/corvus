"""Action: output.policy.check — audit row for a runtime output_guard run.

This action is intentionally a no-op at the handler level: ``output_guard``
has already done the persistence of OutputViolation rows before submitting
the action, and the action exists solely to give the check a single root
audit entry that ties all violations together (parent_action_id style).

Pattern #7 — Runtime output policy gates (Phase 1.5 GTM gate).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.rbac import UserIdentity
from app.models import Action


class OutputPolicyCheckInput(BaseModel):
    """Static record of what the guard saw at check time."""

    query_id: int | None = None
    rule_count: int = Field(..., ge=0)
    blocked: bool = False
    redacted: bool = False
    rules: list[str] = Field(default_factory=list)


async def handle_output_policy_check(
    payload: OutputPolicyCheckInput,
    actor: UserIdentity,
    db: AsyncSession,  # noqa: ARG001 — kept for handler signature uniformity
    action_row: Action,  # noqa: ARG001
) -> dict[str, Any]:
    """Record the gate's summary in the audit row; no mutation."""
    return {
        "audit": {
            "query_id": payload.query_id,
            "rule_count": payload.rule_count,
            "blocked": payload.blocked,
            "redacted": payload.redacted,
            "rules": payload.rules,
            "actor": actor.user_id,
        },
        "payload": None,
    }
