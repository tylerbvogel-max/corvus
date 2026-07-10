"""Skill-compiler trigger endpoints.

POST /compile/run is the batch entry point (corvus-mind-compile timer,
daily) — runs the reverse staleness check, then compiles up to the
per-run cap of eligible lesson clusters into ~/.claude/skills/mind-*.
GET /compile/status previews eligibility with no LLM and no writes.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.routers.recall import require_memory_surface
from app.services.mind_janitors import _load_lessons
from app.services.skill_compiler import _load_manifest, find_clusters, run_compile

router = APIRouter(prefix="/compile", tags=["memory"],
                    dependencies=[Depends(require_memory_surface)])


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
    report = await run_compile(db)
    assert isinstance(report, dict), "compile report must be a dict"
    return report
