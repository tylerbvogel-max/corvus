"""Reconsolidation-auditor trigger endpoints (scheduled doubt).

POST /auditor/run is the batch entry point — hit by the
corvus-mind-auditor systemd timer (mode=auto: evidence-time cadence
decides light/deep/skip) or manually with an explicit mode. POST
/auditor/audit/{neuron_id} is the event-driven pass for a single suspect
memory. GET endpoints are side-effect-free observability.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.routers.recall import require_memory_surface
from app.services.memory_quality_auditor import (
    _prior_ran_at, _sessions_distilled_since, auditor_metrics, run_audit,
)

router = APIRouter(prefix="/auditor", tags=["memory"],
                   dependencies=[Depends(require_memory_surface)])


@router.get("/status")
async def auditor_status():
    """Watermark, evidence-clock position, and last-run summary."""
    prior = _prior_ran_at()
    return {
        "watermark": prior,
        "fresh_sessions_since_watermark": _sessions_distilled_since(prior),
        **auditor_metrics(),
    }


@router.post("/run")
async def auditor_run(
    mode: str = Query(default="auto",
                      pattern="^(auto|light|deep)$"),
    max_candidates: int | None = Query(default=None, ge=1, le=200),
    max_critic: int | None = Query(default=None, ge=0, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Run one auditor pass. mode=auto lets the evidence clock decide;
    light/deep force the pass (deep = admin request per the cadence spec)."""
    report = await run_audit(db, mode=mode, max_candidates=max_candidates,
                             max_critic=max_critic,
                             trigger="admin" if mode != "auto" else "timer")
    assert isinstance(report, dict), "auditor report must be a dict"
    return json.loads(json.dumps(report, default=str))


@router.post("/audit/{neuron_id}")
async def audit_one(neuron_id: int, db: AsyncSession = Depends(get_db)):
    """Event-driven pass: reopen one memory now (contradicted attribution,
    user correction, incident participation)."""
    report = await run_audit(db, mode="event", neuron_ids=[neuron_id],
                             trigger="event")
    if report.get("missing_neurons"):
        raise HTTPException(
            status_code=404,
            detail=f"neuron {neuron_id} is not an active auditable lesson")
    return json.loads(json.dumps(report, default=str))


@router.get("/candidates")
async def auditor_candidates(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Dry-run microglial surveillance: score the corpus, return the
    ranked risk breakdowns. No LLM, no proposals, no watermark movement."""
    from app.services.memory_quality_auditor import (
        build_scoring_context, score_neuron)
    from app.services.mind_janitors import _load_lessons

    lessons = await _load_lessons(db)
    ctx = await build_scoring_context(db, lessons)
    scored = sorted((score_neuron(n, ctx) for n in lessons),
                    key=lambda s: -s["risk_score"])
    return {"corpus": len(lessons), "top": scored[:limit]}
