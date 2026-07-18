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
from app.services.skill_signpost import skill_pointers_for
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
    # Situated recall: hits anchored under this project node get a boost —
    # contextual truths should win where their context applies.
    project: str | None = Field(default=None, max_length=100)


class RememberRequest(BaseModel):
    lesson: str = Field(min_length=1, max_length=8000)
    evidence: str = Field(min_length=1, max_length=4000)
    label: str = Field(min_length=1, max_length=200)
    scope: str | None = Field(default=None, max_length=100)
    node_type: str = Field(default="lesson", max_length=50)
    abstraction_type: str | None = Field(default="principle", max_length=20)
    summary: str | None = Field(default=None, max_length=500)
    authority_level: str = Field(default="informational", max_length=30)
    project: str | None = Field(default=None, max_length=100)


async def _parent_projects(db: AsyncSession, ctx) -> dict:
    """neuron_id -> project-node label for hits nested under a project."""
    from sqlalchemy import select
    from app.models import Neuron
    parent_ids = {n.parent_id for n in ctx.neuron_map.values() if n.parent_id}
    if not parent_ids:
        return {}
    rows = (await db.execute(
        select(Neuron.id, Neuron.label).where(
            Neuron.id.in_(parent_ids), Neuron.node_type == "project")
    )).all()
    label_by_parent = {r.id: r.label for r in rows}
    return {nid: label_by_parent.get(n.parent_id)
            for nid, n in ctx.neuron_map.items() if n.parent_id in label_by_parent}


async def _persist_recall(
    db: AsyncSession, req: RecallRequest, ctx, latency_ms: float,
    returned_ids: list[int], skill_pointers: list[dict] | None = None,
) -> int | None:
    """Record the recall as a Query row + firings so the Evaluate pages,
    invocation counts, and decay signals see ambient memory traffic.
    response_text stays NULL (nothing was executed); cost is genuinely 0.

    was_included tracks what was ACTUALLY RETURNED (returned_ids), not what
    ranked in the top_k. Filtered-out scaffolding must not keep earning
    inclusion credit for slots it never occupied — that credit is what fed
    its burst/recency and let it outrank real lessons in the first place."""
    from app.models import Query
    from app.services.neuron_service import get_system_state, record_firing

    state = await get_system_state(db)
    state.total_queries += 1
    row = Query(
        user_message=req.query[:4000],
        classified_intent=ctx.intent,
        classified_departments=json.dumps(ctx.departments),
        classified_keywords=json.dumps(ctx.keywords),
        selected_neuron_ids=json.dumps(returned_ids),
        neuron_scores_json=json.dumps(ctx.neuron_scores),
        stage_telemetry_json=ctx.stage_telemetry,
        run_opus=False,
        cost_usd=0.0,
        model_version=f"recall:{req.source[:32]}",
        results_json=json.dumps([{
            "latency_ms": latency_ms,
            "source": req.source[:32],
            "candidates_considered": ctx.candidates_considered,
            "neurons_activated": ctx.neurons_activated,
            "neurons_delivered": len(returned_ids),
            "estimated_memory_tokens": ctx.estimated_memory_tokens,
            "memory_context_chars": ctx.memory_context_chars,
            "memory_context_utf8_bytes": ctx.memory_context_utf8_bytes,
            "memory_token_budget": ctx.memory_token_budget,
            "assembly_stop_reason": ctx.assembly_stop_reason,
            "redundancy_suppressed": ctx.redundancy_suppressed,
            "token_estimator_version": ctx.token_estimator_version,
            "skill_pointers": [
                {"skill": p["name"], "votes": p["votes"],
                 "path": p.get("path"),
                 **({"node_score": p["node_score"]}
                    if p.get("node_score") is not None else {})}
                for p in (skill_pointers or [])],
        }]),
    )
    db.add(row)
    await db.flush()
    position_of = {nid: i for i, nid in enumerate(returned_ids)}
    for idx, score in enumerate(ctx.all_scored):
        pos = position_of.get(score.neuron_id)
        await record_firing(
            db, score.neuron_id, row.id, state.global_token_counter,
            global_query_offset=state.total_queries, score=score,
            rank=idx + 1, prompt_position=pos,
            was_included=pos is not None,
        )
    await db.commit()
    return row.id


# Navigational scaffolding — org-chart anchors whose entire content is
# "Department: Harness" / "Role: Project Context in Projects". They carry no
# knowledge, but they sit at the top of every parent chain, so they match
# generic queries, fire constantly, and accrue burst/recency that content-
# bearing lessons with no firing history cannot outrank. Measured 2026-07-13
# on corvus-mind: role "Project Context" had 381 firings and project "corvus"
# 366, while the lessons that actually answered the query had 0 — and the
# scaffolding took 4 of the top 5 slots. They stay in the graph (spread
# traverses them as hubs); they just never consume a recall result slot.
# "skill" (mind-skill-node-scoring): compiled-skill graph shadows are
# pointer fuel, not hits — their score feeds the direct signpost path
# below instead of consuming a slot the hook would discard client-side.
# This also removes them from MCP recall hits, intentionally: clients
# should see the pointer, not a scaffold hit.
_SCAFFOLDING_NODE_TYPES = frozenset({"role", "department", "project",
                                     "document", "skill"})
