"""Memory-organ metrics endpoint (the tailored Evaluate surface).

GET /metrics/mind — performance (recall latency + stages, injection
coverage, distiller funnel, janitor/compiler activity, cost ledger) and
growth (lesson corpus + daily timeseries). Read-only, no LLM.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.mind_metrics import collect_all

router = APIRouter(prefix="/metrics", tags=["memory"])


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


@router.get("/mind/injection-channels")
async def mind_injection_channels():
    """Load-bearing rate split by delivery channel (standing vs retrieved).

    Standing content succeeds by being present and retrieved content by
    being relevant, so the pooled rate judges neither. This is the
    before/after instrument for any change to charter membership."""
    from app.services.injection_channel import reconstruct_history
    report = reconstruct_history()
    assert isinstance(report, dict), "channel report must be a dict"
    return report


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


@router.get("/mind/subscription")
async def mind_subscription():
    """Live Claude subscription utilization (5-hour block + weekly limits)."""
    from app.services.claude_usage import subscription_report
    report = await subscription_report()
    assert isinstance(report, dict), "subscription report must be a dict"
    return report


@router.get("/mind/subscription/codex")
async def mind_codex_subscription():
    """Live Codex subscription rate limits and token activity."""
    from app.services.codex_usage import subscription_report
    report = await subscription_report()
    assert isinstance(report, dict), "Codex subscription report must be a dict"
    return report


@router.get("/mind/locomo-run")
async def mind_locomo_run():
    """LoCoMo certificate phase status, derived from run logs on disk."""
    from app.services.locomo_run_status import run_status
    report = run_status()
    assert isinstance(report, dict), "locomo run status must be a dict"
    return report


@router.get("/mind/skills")
async def mind_skills(db: AsyncSession = Depends(get_db)):
    """Compiled skills with source health and rendered bodies."""
    from app.services.mind_metrics import (
        skills_report, uncompiled_skill_invocations,
    )
    rows = await skills_report(db)
    assert isinstance(rows, list), "skills report must be a list"
    others = uncompiled_skill_invocations({r.get("name") for r in rows})
    return {"skills": rows, "uncompiled_invocations": others}
