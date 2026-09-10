"""Distillation trigger endpoint.

POST /distill/run is the batch entry point for the episode distiller —
hit by the corvus-mind-distill systemd timer (curl-triggered one-shot) or
manually. Per-run session cap bounds Opus spend. Database checkpoints govern
retry and appended input; filesystem markers are recoverable projections.
Ambiguous historical boundaries remain blocked rather than being replayed.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.observability.jobs import scheduled_http_run as scheduled_run
from app.observability.job_outcomes import attach_outcome
from app.services.distiller import MAX_SESSIONS_PER_RUN, EPISODE_DIR, run_distillation
from app.services.distillation_progress import progress_status
from app.tenant import tenant

router = APIRouter(prefix="/distill", tags=["memory"])


@router.get("/status")
async def distill_status(min_quiet_minutes: int = Query(default=30, ge=0, le=1440),
                         db: AsyncSession = Depends(get_db)):
    """Ready-to-distill episode logs (no LLM, no side effects)."""
    return await progress_status(db, EPISODE_DIR, min_quiet_minutes)


@router.post("/run")
async def distill_run(
    limit: int = Query(default=MAX_SESSIONS_PER_RUN, ge=1, le=10),
    min_quiet_minutes: int = Query(default=30, ge=0, le=1440),
    db: AsyncSession = Depends(get_db),
):
    """Distill up to `limit` ready session logs into candidate lessons."""
    assert limit >= 1, "limit must be positive"
    with scheduled_run("distill", tenant.tenant_id) as detail:
        report = await run_distillation(
            db, limit=limit, min_quiet_minutes=min_quiet_minutes)
        detail["limit"] = limit
        report = attach_outcome("distill", report, detail)
    return report
