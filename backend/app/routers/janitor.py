"""Memory-janitor trigger endpoints.

POST /janitor/run is the batch entry point for the memory janitors
(consolidation / staleness / decay) — hit by the corvus-mind-janitor
systemd timer or manually. GET /janitor/status reports the current
janitor-relevant graph state without side effects.
"""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import IntegrityFinding, MemoryChangeEvent, Neuron
from app.services.mind_janitors import LESSON_TYPES, run_janitors

router = APIRouter(prefix="/janitor", tags=["memory"])


@router.get("/status")
async def janitor_status(db: AsyncSession = Depends(get_db)):
    """Janitor-relevant graph state (no LLM, no side effects)."""
    active = (await db.execute(
        select(sa_func.count(Neuron.id)).where(
            Neuron.is_active.is_(True), Neuron.node_type.in_(LESSON_TYPES))
    )).scalar_one()
    absorbed = (await db.execute(
        select(sa_func.count(Neuron.id)).where(
            Neuron.is_active.is_(False), Neuron.superseded_by.isnot(None),
            Neuron.node_type.in_(LESSON_TYPES))
    )).scalar_one()
    open_contradictions = (await db.execute(
        select(sa_func.count(IntegrityFinding.id)).where(
            IntegrityFinding.finding_type == "contradiction",
            IntegrityFinding.status == "open")
    )).scalar_one()
    assert active >= 0 and absorbed >= 0, "counts must be non-negative"
    return {"active_lessons": active, "absorbed_lessons": absorbed,
            "open_contradictions": open_contradictions}


@router.post("/run")
async def janitor_run(
    consolidation: bool = Query(default=True),
    staleness: bool = Query(default=True),
    decay: bool = Query(default=True),
    lint: bool = Query(default=True),
    plasticity: bool = Query(default=True),
    max_pairs: int = Query(default=40, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Run the selected janitor passes and return the combined report."""
    assert max_pairs >= 1, "max_pairs must be positive"
    report = await run_janitors(
        db, consolidation=consolidation, staleness=staleness,
        decay=decay, lint=lint, plasticity=plasticity, max_pairs=max_pairs,
    )
    assert isinstance(report, dict), "janitor report must be a dict"
    return json.loads(json.dumps(report, default=str))


@router.get("/corpus-health")
async def corpus_health_report(db: AsyncSession = Depends(get_db)):
    """Deterministic corpus-quality metrics (graph lint item 0): duplicate
    mass, scope consistency, injection-slot waste. No LLM; persists the
    latest snapshot + trend history beside janitor-report.json."""
    from app.services.mind_lint import corpus_health
    report = await corpus_health(db)
    return json.loads(json.dumps(report, default=str))


def _event_dict(e: MemoryChangeEvent) -> dict:
    return {"field": e.field, "old_value": e.old_value,
            "new_value": e.new_value, "reason": e.reason,
            "actor": e.actor,
            "changed_at": e.changed_at.isoformat() if e.changed_at else None}


@router.get("/history/{neuron_id}")
async def memory_history(neuron_id: int, db: AsyncSession = Depends(get_db)):
    """Temporal view of one memory row (kill-temporal-kg parity).

    Returns the row's change log plus its predecessors — rows it
    superseded, each with a validity window (created_at .. the
    changed_at of the supersession event)."""
    neuron = await db.get(Neuron, neuron_id)
    if neuron is None:
        raise HTTPException(status_code=404, detail="no such neuron")
    events = (await db.execute(
        select(MemoryChangeEvent)
        .where(MemoryChangeEvent.neuron_id == neuron_id)
        .order_by(MemoryChangeEvent.changed_at, MemoryChangeEvent.id)
    )).scalars().all()
    predecessors = (await db.execute(
        select(Neuron).where(Neuron.superseded_by == neuron_id)
    )).scalars().all()
    pred_out = []
    for p in predecessors:  # bounded by supersedes fan-in (JPL-2)
        sup_event = (await db.execute(
            select(MemoryChangeEvent).where(
                MemoryChangeEvent.neuron_id == p.id,
                MemoryChangeEvent.field == "superseded_by",
            ).order_by(MemoryChangeEvent.changed_at.desc()).limit(1)
        )).scalar_one_or_none()
        pred_out.append({
            "id": p.id, "label": p.label, "content": p.content,
            "valid_from": p.created_at.isoformat() if p.created_at else None,
            "valid_to": sup_event.changed_at.isoformat()
            if sup_event and sup_event.changed_at else None,
            "reason": sup_event.reason if sup_event else None,
        })
    return {
        "id": neuron.id, "label": neuron.label, "content": neuron.content,
        "is_active": neuron.is_active, "superseded_by": neuron.superseded_by,
        "valid_from": neuron.created_at.isoformat() if neuron.created_at else None,
        "changes": [_event_dict(e) for e in events],
        "superseded_predecessors": pred_out,
    }


@router.get("/as-of/{neuron_id}")
async def memory_as_of(
    neuron_id: int,
    at: str = Query(description="ISO datetime — reconstruct belief state then"),
    db: AsyncSession = Depends(get_db),
):
    """What did we believe about this memory row at datetime `at`?

    Reverts change-log events newer than `at` (newest first) to
    reconstruct the row's logged fields as they stood at that moment."""
    neuron = await db.get(Neuron, neuron_id)
    if neuron is None:
        raise HTTPException(status_code=404, detail="no such neuron")
    try:
        cutoff = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail="bad ISO datetime")
    # DB timestamps are naive local time (server_default func.now() on a
    # plain TIMESTAMP column) — convert aware input to that frame; naive
    # input is taken as already local.
    if cutoff.tzinfo is not None:
        cutoff = cutoff.astimezone().replace(tzinfo=None)
    if neuron.created_at and neuron.created_at > cutoff:
        return {"id": neuron_id, "as_of": at, "existed": False,
                "note": "row did not exist yet at that datetime"}
    later = (await db.execute(
        select(MemoryChangeEvent).where(
            MemoryChangeEvent.neuron_id == neuron_id,
            MemoryChangeEvent.changed_at > cutoff,
        ).order_by(MemoryChangeEvent.changed_at.desc(), MemoryChangeEvent.id.desc())
    )).scalars().all()
    state = {
        "superseded_by": neuron.superseded_by, "is_active": neuron.is_active,
        "avg_utility": neuron.avg_utility, "authority_level": neuron.authority_level,
    }
    for e in later:  # newest-first revert; bounded by log size (JPL-2)
        if e.field in state:
            state[e.field] = e.old_value
    return {"id": neuron_id, "label": neuron.label, "content": neuron.content,
            "as_of": at, "existed": True, "state": state,
            "reverted_events": len(later),
            "was_current": state["superseded_by"] in (None, "None")
            and str(state["is_active"]).lower() != "false"}
