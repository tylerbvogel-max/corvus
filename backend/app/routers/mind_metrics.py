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


@router.get("/mind/delivery-pathways")
async def mind_delivery_pathways(db: AsyncSession = Depends(get_db)):
    """Habituation ledger (mind-delivery-plasticity): per-pathway counters
    and states, plus the hot-path projection currently in force. Read-only —
    the sole writer is the plasticity janitor pass."""
    import json as _json
    import os as _os

    from sqlalchemy import select

    from app.models import DeliveryPathway
    from app.services.delivery_plasticity import (
        PROJECTION_PATH, STATE_FLOORS, false_kill_receipts)

    rows = (await db.execute(
        select(DeliveryPathway).order_by(
            DeliveryPathway.state, DeliveryPathway.neuron_id)
    )).scalars().all()
    by_state: dict[str, int] = {}
    for r in rows:
        by_state[r.state] = by_state.get(r.state, 0) + 1
    projection = None
    try:
        with open(_os.path.expanduser(PROJECTION_PATH), encoding="utf-8") as fh:
            projection = _json.load(fh)
    except (OSError, ValueError):
        pass
    receipts = false_kill_receipts()
    return {
        "pathways": len(rows), "by_state": by_state,
        "floors": STATE_FLOORS,
        # ZERO FALSE KILLS, re-grounded (mind-recurrence-watch): counted
        # from verified recurrence events — ground-truth harm in withheld
        # sessions — never from reward absence. The proxy that nominates
        # trials does not grade its own outcomes.
        "false_kills": {
            "verified": len(receipts),
            "grounding": "recurrence events (exogenous), not reward absence",
            "receipts": receipts[-20:],
        },
        "projection": projection,
        "rows": [{
            "neuron_id": r.neuron_id, "trigger": r.trigger,
            "tool": r.tool or None, "state": r.state,
            "delivered_n": r.delivered_n, "rewarded_n": r.rewarded_n,
            "penalized_n": r.penalized_n,
            "last_delivered_at": r.last_delivered_at,
            "last_rewarded_at": r.last_rewarded_at,
            "state_changed_at": r.state_changed_at,
            "proposal_id": r.proposal_id,
        } for r in rows],
    }


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
