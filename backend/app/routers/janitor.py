"""Memory-janitor trigger endpoints.

POST /janitor/run is the batch entry point for the memory janitors
(consolidation / staleness / decay) — hit by the corvus-mind-janitor
systemd timer or manually. GET /janitor/status reports the current
janitor-relevant graph state without side effects.
"""

import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import IntegrityFinding, Neuron
from app.routers.recall import require_memory_surface
from app.services.mind_janitors import LESSON_TYPES, run_janitors

router = APIRouter(prefix="/janitor", tags=["memory"],
                   dependencies=[Depends(require_memory_surface)])


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
    max_pairs: int = Query(default=40, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Run the selected janitor passes and return the combined report."""
    assert max_pairs >= 1, "max_pairs must be positive"
    report = await run_janitors(
        db, consolidation=consolidation, staleness=staleness,
        decay=decay, max_pairs=max_pairs,
    )
    assert isinstance(report, dict), "janitor report must be a dict"
    return json.loads(json.dumps(report, default=str))
