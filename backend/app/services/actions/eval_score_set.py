"""Action: eval.score.set — replace the EvalScore rows for a query.

First action wired through the action bus (AIP governance roadmap, Phase 1,
pattern #1, Step 1). Both the user-driven `evaluate_query` endpoint and the
autopilot eval helper now go through this handler.

Input shape mirrors what the previous _save_eval_scores helper accepted:
a query_id, the evaluator model name, the parsed per-answer score rows, and
a verdict string.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.rbac import UserIdentity
from app.models import Action, EvalScore


class EvalScoreRow(BaseModel):
    # Scores are 1-5 in half steps: the unrounded mean of the counterbalanced
    # judge passes (each pass scores integer 1-5). Migration 019.
    answer_label: str = Field(..., max_length=8)
    answer_mode: str = Field(..., max_length=80)
    accuracy: float = Field(..., ge=1, le=5)
    completeness: float = Field(..., ge=1, le=5)
    clarity: float = Field(..., ge=1, le=5)
    faithfulness: float = Field(..., ge=1, le=5)
    overall: float = Field(..., ge=1, le=5)


class EvalScoreSetInput(BaseModel):
    query_id: int
    eval_model: str = Field(..., max_length=80)
    verdict: str
    scores: list[EvalScoreRow]


async def handle_eval_score_set(
    payload: EvalScoreSetInput,
    actor: UserIdentity,
    db: AsyncSession,
    action_row: Action,
) -> dict[str, Any]:
    """Replace any existing EvalScore rows for this query with the new ones."""
    # Delete prior scores for this query.
    old = await db.execute(
        select(EvalScore).where(EvalScore.query_id == payload.query_id)
    )
    deleted_count = 0
    for row in old.scalars():
        await db.delete(row)
        deleted_count += 1

    # Insert new score rows.
    inserted_ids: list[int] = []
    for score in payload.scores:
        new_row = EvalScore(
            query_id=payload.query_id,
            eval_model=payload.eval_model,
            answer_mode=score.answer_mode,
            answer_label=score.answer_label,
            accuracy=score.accuracy,
            completeness=score.completeness,
            clarity=score.clarity,
            faithfulness=score.faithfulness,
            overall=score.overall,
            verdict=payload.verdict,
        )
        db.add(new_row)
        await db.flush()
        assert new_row.id is not None, "EvalScore row must have id after flush"
        inserted_ids.append(new_row.id)

    return {
        "audit": {
            "query_id": payload.query_id,
            "eval_model": payload.eval_model,
            "deleted": deleted_count,
            "inserted": len(inserted_ids),
            "inserted_ids": inserted_ids,
        },
        "payload": {"score_ids": inserted_ids},
    }
