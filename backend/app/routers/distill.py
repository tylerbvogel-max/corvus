"""Distillation trigger endpoint.

POST /distill/run is the batch entry point for the episode distiller —
hit by the corvus-mind-distill systemd timer (autopilot-curl pattern) or
manually. Per-run session cap bounds Opus spend; failed logs keep no
marker and retry on the next run.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.routers.recall import require_memory_surface
from app.services.distiller import MAX_SESSIONS_PER_RUN, find_ready_logs, run_distillation

router = APIRouter(prefix="/distill", tags=["memory"],
                   dependencies=[Depends(require_memory_surface)])


@router.get("/status")
async def distill_status(min_quiet_minutes: int = Query(default=30, ge=0, le=1440)):
    """Ready-to-distill episode logs (no LLM, no side effects)."""
    ready = find_ready_logs(min_quiet_minutes=min_quiet_minutes)
    assert isinstance(ready, list), "find_ready_logs must return a list"
    return {"ready": len(ready), "paths": ready}


@router.post("/run")
async def distill_run(
    limit: int = Query(default=MAX_SESSIONS_PER_RUN, ge=1, le=10),
    min_quiet_minutes: int = Query(default=30, ge=0, le=1440),
    db: AsyncSession = Depends(get_db),
):
    """Distill up to `limit` ready session logs into candidate lessons."""
    assert limit >= 1, "limit must be positive"
    return await run_distillation(db, limit=limit, min_quiet_minutes=min_quiet_minutes)
