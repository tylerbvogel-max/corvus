"""Memory-organ metrics endpoint (the tailored Evaluate surface).

GET /metrics/mind — performance (recall latency + stages, injection
coverage, distiller funnel, janitor/compiler activity, cost ledger) and
growth (lesson corpus + daily timeseries). Read-only, no LLM.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.routers.recall import require_memory_surface
from app.services.mind_metrics import collect_all

router = APIRouter(prefix="/metrics", tags=["memory"],
                   dependencies=[Depends(require_memory_surface)])


@router.get("/mind")
async def mind_metrics(db: AsyncSession = Depends(get_db)):
    """Aggregate memory-organ performance and growth metrics."""
    report = await collect_all(db)
    assert isinstance(report, dict), "metrics report must be a dict"
    return report


@router.get("/mind/sessions")
async def mind_sessions():
    """Episode-log browser: captured/backfilled sessions + distill status."""
    from app.services.mind_metrics import sessions_report
    rows = sessions_report()
    assert isinstance(rows, list), "sessions report must be a list"
    return {"sessions": rows}


@router.get("/mind/trust")
async def mind_trust(db: AsyncSession = Depends(get_db)):
    """Per-lesson trust trajectories (utility over time)."""
    from app.services.mind_metrics import trust_report
    rows = await trust_report(db)
    assert isinstance(rows, list), "trust report must be a list"
    return {"lessons": rows}


@router.get("/mind/inbox")
async def mind_inbox(db: AsyncSession = Depends(get_db)):
    """Everything awaiting human judgment: findings, proposals, borderline pairs."""
    from app.services.mind_metrics import inbox_report
    report = await inbox_report(db)
    assert isinstance(report, dict), "inbox report must be a dict"
    return report


@router.get("/mind/skills")
async def mind_skills(db: AsyncSession = Depends(get_db)):
    """Compiled skills with source health and rendered bodies."""
    from app.services.mind_metrics import skills_report
    rows = await skills_report(db)
    assert isinstance(rows, list), "skills report must be a list"
    return {"skills": rows}
