"""Structured recall + gate-routed remember endpoints.

The read/write surface for harness-memory clients (thin MCP servers and
session hooks are HTTP clients of these): /recall runs the standard
prepare pipeline and returns structured hits instead of an assembled
prompt; /remember persists an evidence-gated lesson via
services.lesson_store (staged proposal → tiered write gate → inline
embed), the same path the episode distiller uses. Domain-agnostic —
clients own their node-type vocabulary and pass abstraction_type
explicitly.
"""

import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.executor import prepare_context
from app.services.lesson_store import save_lesson

router = APIRouter(tags=["memory"])


class RecallRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=25)
    include_content: bool = False


class RememberRequest(BaseModel):
    lesson: str = Field(min_length=1, max_length=8000)
    evidence: str = Field(min_length=1, max_length=4000)
    label: str = Field(min_length=1, max_length=200)
    scope: str | None = Field(default=None, max_length=100)
    node_type: str = Field(default="lesson", max_length=50)
    abstraction_type: str | None = Field(default="principle", max_length=20)
    summary: str | None = Field(default=None, max_length=500)
    authority_level: str = Field(default="informational", max_length=30)


@router.post("/recall")
async def recall(req: RecallRequest, db: AsyncSession = Depends(get_db)):
    """Cheap structured recall: prepare pipeline only, no LLM, no execution."""
    t0 = time.monotonic()
    ctx = await prepare_context(db, req.query, top_k=req.top_k)
    assert ctx is not None, "prepare_context must return a PreparedContext"
    hits = []
    for s in ctx.neuron_scores[:req.top_k]:
        neuron = ctx.neuron_map.get(s["neuron_id"])
        hit = {
            "neuron_id": s["neuron_id"],
            "label": s["label"],
            "summary": s["summary"],
            "scope": s["department"],
            "node_type": neuron.node_type if neuron else None,
            "authority_level": neuron.authority_level if neuron else None,
            "score": round(s["combined"], 4),
        }
        if req.include_content and neuron is not None:
            hit["content"] = neuron.content
        hits.append(hit)
    assert len(hits) <= req.top_k, "hit count must respect top_k"
    return {
        "intent": ctx.intent,
        "scopes": ctx.departments,
        "latency_ms": round((time.monotonic() - t0) * 1000, 1),
        "hits": hits,
    }


@router.post("/remember")
async def remember(req: RememberRequest, db: AsyncSession = Depends(get_db)):
    """Persist an explicit lesson save through the write gate.

    Informational-tier saves auto-commit (and are reclaimed by
    consolidation decay if never reinforced); anything above the
    tenant's auto-commit ceiling queues for human review.
    """
    assert req.lesson.strip(), "lesson must be non-empty"
    assert req.evidence.strip(), "evidence must be non-empty"
    return await save_lesson(
        db, lesson=req.lesson, evidence=req.evidence, label=req.label,
        scope=req.scope, node_type=req.node_type,
        abstraction_type=req.abstraction_type, summary=req.summary,
        authority_level=req.authority_level,
        source_origin="remember_api", gap_source="remember_api",
    )
