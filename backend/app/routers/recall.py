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

import json
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.executor import prepare_context
from app.services.lesson_store import save_lesson
from app.tenant import tenant

router = APIRouter(tags=["memory"])


def require_memory_surface() -> None:
    """404 the memory endpoints on knowledge tenants (aero/flow): they
    would write harness episodes and lessons into the wrong graph."""
    if not tenant.memory_surface_enabled:
        raise HTTPException(
            status_code=404,
            detail="memory surface disabled for this tenant (tenant.yaml memory_surface)",
        )


class RecallRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=25)
    include_content: bool = False
    # Telemetry: who initiated this recall (hook trigger, mcp, api) and
    # whether to persist a Query row + firing records. Persisted recalls
    # feed the Evaluate/Performance pages, lesson invocation counts, and
    # the decay auditor's recalled-often signal.
    source: str = Field(default="api", max_length=40)
    persist: bool = True


class RememberRequest(BaseModel):
    lesson: str = Field(min_length=1, max_length=8000)
    evidence: str = Field(min_length=1, max_length=4000)
    label: str = Field(min_length=1, max_length=200)
    scope: str | None = Field(default=None, max_length=100)
    node_type: str = Field(default="lesson", max_length=50)
    abstraction_type: str | None = Field(default="principle", max_length=20)
    summary: str | None = Field(default=None, max_length=500)
    authority_level: str = Field(default="informational", max_length=30)


async def _persist_recall(db: AsyncSession, req: RecallRequest, ctx, latency_ms: float) -> int | None:
    """Record the recall as a Query row + firings so the Evaluate pages,
    invocation counts, and decay signals see ambient memory traffic.
    response_text stays NULL (nothing was executed); cost is genuinely 0."""
    from app.models import Query
    from app.services.neuron_service import get_system_state, record_firing

    state = await get_system_state(db)
    state.total_queries += 1
    row = Query(
        user_message=req.query[:4000],
        classified_intent=ctx.intent,
        classified_departments=json.dumps(ctx.departments),
        classified_keywords=json.dumps(ctx.keywords),
        selected_neuron_ids=json.dumps([s["neuron_id"] for s in ctx.neuron_scores]),
        neuron_scores_json=json.dumps(ctx.neuron_scores),
        stage_telemetry_json=ctx.stage_telemetry,
        run_opus=False,
        cost_usd=0.0,
        model_version=f"recall:{req.source[:32]}",
        results_json=json.dumps([{"latency_ms": latency_ms, "source": req.source[:32]}]),
    )
    db.add(row)
    await db.flush()
    for idx, score in enumerate(ctx.all_scored):
        await record_firing(
            db, score.neuron_id, row.id, state.global_token_counter,
            global_query_offset=state.total_queries, score=score,
            rank=idx + 1, prompt_position=idx if idx < req.top_k else None,
            was_included=idx < req.top_k,
        )
    await db.commit()
    return row.id


@router.post("/recall", dependencies=[Depends(require_memory_surface)])
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
            # Time-awareness: agentic facts rot — consumers should render
            # "as of <date>" rather than assert timeless truth.
            "as_of": str(neuron.created_at.date()) if neuron and neuron.created_at else None,
        }
        if req.include_content and neuron is not None:
            hit["content"] = neuron.content
        hits.append(hit)
    assert len(hits) <= req.top_k, "hit count must respect top_k"
    latency_ms = round((time.monotonic() - t0) * 1000, 1)
    query_id = None
    if req.persist:
        query_id = await _persist_recall(db, req, ctx, latency_ms)
    return {
        "intent": ctx.intent,
        "scopes": ctx.departments,
        "latency_ms": latency_ms,
        "query_id": query_id,
        "hits": hits,
    }


@router.post("/remember", dependencies=[Depends(require_memory_surface)])
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
