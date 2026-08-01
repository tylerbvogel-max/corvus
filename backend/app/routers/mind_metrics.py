"""Memory-organ metrics endpoint (the tailored Evaluate surface).

GET /metrics/mind — performance (recall latency + stages, injection
coverage, distiller funnel, janitor/compiler activity, cost ledger) and
growth (lesson corpus + daily timeseries). Read-only, no LLM.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
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
async def mind_injection_channels(since: str | None = None,
                                  until: str | None = None):
    """Load-bearing rate split by delivery channel (standing vs retrieved).

    Standing content succeeds by being present and retrieved content by
    being relevant, so the pooled rate judges neither. This is the
    before/after instrument for any change to charter membership.

    `since`/`until` are ISO-8601 bounds on injection VOLUME only, bucketing
    sessions by their first injection. Load-bearing rates stay lifetime:
    they depend on attribution verdicts that arrive whenever a session is
    distilled, so windowing them by session start would mix a windowed
    numerator with an unwindowed denominator.
    """
    from app.services.injection_channel import reconstruct_history, standing_volume

    def _bound(raw: str | None, name: str) -> datetime | None:
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail=f"{name} must be ISO-8601, got {raw!r}")

    lo, hi = _bound(since, "since"), _bound(until, "until")
    if lo and hi and hi <= lo:
        raise HTTPException(status_code=422, detail="until must be after since")

    report = reconstruct_history()
    assert isinstance(report, dict), "channel report must be a dict"
    report["volume_by_channel"] = standing_volume(since=lo, until=hi)
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
