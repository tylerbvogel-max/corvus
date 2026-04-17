"""Actions: eval.run.start and eval.run.complete — append-only audit markers.

These handlers are deliberately pure audit bookkeeping. The actual EvalRun /
EvalRunCase row writes happen inside ``services/eval_runs.py`` because they
span multiple SAVEPOINTs (per-case commits) and integrate with the live
query pipeline. The action rows exist so every run has a root audit entry
that the lineage tree can point to, same pattern as ``output.policy.check``.

Pattern #3 — Evals as immutable, first-class artifacts.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.rbac import UserIdentity
from app.models import Action


class EvalRunStartInput(BaseModel):
    """Snapshot of what's about to run. Immutable once recorded."""

    suite_name: str
    suite_hash: str
    tenant_id: str
    model_versions: dict = Field(default_factory=dict)
    scoring_engine_version: str
    case_count: int = Field(..., ge=0)


class EvalRunCompleteInput(BaseModel):
    """Terminal verdict of the run. Appended after all cases finish."""

    eval_run_id: int
    status: str  # "completed" | "failed"
    case_count: int = Field(..., ge=0)
    blocked_count: int = Field(..., ge=0)
    violation_count: int = Field(..., ge=0)
    error_count: int = Field(..., ge=0)


async def handle_eval_run_start(
    payload: EvalRunStartInput,
    actor: UserIdentity,
    db: AsyncSession,  # noqa: ARG001
    action_row: Action,  # noqa: ARG001
) -> dict[str, Any]:
    """Record the run-start audit entry. No mutation — the runner creates
    the EvalRun row separately and links back via EvalRun.action_id."""
    return {
        "audit": {
            "suite_name": payload.suite_name,
            "suite_hash": payload.suite_hash,
            "tenant_id": payload.tenant_id,
            "case_count": payload.case_count,
            "scoring_engine_version": payload.scoring_engine_version,
            "actor": actor.user_id,
        },
        "payload": None,
    }


async def handle_eval_run_complete(
    payload: EvalRunCompleteInput,
    actor: UserIdentity,
    db: AsyncSession,  # noqa: ARG001
    action_row: Action,  # noqa: ARG001
) -> dict[str, Any]:
    """Record the run-complete audit entry. EvalRun row transitioned to
    terminal state by the runner; this is the audit twin."""
    return {
        "audit": {
            "eval_run_id": payload.eval_run_id,
            "status": payload.status,
            "case_count": payload.case_count,
            "blocked_count": payload.blocked_count,
            "violation_count": payload.violation_count,
            "error_count": payload.error_count,
            "actor": actor.user_id,
        },
        "payload": None,
    }