# Over-fetch multiple so dropped scaffolding is backfilled by real knowledge
# rather than shrinking the result set.
_SCAFFOLDING_HEADROOM = 3
_MAX_FETCH_K = 40


@router.post("/recall", dependencies=[Depends(require_memory_surface)])
async def recall(req: RecallRequest, db: AsyncSession = Depends(get_db)):
    """Cheap structured recall: prepare pipeline only, no LLM, no execution."""
    t0 = time.monotonic()
    # prepare_context truncates neuron_scores to top_k, so scaffolding must be
    # over-fetched THEN filtered — filtering a top_k-truncated list just returns
    # fewer hits with nothing to backfill from.
    fetch_k = min(_MAX_FETCH_K, req.top_k * _SCAFFOLDING_HEADROOM)
    ctx = await prepare_context(db, req.query, top_k=fetch_k)
    assert ctx is not None, "prepare_context must return a PreparedContext"
    project_of = await _parent_projects(db, ctx) if req.project else {}
    hits = []
    for s in ctx.neuron_scores:
        neuron = ctx.neuron_map.get(s["neuron_id"])
        if neuron is not None and neuron.node_type in _SCAFFOLDING_NODE_TYPES:
            continue
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
        # INJECTION BADGING (mind-reference-class): downstream consumers
        # must be able to tell textbook from scar tissue.
        if neuron is not None and neuron.source_origin == "document":
            hit["reference"] = True
        hit["project"] = project_of.get(s["neuron_id"])
        if req.project and hit["project"] == req.project:
            hit["score"] = round(hit["score"] * 1.15, 4)  # situated boost
        hits.append(hit)
    if req.project:
        hits.sort(key=lambda h: -h["score"])
    hits = hits[:req.top_k]
    assert len(hits) <= req.top_k, "hit count must respect top_k"
    ref_ids = [h["neuron_id"] for h in hits if h.get("reference")]
    if ref_ids:  # one batched query, only when reference hits surfaced —
        # the LLM-free hot path is unchanged for pure-lesson recalls
        from app.services.reference_ingest import reference_sources_for
        sources = await reference_sources_for(db, ref_ids)
        for h in hits:
            if h.get("reference"):
                h["source"] = sources.get(h["neuron_id"])
    # Skill signposting (mind-skill-signpost): member-lesson vote over the
    # over-fetched candidate set — BEFORE the scaffolding filter and top_k
    # cut, so a relevant cluster still signals when its lessons lose the
    # final slots. Skill nodes themselves stay filtered from hits; their
    # own scores feed the direct path (mind-skill-node-scoring) — this is
    # the only place their relevance becomes visible at query time.
    skill_pointers = skill_pointers_for(
        [(s["neuron_id"], s["combined"]) for s in ctx.neuron_scores],
        [(s["label"], s["combined"]) for s in ctx.neuron_scores
         if (n := ctx.neuron_map.get(s["neuron_id"])) is not None
         and n.node_type == "skill"])
    latency_ms = round((time.monotonic() - t0) * 1000, 1)
    query_id = None
    if req.persist:
        query_id = await _persist_recall(
            db, req, ctx, latency_ms, [h["neuron_id"] for h in hits],
            skill_pointers)
    return {
        "intent": ctx.intent,
        "scopes": ctx.departments,
        "latency_ms": latency_ms,
        "query_id": query_id,
        "hits": hits,
        "skill_pointers": skill_pointers,
        "telemetry": {
            "candidates_considered": ctx.candidates_considered,
            "neurons_activated": ctx.neurons_activated,
            "neurons_delivered": len(hits),
            "pipeline_neurons_delivered": ctx.neurons_delivered,
            "estimated_memory_tokens": ctx.estimated_memory_tokens,
            "memory_context_chars": ctx.memory_context_chars,
            "memory_context_utf8_bytes": ctx.memory_context_utf8_bytes,
            "memory_token_budget": ctx.memory_token_budget,
            "assembly_stop_reason": ctx.assembly_stop_reason,
            "redundancy_suppressed": ctx.redundancy_suppressed,
            "token_estimator_version": ctx.token_estimator_version,
            "recall_latency_ms": latency_ms,
        },
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
        authority_level=req.authority_level, project=req.project,
        source_origin="remember_api", gap_source="remember_api",
    )
