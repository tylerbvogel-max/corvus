"""Reflective-cycle trigger and inspection endpoints.

POST /compile/run is the daily batch entry point. It reviews derived models,
checks projection staleness, and compiles bounded new lesson clusters.
GET /compile/status previews eligibility; GET /compile/models inspects model
admission and challenge receipts. Both reads avoid LLM calls and writes.
"""

from fastapi import APIRouter, Depends
from collections import Counter
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.observability.jobs import scheduled_http_run as scheduled_run
from app.observability.job_outcomes import attach_outcome
from app.services.mind_janitors import _load_lessons
from app.services.skill_compiler import _load_manifest, find_clusters, run_compile
from app.services import reflection_models
from app.tenant import tenant

router = APIRouter(prefix="/compile", tags=["memory"])


@router.get("/models")
async def compile_models():
    """Inspect derived models, their falsifiers, evidence, and history."""
    catalog = reflection_models.load_catalog()
    counts = Counter(record.get("status", "unknown")
                     for record in catalog["records"].values())
    return {"schema_version": catalog["schema_version"],
            "counts": dict(counts), "records": catalog["records"]}


@router.get("/status")
async def compile_status(db: AsyncSession = Depends(get_db)):
    """Eligible clusters + current manifest (no LLM, no side effects)."""
    lessons = await _load_lessons(db)
    clusters = find_clusters(lessons)
    manifest = _load_manifest()
    assert isinstance(manifest, list), "manifest must be a list"
    return {
        "lessons": len(lessons),
        "clusters": [
            {"scope": c[0].department, "size": len(c),
             "members": [{"id": x.id, "label": x.label} for x in c]}
            for c in clusters
        ],
        "compiled": manifest,
    }


@router.post("/run")
async def compile_run(db: AsyncSession = Depends(get_db)):
    """Reverse check + compile eligible clusters (bounded Opus spend)."""
    with scheduled_run("compile", tenant.tenant_id) as detail:
        report = await run_compile(db)
        assert isinstance(report, dict), "compile report must be a dict"
        report = attach_outcome("compile", report, detail)
    return report
