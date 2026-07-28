"""POST /query — Main pipeline. POST /query/{id}/rate — User feedback."""

import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Query, NeuronFiring, Neuron, EvalScore, NeuronRefinement, SynapticLearningEvent, OutputViolation
from app.schemas import (
    QueryRequest, QueryResponse, QuerySummary, QueryDetail, NeuronHit,
    EvalRequest, EvalResponse, EvalScoreOut, EvalScoreSummary,
    RatingRequest, RatingResponse,
    RefineRequest, RefineResponse, NeuronUpdateSuggestion, NewNeuronSuggestion,
    ApplyRefineRequest, ApplyRefineResponse, RefinementOut,
    LearningEventOut, LearningAnalytics,
    OutputViolationOut,
    QueryDossier,
    FollowUpSuggestion, FollowUpSuggestionsResponse,
    SlotResult,  # For backward-compat: parsing legacy multi-slot query data
)
from app.governance.output_guard import GuardResult, run_guards
from app.services.executor import execute_query, prepare_context
from app.services.pipeline import PipelineStageError
from app.services.llm_provider import llm_chat, estimate_cost, get_available_models, MODEL_REGISTRY, effort_var
from app.services import action_bus
from app.middleware.rbac import UserIdentity, resolve_identity


def get_valid_model_names() -> set[str]:
    return set(MODEL_REGISTRY.keys())
from app.services.neuron_service import get_system_state, score_candidates
from app.services.scoring_engine import update_impact_ema
from app.services.input_guard import check_input, check_output_risk, check_output_grounding
from pydantic import BaseModel, Field
from sqlalchemy import select, func

router = APIRouter(tags=["query"])

def _session_spec_from_request(req: QueryRequest) -> dict | None:
    """Resolve the persisted-CLI-session spec for a query request.

    Opt-in (persist_session) and gated by settings.chat_session_persistence.
    A returned llm_session_id from a prior turn resumes that session; without
    one, a fresh UUID is minted and --session-id creates it.
    """
    if not settings.chat_session_persistence:
        return None
    if not (req.persist_session or req.llm_session_id):
        return None
    import uuid
    if req.llm_session_id:
        return {"session_id": req.llm_session_id, "resume": True,
                "refresh": req.refresh_context}
    return {"session_id": str(uuid.uuid4()), "resume": False}




# ── Lightweight context endpoint for Corvus integration ──

class ContextRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=5000)
    project_path: str | None = None
    token_budget: int = Field(4000, ge=500, le=16000)
    top_k: int = Field(8, ge=1, le=50)


class ContextResponse(BaseModel):
    system_prompt: str
    intent: str
    departments: list[str]
    role_keys: list[str]
    keywords: list[str]
    neurons_activated: int
    candidates_considered: int = 0
    neurons_delivered: int = 0
    estimated_memory_tokens: int = 0
    memory_context_chars: int = 0
    memory_context_utf8_bytes: int = 0
    memory_token_budget: int = 0
    assembly_stop_reason: str | None = None
    redundancy_suppressed: int = 0
    token_estimator_version: str | None = None
    recall_latency_ms: float = 0.0
    neuron_scores: list[dict] = []
    classify_cost_usd: float = 0


@router.post("/context", response_model=ContextResponse)
async def get_context(req: ContextRequest, db: AsyncSession = Depends(get_db)):
    """Lightweight context assembly endpoint — runs prepare_context() without LLM execution.

    Designed for external consumers (e.g., Corvus) that need Corvus's domain context
    injected into their own LLM calls. Returns the assembled system prompt and metadata.
    """
    ctx = await prepare_context(
        db,
        req.message,
        token_budget=req.token_budget,
        top_k=req.top_k,
        project_path=req.project_path,
    )
    return ContextResponse(
        system_prompt=ctx.system_prompt,
        intent=ctx.intent,
        departments=ctx.departments,
        role_keys=ctx.role_keys,
        keywords=ctx.keywords,
        neurons_activated=ctx.neurons_activated,
        candidates_considered=ctx.candidates_considered,
        neurons_delivered=ctx.neurons_delivered,
        estimated_memory_tokens=ctx.estimated_memory_tokens,
        memory_context_chars=ctx.memory_context_chars,
        memory_context_utf8_bytes=ctx.memory_context_utf8_bytes,
        memory_token_budget=ctx.memory_token_budget,
        assembly_stop_reason=ctx.assembly_stop_reason,
        redundancy_suppressed=ctx.redundancy_suppressed,
        token_estimator_version=ctx.token_estimator_version,
        recall_latency_ms=ctx.recall_latency_ms,
        neuron_scores=ctx.neuron_scores,
        classify_cost_usd=ctx.classify_cost_usd,
    )


@router.get("/models")
async def list_available_models():
    """Return list of LLM models whose provider API key is configured."""
    return get_available_models()


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    model: str = Field("codex-luna")
    history: list[dict] = Field(default_factory=list)
    effort: str | None = None  # reasoning effort: low|medium|high


class ChatResponse(BaseModel):
    response: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0


@router.post("/chat", response_model=ChatResponse)
async def simple_chat(req: ChatRequest):
    """Direct LLM chat without neuron pipeline. For casual conversation."""
    system_prompt = (
        "You are Corvus, an AI assistant embedded in the Corvus knowledge management system. "
        "You help users understand and work with their organizational knowledge graph.\n\n"
        "Response style:\n"
        "- Keep answers SHORT — 2-4 sentences for simple questions, 1-2 short paragraphs max.\n"
        "- Write like you're talking to a colleague: warm, direct, no filler.\n"
        "- Use **bold** and *italic* for emphasis. Never use markdown tables.\n"
        "- Do NOT start with 'Great question' or similar. Lead with the answer.\n"
        "- End with a brief suggestion of related topics the user might want to explore."
    )
    # Build conversation context from history
    history_text = ""
    if req.history:
        lines = []
        for msg in req.history[-10:]:  # last 10 messages
            role = msg.get("role", "user")
            text = msg.get("text", "")
            lines.append(f"{'User' if role == 'user' else 'Assistant'}: {text}")
        history_text = "\n".join(lines) + "\n\nUser: "

    user_message = history_text + req.message if history_text else req.message
    valid_names = get_valid_model_names()
    if req.model not in valid_names:
        raise HTTPException(status_code=400, detail=f"Invalid or unavailable model: {req.model}")
    effort_var.set(req.effort)
    result = await llm_chat(system_prompt, user_message, max_tokens=2048, model=req.model)
    return ChatResponse(
        response=result["text"],
        model=result["served_by"],
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        cost_usd=result["cost_usd"],
    )


def _parse_slots(query: Query) -> list[SlotResult]:
    """Parse slots from results_json, falling back to legacy columns."""
    if query.results_json:
        try:
            return [SlotResult(**s) for s in json.loads(query.results_json)]
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
    # Legacy fallback
    slots = []
    if query.response_text:
        slots.append(SlotResult(
            mode="haiku_neuron", model="haiku", neurons=True,
            response=query.response_text,
            input_tokens=query.execute_input_tokens,
            output_tokens=query.execute_output_tokens,
            cost_usd=0,
        ))
    if query.opus_response_text:
        slots.append(SlotResult(
            mode="opus_raw", model="opus", neurons=False,
            response=query.opus_response_text,
            input_tokens=query.opus_input_tokens,
            output_tokens=query.opus_output_tokens,
            cost_usd=0,
        ))
    return slots


def _parse_modes(query: Query) -> list[str]:
    if query.results_json:
        try:
            return [s["mode"] for s in json.loads(query.results_json)]
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
    modes = []
    if query.run_neuron:
        modes.append("haiku_neuron")
    if query.run_opus:
        modes.append("opus_raw")
    return modes


@router.post("/queries/run-counts")
async def query_run_counts(texts: list[str], db: AsyncSession = Depends(get_db)):
    """Return {text: count} for each provided query text."""
    if not texts:
        return {}
    result = await db.execute(
        select(Query.user_message, func.count())
        .where(Query.user_message.in_(texts))
        .group_by(Query.user_message)
    )
    counts = {row[0]: row[1] for row in result.all()}
    return {t: counts.get(t, 0) for t in texts}


@router.get("/queries", response_model=list[QuerySummary])
async def list_queries(db: AsyncSession = Depends(get_db)):
    """List the 50 most recent queries with summary metadata."""
    result = await db.execute(select(Query).order_by(Query.id.desc()).limit(50))
    queries = result.scalars().all()
    return [
        QuerySummary(
            id=q.id,
            user_message=q.user_message,
            classified_intent=q.classified_intent,
            modes=_parse_modes(q),
            cost_usd=q.cost_usd,
            user_rating=q.user_rating,
            created_at=q.created_at.isoformat() if q.created_at else None,
        )
        for q in queries
    ]


async def _load_neurons_by_ids(db: AsyncSession, neuron_ids: list[int]) -> list:
    neurons = []
    for nid in neuron_ids:
        neuron = await db.get(Neuron, nid)
        if neuron:
            neurons.append(neuron)
    assert isinstance(neurons, list), "neurons must be a list"
    assert len(neurons) <= len(neuron_ids), "cannot load more neurons than IDs given"
    return neurons


async def _build_score_map(
    db: AsyncSession, query: Query, neurons: list,
) -> dict[int, dict]:
    if query.neuron_scores_json:
        score_map = {}
        for s in json.loads(query.neuron_scores_json):
            score_map[s["neuron_id"]] = s
        assert isinstance(score_map, dict), "score_map must be a dict"
        return score_map
    if neurons:
        state = await get_system_state(db)
        keywords_list = json.loads(query.classified_keywords) if query.classified_keywords else []
        scored = await score_candidates(db, neurons, state.total_queries, keywords_list)
        assert isinstance(scored, list), "scored candidates must be a list"
        return {s.neuron_id: {"combined": s.combined, "burst": s.burst,
                "impact": s.impact, "precision": s.precision, "novelty": s.novelty,
                "recency": s.recency, "relevance": s.relevance} for s in scored}
    return {}


def _build_neuron_hits(neurons: list, score_map: dict[int, dict]) -> list[NeuronHit]:
    hits = []
    for neuron in neurons:
        s = score_map.get(neuron.id, {})
        hits.append(NeuronHit(
            neuron_id=neuron.id, label=neuron.label, layer=neuron.layer,
            department=neuron.department,
            parent_id=neuron.parent_id, summary=neuron.summary,
            combined=s.get("combined", 0), burst=s.get("burst", 0),
            impact=s.get("impact", 0), precision=s.get("precision", 0),
            novelty=s.get("novelty", 0), recency=s.get("recency", 0),
            relevance=s.get("relevance", 0), spread_boost=s.get("spread_boost", 0),
        ))
    assert len(hits) == len(neurons), "must produce one hit per neuron"
    return hits


async def _load_eval_scores(
    db: AsyncSession, query_id: int, eval_text: str | None,
) -> tuple[list[EvalScoreOut], str | None]:
    result = await db.execute(
        select(EvalScore).where(EvalScore.query_id == query_id).order_by(EvalScore.answer_label)
    )
    eval_scores = [
        EvalScoreOut(
            answer_label=es.answer_label,
            answer_mode=es.answer_mode,
            accuracy=es.accuracy,
            completeness=es.completeness,
            clarity=es.clarity,
            faithfulness=es.faithfulness,
            overall=es.overall,
        )
        for es in result.scalars()
    ]
    eval_winner = None
    if eval_scores and eval_text:
        best = max(eval_scores, key=lambda s: s.overall)
        if eval_scores.count(best) == 1:
            eval_winner = best.answer_label
    assert isinstance(eval_scores, list), "eval_scores must be a list"
    return eval_scores, eval_winner


async def _load_refinements(
    db: AsyncSession, query_id: int,
) -> list[RefinementOut]:
    result = await db.execute(
        select(NeuronRefinement).where(NeuronRefinement.query_id == query_id).order_by(NeuronRefinement.id)
    )
    refinements = []
    for r in result.scalars().all():
        neuron = await db.get(Neuron, r.neuron_id)
        refinements.append(RefinementOut(
            id=r.id,
            neuron_id=r.neuron_id,
            action=r.action,
            field=r.field,
            old_value=r.old_value,
            new_value=r.new_value,
            reason=r.reason,
            neuron_label=neuron.label if neuron else None,
        ))
    assert isinstance(refinements, list), "refinements must be a list"
    return refinements


def _parse_pending_refine(refine_json: str | None, fallback_query_id: int) -> dict | None:
    """Parse the stored refine artifact into a RefineResponse-shaped dict.

    Two writers populate `query.refine_json`:
    - Manual /refine endpoint — writes the full RefineResponse shape.
    - Autopilot _refine — writes only {reasoning, updates, new_neurons} and
      omits query_id/model/input_tokens/output_tokens.

    Normalize by backfilling defaults so Pydantic validation succeeds for
    both shapes. Required — otherwise /queries/{id} 500s on any query that
    was last refined by the autopilot.
    """
    assert isinstance(fallback_query_id, int), "fallback_query_id must be int"
    if not refine_json:
        return None
    try:
        parsed = json.loads(refine_json)
    except json.JSONDecodeError:
        return None
    if parsed is None or not isinstance(parsed, dict):
        return None
    parsed.setdefault("query_id", fallback_query_id)
    parsed.setdefault("model", "")
    parsed.setdefault("input_tokens", 0)
    parsed.setdefault("output_tokens", 0)
    parsed.setdefault("reasoning", "")
    return parsed


@router.get("/queries/{query_id}", response_model=QueryDetail)
async def get_query_detail(query_id: int, db: AsyncSession = Depends(get_db)):
    """Retrieve full detail for a single query including neuron hits, slots, eval scores, and refinements."""
    query = await db.get(Query, query_id)
    if not query:
        raise HTTPException(status_code=404, detail="Query not found")

    neuron_ids = json.loads(query.selected_neuron_ids) if query.selected_neuron_ids else []
    neurons = await _load_neurons_by_ids(db, neuron_ids)
    score_map = await _build_score_map(db, query, neurons)
    hits = _build_neuron_hits(neurons, score_map)
    slots = _parse_slots(query)
    eval_scores, eval_winner = await _load_eval_scores(db, query_id, query.eval_text)
    refinements = await _load_refinements(db, query_id)
    pending_refine = _parse_pending_refine(query.refine_json, query.id)

    assert query.id is not None, "query must have a valid ID"
    return QueryDetail(
        id=query.id,
        user_message=query.user_message,
        classified_intent=query.classified_intent,
        departments=json.loads(query.classified_departments) if query.classified_departments else [],
        role_keys=json.loads(query.classified_role_keys) if query.classified_role_keys else [],
        keywords=json.loads(query.classified_keywords) if query.classified_keywords else [],
        assembled_prompt=query.assembled_prompt,
        classify_input_tokens=query.classify_input_tokens,
        classify_output_tokens=query.classify_output_tokens,
        classify_cost=estimate_cost("haiku", query.classify_input_tokens, query.classify_output_tokens),
        slots=slots,
        total_cost=query.cost_usd or 0,
        user_rating=query.user_rating,
        eval_text=query.eval_text,
        eval_model=query.eval_model,
        eval_input_tokens=query.eval_input_tokens,
        eval_output_tokens=query.eval_output_tokens,
        eval_scores=eval_scores,
        eval_winner=eval_winner,
        neuron_hits=hits,
        refinements=refinements,
        pending_refine=pending_refine,
        created_at=query.created_at.isoformat() if query.created_at else None,
    )


@router.get("/queries/{query_id}/dossier", response_model=QueryDossier)
async def get_query_dossier(query_id: int, db: AsyncSession = Depends(get_db)):
    """AIP Phase 3 — return the full Dossier for a single query.

    On-read aggregation of the five per-query governance signals:
    pipeline telemetry, eval (ad-hoc + eval-run participations), output
    violations, action audit trail, and integrity findings (attributed
    by selected-neuron-id overlap). See
    `docs/design/aip-phase-3-query-dossier.md` for scope + caveats.
    """
    assert query_id > 0, "query_id must be positive"
    from app.services.query_dossier import build_dossier
    dossier = await build_dossier(db, query_id)
    if dossier is None:
        raise HTTPException(status_code=404, detail="Query not found")
    return dossier


async def _load_included_firings(
    db: AsyncSession, query_id: int,
) -> list[NeuronFiring]:
    """Fetch NeuronFiring rows for a query so output policies can inspect them."""
    result = await db.execute(
        select(NeuronFiring).where(NeuronFiring.query_id == query_id)
    )
    return list(result.scalars())


def _violation_to_out(row: OutputViolation) -> OutputViolationOut:
    """Serialize a persisted OutputViolation row for the API response."""
    return OutputViolationOut(
        id=row.id,
        rule_id=row.rule_id,
        severity=row.severity,
        action=row.action,
        matched_span=row.matched_span,
        redaction=row.redaction,
        detail=row.detail,
    )


async def _apply_output_guards(
    db: AsyncSession,
    query_id: int,
    slots: list[dict],
    actor: UserIdentity,
) -> tuple[list[OutputViolationOut], bool]:
    """Run Pattern #7 output policies against every slot's response.

    Returns ``(violations_out, blocked)``. Redactions are applied in place
    to each slot's ``response`` field AND mirrored onto the persisted
    ``queries.response_text`` / ``queries.opus_response_text`` columns
    so the governed text — not the raw LLM output — is what remains on
    the audit record. Caller owns the commit.
    """
    assert len(slots) <= 16, "slot count exceeds sanity cap (JPL-2)"
    firings = await _load_included_firings(db, query_id)
    out: list[OutputViolationOut] = []
    blocked = False
    query_row: Query | None = None
    for slot in slots:
        text = slot.get("response") or ""
        if not text:
            continue
        guard: GuardResult = await run_guards(
            db,
            query_id=query_id,
            response_text=text,
            firings=firings,
            actor=actor,
        )
        if guard.final_text != text:
            slot["response"] = guard.final_text
            # Mirror the redaction back onto the persisted Query row so
            # queries.response_text reflects the governed text, not the
            # raw LLM output. Load lazily to avoid a query when nothing
            # is being redacted.
            if query_row is None:
                query_row = await db.get(Query, query_id)
            if query_row is not None:
                mode = slot.get("mode")
                if mode == "haiku_neuron" and query_row.response_text == text:
                    query_row.response_text = guard.final_text
                elif mode == "opus_raw" and query_row.opus_response_text == text:
                    query_row.opus_response_text = guard.final_text
        out.extend(_violation_to_out(v) for v in guard.violations)
        if guard.blocked:
            blocked = True
    return out, blocked


async def _run_output_gate(
    db: AsyncSession,
    result: dict,
    identity: UserIdentity,
) -> None:
    """Run Pattern #7 output policies; mutate ``result`` or raise 422.

    Attaches ``result["output_violations"]`` for non-blocked runs; raises
    ``HTTPException(422)`` if any policy had ``action="block"``.
    """
    query_id = result.get("query_id")
    if query_id is None:
        return
    violations, blocked = await _apply_output_guards(
        db, query_id, result.get("slots", []), identity,
    )
    await db.commit()  # persist violations + audit action regardless of block
    result["output_violations"] = [v.model_dump() for v in violations]
    if blocked:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Response blocked by output policy",
                "blocking_violations": [
                    v.model_dump() for v in violations if v.action == "block"
                ],
            },
        )


async def _maybe_attach_entailment(db: AsyncSession, result: dict, output_checks: list[dict]) -> None:
    """Attach the opt-in claim-entailment pass to the primary output check.

    Advisory and config-gated (settings.entailment_check_enabled): one extra
    batched LLM call judging each cited claim of the PRIMARY answer against
    its cited source content. Never blocks or mutates the answer.
    """
    assert isinstance(output_checks, list), "output_checks must be a list"
    if not settings.entailment_check_enabled or not output_checks:
        return
    from app.services.entailment_check import run_entailment_check
    output_checks[0]["entailment"] = await run_entailment_check(
        db, result.get("response_text", ""), result.get("query_id"),
    )


def _legacy_output_checks(result: dict) -> list[dict]:
    """Risk-flag + grounding check on combined response text (legacy path)."""
    response_text = result.get("response_text", "")
    if not response_text:
        return []
    risk_flags = check_output_risk(response_text)
    if result.get("neuron_scores"):
        grounding = check_output_grounding(
            response_text,
            result.get("assembled_prompt"),
        )
    else:
        grounding = {"grounded": None, "confidence": None, "reason": "No neuron context"}
    return [{
        "mode": "direct",
        "risk_flags": risk_flags,
        "grounding": grounding,
    }]


def _slot_dicts_from_request(req: QueryRequest) -> list[dict] | None:
    """Executor slot dicts for a request, applying the audit-grade action.

    audit_grade marks slot 0 as the explicit opus@low audit action
    (arch-tier-routing), creating the default single slot when the request
    sent none — so the hero chat can request an audit-grade answer without
    knowing the slot vocabulary.
    """
    assert req is not None, "req must be a QueryRequest"
    slot_dicts = [s.model_dump() for s in req.slots] if req.slots else None
    if not req.audit_grade:
        return slot_dicts
    if slot_dicts is None:
        slot_dicts = [{
            "mode": "opus_neuron",
            "token_budget": settings.token_budget,
            "top_k": None,
            "priming": True,
        }]
    slot_dicts[0]["audit"] = True
    return slot_dicts


@router.post("/query", response_model=QueryResponse)
async def post_query(
    req: QueryRequest,
    db: AsyncSession = Depends(get_db),
    identity: UserIdentity = Depends(resolve_identity),
):
    """Execute a query through the neuron pipeline.

    classify → score → assemble → execute (per-slot, parallel) → output gate.
    """
    # JPL Rule 5: message must be non-empty (defense-in-depth beyond Pydantic)
    assert req.message and req.message.strip(), "Query message must be non-empty"

    # ── Input Guard: run before classification ──
    guard_result = check_input(req.message)
    if guard_result.verdict == "block":
        reason = guard_result.flags[0].get("description") if guard_result.flags else "safety filter"
        raise HTTPException(
            status_code=403,
            detail={
                "message": f"Input blocked: {reason}",
                "flags": guard_result.flags,
            },
        )

    effort_var.set(req.effort)
    try:
        slot_dicts = _slot_dicts_from_request(req)
        result = await execute_query(
            db, req.message,
            slots=slot_dicts,
            prior_neuron_ids=req.prior_neuron_ids,
            session_spec=_session_spec_from_request(req),
        )
    except HTTPException:
        raise
    except PipelineStageError as pse:
        # Pattern #5 hard-fail: surface which stage broke so the UI can highlight it.
        raise HTTPException(
            status_code=500,
            detail={
                "message": f"Pipeline stage '{pse.stage_name}' failed",
                "failed_stage": pse.stage_name,
                "cause": str(pse.original),
            },
        )
    except RuntimeError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Query execution failed: {e}")

    await _run_output_gate(db, result, identity)

    result["input_guard"] = guard_result.to_dict()
    result["output_checks"] = _legacy_output_checks(result)
    await _maybe_attach_entailment(db, result, result["output_checks"])

    return QueryResponse(**result)


async def _stream_output_checks(db, result: dict, on_stage) -> list[dict]:
    """Run output risk/grounding checks + the opt-in entailment pass for the
    SSE path, emitting each as its own stage event for the pipeline viz."""
    output_checks: list[dict] = []
    response_text = result.get("response_text", "")
    if response_text:
        risk_flags = check_output_risk(response_text)
        grounding = check_output_grounding(
            response_text, result.get("assembled_prompt"),
        ) if result.get("neuron_scores") else {"grounded": None, "confidence": None, "reason": "No neuron context"}
        output_checks.append({
            "mode": "direct",
            "risk_flags": risk_flags,
            "grounding": grounding,
        })

    await on_stage("output_checks", {"status": "done", "detail": {"checked": len(output_checks)}})

    # Opt-in entailment pass on the primary answer (one batched LLM
    # call, advisory) — emitted as its own stage so the viz shows it.
    if settings.entailment_check_enabled and output_checks:
        await on_stage("entailment_check", {"status": "running", "detail": {}})
        await _maybe_attach_entailment(db, result, output_checks)
        ent = output_checks[0].get("entailment") or {}
        await on_stage("entailment_check", {"status": "done", "detail": {
            "checked": ent.get("checked", 0),
            "unsupported": ent.get("unsupported_count", 0),
            "state": ent.get("status", "unknown"),
        }})
    return output_checks


@router.post("/query/stream")
async def post_query_stream(req: QueryRequest, db: AsyncSession = Depends(get_db)):
    """SSE streaming version of POST /query — emits pipeline stage events in real time."""

    effort_var.set(req.effort)  # inherited by the run_pipeline task + its slot tasks

    queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=1000)
    # JPL Rule 5: queue must be bounded to prevent unbounded memory growth
    assert queue.maxsize > 0, "SSE queue must have a bounded maxsize"

    async def on_stage(stage: str, data: dict):
        await queue.put({"event": "stage", "data": {"stage": stage, **data}})

    async def run_pipeline():
        try:
            # Input guard
            guard_result = check_input(req.message)
            await on_stage("input_guard", {
                "status": "done",
                "detail": {"verdict": guard_result.verdict, "flag_count": len(guard_result.flags)},
            })
            if guard_result.verdict == "block":
                # Surface the first specific flag description so the user
                # can tell WHY the filter fired (length, pattern match, …).
                reason = guard_result.flags[0].get("description") if guard_result.flags else "safety filter"
                await queue.put({"event": "error", "data": {
                    "message": f"Input blocked: {reason}",
                    "flags": guard_result.flags,
                }})
                return

            # Execute pipeline with stage callbacks (Session 3+ multi-slot path)
            slot_dicts = _slot_dicts_from_request(req)

            result = await execute_query(
                db, req.message,
                slots=slot_dicts,
                on_stage=on_stage,
                prior_neuron_ids=req.prior_neuron_ids,
                session_spec=_session_spec_from_request(req),
            )

            output_checks = await _stream_output_checks(db, result, on_stage)

            result["input_guard"] = guard_result.to_dict()
            result["output_checks"] = output_checks

            # Final result
            resp = QueryResponse(**result)
            await queue.put({"event": "result", "data": resp.model_dump()})
        except PipelineStageError as pse:
            await queue.put({"event": "error", "data": {
                "message": f"Pipeline stage '{pse.stage_name}' failed",
                "failed_stage": pse.stage_name,
                "cause": str(pse.original),
            }})
        except Exception as e:
            await queue.put({"event": "error", "data": {"message": str(e)}})
        finally:
            await queue.put(None)

    async def event_generator():
        task = asyncio.create_task(run_pipeline())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event_type = item["event"]
                data_json = json.dumps(item["data"])
                yield f"event: {event_type}\ndata: {data_json}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _strip_citation_keys(text: str) -> str:
    """Remove [FQ-] citation keys from text bound for an eval/refine judge.

    Keys are per-query random secrets; a judge working from a DIFFERENT
    context prep sees different keys and rules valid citations "fabricated"
    (root cause of the systematic faithfulness ~2/5 on citing answers,
    diagnosed 2026-07-08). Key validity is layer 1's job, verified at answer
    time against the correct map — judges evaluate CONTENT, so keys are
    noise to them regardless.
    """
    if not text:
        return text
    from app.services.citation_hopping import extract_citation_tokens, strip_hallucinated
    tokens = extract_citation_tokens(text)
    return strip_hallucinated(text, tokens) if tokens else text


def _build_eval_prompts(
    user_message: str, slots: list[SlotResult], domain_knowledge: str = "",
) -> tuple[str, str, list[tuple[str, SlotResult]]]:
    """Judge prompts for a multi-answer comparison. BLIND BY CONSTRUCTION:
    answers are presented as bare letters — never slot labels or model names,
    which leak producer identity and let brand/self-preference bias the judge
    (found live 2026-07-09: "Answer A (Haiku + Neurons @ 8K)"). The returned
    answer_map is how CALLERS resolve letters back to slots; the judge never
    sees it.
    """
    assert slots, "slots must be non-empty"
    answer_map: list[tuple[str, SlotResult]] = []
    sections = [f"User's question:\n{user_message}"]
    for i, slot in enumerate(slots):
        letter = chr(65 + i)
        answer_map.append((letter, slot))
        sections.append(
            f"Answer {letter}:\n{_strip_citation_keys(slot.response)}")

    score_template = []
    for letter, _slot in answer_map:
        score_template.append(
            f'  {{"answer": "{letter}", '
            f'"accuracy": <1-5>, "completeness": <1-5>, "clarity": <1-5>, '
            f'"faithfulness": <1-5>, "overall": <1-5>}}'
        )

    eval_prompt = "\n\n---\n\n".join(sections)
    assert len(sections) >= 3, "eval prompt must include question + at least 2 answers"

    eval_system = _eval_system_prompt(domain_knowledge, score_template)
    assert eval_system and eval_prompt, "eval prompts must be non-empty"

    return eval_system, eval_prompt, answer_map


# Anchored judge rubric. The old scale ("1=poor, 5=excellent" with one-line
# dimension names) left every mid score undefined — a 3/5 could mean a real
# defect, a shrug, or two passes averaging (2,4). Anchors pin what each score
# means so dimension scores are stable across runs and diagnosable.
# Honest gap acknowledgment must NOT be scored as unfaithfulness: the answer
# prompt (prompt_assembler GROUNDING_CLAUSE) instructs models to flag points
# the knowledge pack doesn't cover instead of inferring, so the judge must
# reward that under Faithfulness/Accuracy and account for the actual missing
# coverage under Completeness only — otherwise the two prompts fight.
_RUBRIC_SHARED = (
    "- Completeness — covers the full question:\n"
    "  5=every part and sub-question addressed; 4=one minor aspect missing; "
    "3=a notable part of the question left unaddressed; 2=answers only part of "
    "the question; 1=misses the point of the question\n"
    "- Clarity — structure and readability:\n"
    "  5=well-organized and skimmable with no filler; 4=minor structural "
    "issues; 3=understandable but disorganized or padded; 2=hard to follow; "
    "1=confusing or incoherent\n"
    "- Overall — holistic quality: weigh the dimensions above; an answer with "
    "a misleading factual error or a fabricated authority scores at most 2 "
    "here regardless of style\n"
)

_RUBRIC_GROUNDED = (
    "Score each answer on these dimensions. Scores are integers 1 to 5 — use these anchors:\n"
    "- Accuracy — factual correctness against the domain knowledge above:\n"
    "  5=every checkable claim matches the domain knowledge; 4=one minor "
    "imprecision; 3=a few wrong specifics (numbers, names, clause references); "
    "2=a substantive error that would mislead the reader; 1=largely incorrect\n"
    "- Faithfulness — grounding in the domain knowledge above:\n"
    "  5=every claim traceable to the domain knowledge; 4=one minor "
    "untraceable embellishment; 3=a few unsupported specifics; 2=substantial "
    "content not in the domain knowledge; 1=mostly ungrounded or fabricated\n"
    + _RUBRIC_SHARED +
    "An answer that explicitly states the provided knowledge does not cover a "
    "point is MORE faithful than one that fills the gap from memory: never "
    "penalize honest gap acknowledgment under Faithfulness or Accuracy — "
    "reflect actual missing coverage under Completeness only.\n"
)

_RUBRIC_BLIND = (
    "Score each answer on these dimensions. Scores are integers 1 to 5 — use these anchors:\n"
    "- Accuracy — factual correctness:\n"
    "  5=every checkable claim is correct; 4=one minor imprecision; 3=a few "
    "wrong specifics (numbers, names, clause references); 2=a substantive "
    "error that would mislead the reader; 1=largely incorrect\n"
    "- Faithfulness — no fabrication or overclaiming:\n"
    "  5=no invented sources, standards, or specifics, and uncertainty is "
    "acknowledged; 4=one minor overclaimed detail; 3=a few specifics stated "
    "with unwarranted certainty; 2=fabricated or unverifiable authority "
    "(citations, clause numbers) presented as fact; 1=largely fabricated\n"
    + _RUBRIC_SHARED +
    "An answer that explicitly acknowledges uncertainty or missing "
    "information is MORE faithful than one that fills the gap with confident "
    "invention — never penalize honest gap acknowledgment under Faithfulness "
    "or Accuracy.\n"
)


def _eval_system_prompt(domain_knowledge: str, score_template: list[str]) -> str:
    """Judge system prompt (optionally grounded in domain facts). The answer
    identities must stay out of here — see _build_eval_prompts."""
    assert isinstance(score_template, list) and score_template, \
        "score_template must be non-empty"
    output_contract = (
        "You MUST respond with EXACTLY this format — a JSON block followed by your verdict:\n\n"
        "```json\n"
        '{"scores": [\n'
        + ",\n".join(score_template) + "\n"
        "],\n"
        '"winner": "<letter or tie>",\n'
        '"verdict": "<2-4 sentence comparison explaining your reasoning; for any '
        'score of 3 or below, name the specific claim or omission that caused it>"\n'
        "}\n"
        "```\n\n"
        "No other text outside the JSON block. Use the answer labels (A, B, etc.) in your verdict."
    )
    if domain_knowledge:
        return (
            "You are a domain expert evaluating AI responses against authoritative knowledge.\n\n"
            "## Domain Knowledge (Ground Truth)\n"
            "Use the following domain facts to assess accuracy and faithfulness:\n\n"
            + domain_knowledge + "\n\n"
            "## Evaluation Instructions\n"
            + _RUBRIC_GROUNDED + "\n"
            + output_contract
        )
    return (
        "You are a blind evaluator comparing AI responses. You have NO prior context — "
        "only the user's question and the answers provided.\n\n"
        + _RUBRIC_BLIND + "\n"
        + output_contract
    )


def _parse_eval_response(raw_text: str) -> tuple[list[dict], str, str | None]:
    parsed_scores: list[dict] = []
    verdict_text = raw_text
    winner = None
    try:
        json_str = raw_text
        if "```" in json_str:
            json_str = json_str.split("```")[1]
            if json_str.startswith("json"):
                json_str = json_str[4:]
            json_str = json_str.strip()
        parsed = json.loads(json_str)
        parsed_scores = parsed.get("scores", [])
        verdict_text = parsed.get("verdict", raw_text)
        winner = parsed.get("winner")
    except (json.JSONDecodeError, KeyError, TypeError, IndexError):
        pass
    assert isinstance(parsed_scores, list), "parsed_scores must be a list"
    return parsed_scores, verdict_text, winner


_EVAL_DIMS = ("accuracy", "completeness", "clarity", "faithfulness", "overall")


async def _judge_pass(
    user_message: str, ordered: list[SlotResult], domain_knowledge: str, model: str,
) -> dict:
    """One judging pass over the answers in the given presentation order."""
    assert len(ordered) >= 2, "judge pass needs at least 2 answers"
    eval_system, eval_prompt, _ = _build_eval_prompts(user_message, ordered, domain_knowledge)
    result = await llm_chat(eval_system, eval_prompt, max_tokens=2048, model=model)
    parsed_scores, verdict_text, winner = _parse_eval_response(result["text"].strip())
    return {
        "scores": parsed_scores, "verdict": verdict_text, "winner": winner,
        "input_tokens": result["input_tokens"], "output_tokens": result["output_tokens"],
    }


def _scores_by_index(parsed_scores: list[dict], n: int, reversed_order: bool) -> dict[int, dict]:
    """Map a pass's letter-keyed score rows back to ORIGINAL slot indices."""
    assert n >= 2, "n must be >= 2"
    out: dict[int, dict] = {}
    for ps in parsed_scores:
        letter = str(ps.get("answer", "")).upper()
        if len(letter) == 1 and "A" <= letter <= chr(64 + n):
            pos = ord(letter) - 65
            out[(n - 1 - pos) if reversed_order else pos] = ps
    return out


def _winner_index(winner, n: int, reversed_order: bool) -> int | None:
    """Original slot index of a pass's winner letter; None for tie/absent/garbage."""
    letter = str(winner or "").strip().upper()
    if len(letter) != 1 or not ("A" <= letter <= chr(64 + n)):
        return None
    pos = ord(letter) - 65
    return (n - 1 - pos) if reversed_order else pos


def _reconcile_eval_passes(n: int, fwd: dict, rev: dict) -> tuple[list[dict], str | None, bool]:
    """Counterbalance judge position bias (empirically the judge favors the
    last-presented answer): average each answer's dimension scores across the
    forward and reversed presentation orders, and declare a winner ONLY when
    both orderings pick the same underlying answer — otherwise tie.

    Merged dimension scores are the UNROUNDED two-pass mean (half steps:
    3.5 = the passes said 3 and 4). Rounding back to integers hid pass
    disagreement — a (4,5) split displayed as an uncontested 5 while the
    verdict criticized the answer (observed on query 590, 2026-07-10).

    Returns (score rows in original-order letters, winner, downgraded) where
    downgraded=True means the two orderings disagreed on a winner.
    """
    if not fwd["scores"] and not rev["scores"]:
        return [], None, False  # both passes unparseable — degrade like the old single pass

    fwd_by_idx = _scores_by_index(fwd["scores"], n, reversed_order=False)
    rev_by_idx = _scores_by_index(rev["scores"], n, reversed_order=True)
    merged: list[dict] = []
    for idx in range(n):
        row: dict = {"answer": chr(65 + idx)}
        passes = [s for s in (fwd_by_idx.get(idx), rev_by_idx.get(idx)) if s is not None]
        for dim in _EVAL_DIMS:
            vals = [_clamp(s.get(dim, 3)) for s in passes]
            row[dim] = sum(vals) / len(vals) if vals else 3.0
        merged.append(row)

    w_fwd = _winner_index(fwd["winner"], n, reversed_order=False)
    w_rev = _winner_index(rev["winner"], n, reversed_order=True)
    if w_fwd is not None and w_fwd == w_rev:
        return merged, chr(65 + w_fwd), False
    if fwd["winner"] is None and rev["winner"] is None:
        return merged, None, False
    disagreed = w_fwd is not None or w_rev is not None
    return merged, "tie", disagreed


async def _run_counterbalanced_eval(
    user_message: str, slots: list[SlotResult], domain_knowledge: str, model: str,
) -> tuple[list[dict], str | None, str, int, int]:
    """Judge in both presentation orders concurrently and reconcile.

    De-biases the judge's position preference: scores are per-answer averages
    across orderings; the winner must survive both. Returns
    (scores, winner, verdict_text, input_tokens, output_tokens).
    """
    assert len(slots) >= 2, "counterbalanced eval needs at least 2 slots"
    fwd, rev = await asyncio.gather(
        _judge_pass(user_message, slots, domain_knowledge, model),
        _judge_pass(user_message, list(reversed(slots)), domain_knowledge, model),
    )
    merged_scores, winner, downgraded = _reconcile_eval_passes(len(slots), fwd, rev)
    verdict_text = fwd["verdict"]
    if downgraded:
        verdict_text += (
            "\n\n[Counterbalance check: the order-reversed re-judge picked a different "
            "winner, so the verdict is recorded as a tie. Dimension scores are the "
            "average of both orderings.]"
        )
    return (
        merged_scores, winner, verdict_text,
        fwd["input_tokens"] + rev["input_tokens"],
        fwd["output_tokens"] + rev["output_tokens"],
    )


async def _save_eval_scores(
    db: AsyncSession, query_id: int, model: str,
    parsed_scores: list[dict], verdict_text: str,
    answer_map: list[tuple[str, SlotResult]],
    actor: UserIdentity,
) -> list[EvalScoreOut]:
    """Submit eval.score.set through the action bus and return view rows."""
    answer_lookup = {letter: slot for letter, slot in answer_map}
    bus_rows: list[dict] = []
    view_rows: list[EvalScoreOut] = []
    for ps in parsed_scores:
        letter = ps.get("answer", "?")
        slot_match = answer_lookup.get(letter)
        mode = slot_match.mode if slot_match else letter
        # _clamp_score (not _clamp): merged rows carry half-step means and
        # int() truncation here would silently destroy them (3.5 -> 3).
        row_dict = {
            "answer_label": letter,
            "answer_mode": mode,
            "accuracy": _clamp_score(ps.get("accuracy", 3)),
            "completeness": _clamp_score(ps.get("completeness", 3)),
            "clarity": _clamp_score(ps.get("clarity", 3)),
            "faithfulness": _clamp_score(ps.get("faithfulness", 3)),
            "overall": _clamp_score(ps.get("overall", 3)),
        }
        bus_rows.append(row_dict)
        view_rows.append(EvalScoreOut(**row_dict))

    result = await action_bus.submit(
        db=db,
        kind="eval.score.set",
        actor=actor,
        actor_type="user",
        source_query_id=query_id,
        input_data={
            "query_id": query_id,
            "eval_model": model,
            "verdict": verdict_text,
            "scores": bus_rows,
        },
    )
    if result.state != "applied":
        raise HTTPException(
            status_code=500,
            detail=f"eval.score.set action did not apply: {result.state} ({result.error})",
        )
    assert len(view_rows) == len(parsed_scores), "all parsed scores must produce output rows"
    return view_rows


@router.post("/query/{query_id}/evaluate", response_model=EvalResponse)
async def evaluate_query(
    query_id: int, req: EvalRequest,
    db: AsyncSession = Depends(get_db),
    identity: UserIdentity = Depends(resolve_identity),
):
    """Run domain-informed LLM evaluation comparing multiple response slots for a query.

    Assembles neuron context from the query's activated neurons, providing the evaluator
    with ground-truth domain knowledge. Scores responses based on accuracy against the
    domain facts, completeness, clarity, and faithfulness.
    """
    query = await db.get(Query, query_id)
    if not query:
        raise HTTPException(status_code=404, detail="Query not found")

    slots = _parse_slots(query)
    if len(slots) < 2:
        raise HTTPException(status_code=400, detail="Need at least two responses to compare")
    assert len(slots) >= 2, f"evaluate_query requires >= 2 slots, got {len(slots)}"

    # Ground truth for evaluation: the ORIGINAL briefing the answers were
    # generated from. A fresh prepare_context here re-scores a live graph
    # (usage signals shift) and mints new citation keys, so true statements
    # can be absent from the judge's ground truth. Fresh prep is only the
    # fallback for legacy queries without a stored prompt.
    domain_knowledge = query.assembled_prompt or ""
    if not domain_knowledge:
        from app.services.executor import prepare_context
        try:
            neuron_context = await prepare_context(db, query.user_message, token_budget=8000, top_k=50)
            domain_knowledge = neuron_context.system_prompt if neuron_context else ""
        except Exception:
            # If prepare_context fails, fall back to blind evaluation
            domain_knowledge = ""
    domain_knowledge = _strip_citation_keys(domain_knowledge)

    merged_scores, winner, verdict_text, eval_in, eval_out = await _run_counterbalanced_eval(
        query.user_message, slots, domain_knowledge, req.model,
    )

    answer_map = [(chr(65 + i), s) for i, s in enumerate(slots)]
    score_rows = await _save_eval_scores(
        db, query_id, req.model, merged_scores, verdict_text, answer_map, identity,
    )

    query.eval_text = verdict_text
    query.eval_model = req.model
    query.eval_input_tokens = eval_in
    query.eval_output_tokens = eval_out

    # Synaptic learning: adjust neuron weights based on eval outcome
    learning_out = None
    if settings.synaptic_learning_enabled:
        from app.services.synaptic_learning import apply_synaptic_learning
        from app.schemas import SynapticLearningOut
        summary = await apply_synaptic_learning(db, query_id, winner, answer_map)
        learning_out = SynapticLearningOut(
            outcome=summary.outcome, winner_mode=summary.winner_mode,
            neurons_adjusted=summary.neurons_adjusted,
            edges_adjusted=summary.edges_adjusted,
            avg_delta=summary.avg_delta,
            total_reward=summary.total_reward,
            total_penalty=summary.total_penalty,
        )

    await db.commit()

    return EvalResponse(
        query_id=query_id,
        eval_text=verdict_text,
        eval_model=req.model,
        eval_input_tokens=eval_in,
        eval_output_tokens=eval_out,
        scores=score_rows,
        winner=winner,
        learning=learning_out,
    )


def _clamp(val: int | float, lo: int = 1, hi: int = 5) -> int:
    try:
        return max(lo, min(hi, int(val)))
    except (TypeError, ValueError):
        return 3


def _clamp_score(val: int | float, lo: float = 1.0, hi: float = 5.0) -> float:
    """Clamp a half-step-capable score without integer truncation."""
    try:
        return max(lo, min(hi, float(val)))
    except (TypeError, ValueError):
        return 3.0


@router.get("/eval-scores", response_model=list[EvalScoreSummary])
async def list_eval_scores(db: AsyncSession = Depends(get_db)):
    """Get all eval scores for quick querying/comparison across queries."""
    result = await db.execute(
        select(EvalScore).order_by(EvalScore.query_id.desc(), EvalScore.answer_label)
    )
    rows = result.scalars().all()
    return [
        EvalScoreSummary(
            id=r.id,
            query_id=r.query_id,
            eval_model=r.eval_model,
            answer_mode=r.answer_mode,
            answer_label=r.answer_label,
            accuracy=r.accuracy,
            completeness=r.completeness,
            clarity=r.clarity,
            faithfulness=r.faithfulness,
            overall=r.overall,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]


@router.get("/learning-analytics", response_model=LearningAnalytics)
async def get_learning_analytics(db: AsyncSession = Depends(get_db)):
    """Return synaptic learning history and aggregate metrics."""
    total = (await db.execute(select(func.count(SynapticLearningEvent.id)))).scalar() or 0
    wins = (await db.execute(
        select(func.count(SynapticLearningEvent.id)).where(SynapticLearningEvent.outcome == "win")
    )).scalar() or 0
    losses = (await db.execute(
        select(func.count(SynapticLearningEvent.id)).where(SynapticLearningEvent.outcome == "loss")
    )).scalar() or 0
    avg_reward = (await db.execute(
        select(func.avg(SynapticLearningEvent.effective_delta)).where(SynapticLearningEvent.event_type == "reward")
    )).scalar() or 0.0
    avg_penalty = (await db.execute(
        select(func.avg(func.abs(SynapticLearningEvent.effective_delta))).where(SynapticLearningEvent.event_type == "penalty")
    )).scalar() or 0.0

    recent = await db.execute(
        select(SynapticLearningEvent)
        .order_by(SynapticLearningEvent.id.desc())
        .limit(100)
    )
    events = []
    for e in recent.scalars().all():
        neuron = await db.get(Neuron, e.neuron_id)
        events.append(LearningEventOut(
            id=e.id, query_id=e.query_id, neuron_id=e.neuron_id,
            neuron_label=neuron.label if neuron else None,
            event_type=e.event_type,
            old_avg_utility=e.old_avg_utility, new_avg_utility=e.new_avg_utility,
            effective_delta=e.effective_delta,
            combined_score=e.combined_score, attribution_weight=e.attribution_weight,
            outcome=e.outcome, winner_mode=e.winner_mode,
            created_at=e.created_at.isoformat() if e.created_at else None,
        ))

    return LearningAnalytics(
        total_events=total, total_wins=wins, total_losses=losses,
        avg_reward=round(avg_reward, 6), avg_penalty=round(avg_penalty, 6),
        recent_events=events,
    )


@router.post("/query/{query_id}/rate", response_model=RatingResponse)
async def rate_query(
    query_id: int, req: RatingRequest, db: AsyncSession = Depends(get_db)
):
    """Submit a user utility rating for a query and update neuron impact scores."""
    query = await db.get(Query, query_id)
    if not query:
        raise HTTPException(status_code=404, detail="Query not found")

    query.user_rating = req.utility

    neuron_ids = json.loads(query.selected_neuron_ids) if query.selected_neuron_ids else []
    updated = 0
    for nid in neuron_ids:
        neuron = await db.get(Neuron, nid)
        if neuron:
            neuron.avg_utility = update_impact_ema(neuron.avg_utility, req.utility)
            updated += 1

    firings = await db.execute(
        select(NeuronFiring).where(NeuronFiring.query_id == query_id)
    )
    for firing in firings.scalars():
        firing.outcome = "rated"

    await db.commit()

    return RatingResponse(query_id=query_id, utility=req.utility, neurons_updated=updated)


_FOLLOWUP_SYSTEM_PROMPT: str = (
    "You propose 2-3 short follow-up questions the user might logically ask "
    "NEXT, given the conversation so far. Questions must be concrete, each "
    "under 14 words, and stay inside the same topic area as the original "
    "question. Do NOT re-state the prior answer. Do NOT ask the user for "
    "clarification — these are suggestions for what they could ask next, "
    "not requests for more context.\n\n"
    "Respond with ONLY a JSON array of strings. No preamble, no markdown, "
    "no trailing prose. Example:\n"
    '  ["How does this apply to subcontractors?", '
    '"What documentation is typically required?", '
    '"What are common audit findings here?"]'
)


@router.post("/query/{query_id}/followups", response_model=FollowUpSuggestionsResponse)
async def get_followups(query_id: int, db: AsyncSession = Depends(get_db)):
    """Tier C2: suggest 2-3 follow-up questions for a completed query.

    Runs a small Haiku post-call against the user's question + the
    assistant's neuron-enhanced response. Returns an empty list on any
    error — follow-ups are nice-to-have, never block the UI.
    """
    assert query_id > 0, "query_id must be positive"
    query = await db.get(Query, query_id)
    if not query:
        raise HTTPException(status_code=404, detail="Query not found")

    response_text = ""
    if query.results_json:
        try:
            slots = json.loads(query.results_json)
            if isinstance(slots, list):
                for s in slots:
                    if isinstance(s, dict) and s.get("neurons") and s.get("response"):
                        response_text = str(s["response"])
                        break
        except json.JSONDecodeError:
            pass
    if not response_text:
        response_text = query.response_text or ""

    if not response_text.strip():
        return FollowUpSuggestionsResponse(query_id=query_id, suggestions=[], cost_usd=0.0)

    user_prompt = (
        f"Original question:\n{query.user_message[:600]}\n\n"
        f"Assistant's answer:\n{response_text[:1800]}"
    )
    try:
        result = await llm_chat(
            _FOLLOWUP_SYSTEM_PROMPT, user_prompt, max_tokens=180, model="haiku",
        )
    except (RuntimeError, ValueError):
        return FollowUpSuggestionsResponse(query_id=query_id, suggestions=[], cost_usd=0.0)

    suggestions = _parse_followups(result.get("text", ""))
    return FollowUpSuggestionsResponse(
        query_id=query_id,
        suggestions=[FollowUpSuggestion(text=s) for s in suggestions],
        cost_usd=float(result.get("cost_usd", 0.0)),
    )


def _parse_followups(raw: str) -> list[str]:
    """Extract a JSON array of follow-up strings from an LLM response.

    Defensive — Haiku sometimes wraps the array in markdown fences or
    preambles. Returns [] if unparseable.
    """
    assert isinstance(raw, str), "raw must be str"
    text = raw.strip()
    # Strip markdown code fence if present.
    if text.startswith("```"):
        fence_end = text.find("```", 3)
        if fence_end > 0:
            text = text[3:fence_end]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
    # Find the first `[` and last `]` — lets us survive a preamble line.
    lb = text.find("[")
    rb = text.rfind("]")
    if lb < 0 or rb <= lb:
        return []
    blob = text[lb:rb + 1]
    try:
        parsed = json.loads(blob)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    out: list[str] = []
    for item in parsed:
        if isinstance(item, str):
            cleaned = item.strip().strip('"').strip("'")
            if cleaned:
                out.append(cleaned[:200])
        if len(out) >= 3:
            break
    return out


async def _load_refine_prerequisites(
    query_id: int, db: AsyncSession
) -> tuple["Query", list, list]:
    query = await db.get(Query, query_id)
    if not query:
        raise HTTPException(status_code=404, detail="Query not found")

    eval_result = await db.execute(
        select(EvalScore).where(EvalScore.query_id == query_id).order_by(EvalScore.answer_label)
    )
    eval_scores = eval_result.scalars().all()
    if not eval_scores:
        raise HTTPException(status_code=400, detail="Run evaluation first before refining neurons")

    neuron_ids = json.loads(query.selected_neuron_ids) if query.selected_neuron_ids else []
    if not neuron_ids:
        raise HTTPException(status_code=400, detail="No neurons were activated for this query")

    neurons = []
    for nid in neuron_ids:
        neuron = await db.get(Neuron, nid)
        if neuron:
            neurons.append(neuron)
    # JPL Rule 5: neurons list must be non-empty before building the refinement prompt
    assert len(neurons) > 0, "refine_query requires at least one loaded neuron to build prompt"

    return query, eval_scores, neurons


def _build_refine_system_prompt(domain_knowledge: str = "") -> str:
    base_prompt = (
        "You are an impartial analyst reviewing a bifurcated knowledge pipeline.\n\n"
        "Two types of responses were generated for the same query:\n"
        "- NEURON-ENHANCED: responses built with assembled domain knowledge from a neuron graph\n"
        "- RAW BASELINE: responses from the same LLM with no domain augmentation\n\n"
        "Your job:\n"
        "1. Compare neuron-enhanced vs raw responses — identify where the neurons helped or hurt quality\n"
        "2. Find gaps: did raw slots capture anything useful that neuron slots missed?\n"
        "3. Identify inaccuracies or weaknesses in the neuron content that led to worse answers\n"
        "4. Propose concrete improvements: update neuron content, summaries, labels, or suggest new neurons to fill gaps\n"
        "5. Do NOT fabricate domain facts — only suggest changes grounded in evidence from the responses\n\n"
    )

    if domain_knowledge:
        base_prompt += (
            "## Domain Knowledge (Ground Truth)\n"
            "This is the assembled neuron context that was provided to the neuron-enhanced slots:\n\n"
            + domain_knowledge + "\n\n"
        )

    base_prompt += (
        "## Rules for Refinements\n"
        "- Only suggest changes that would meaningfully improve response quality\n"
        "- For updates: specify the exact field (content, summary, label, or is_active), the old value, and the new value\n"
        "- For new neurons: specify parent_id (an existing neuron ID from the activated list below), "
        "layer (0-5), node_type, label, content, summary, and optionally department/role_key\n"
        "- New neurons MUST attach under an existing activated neuron — do NOT create new departments (L0) or roles (L1)\n"
        "- Keep content concise and factual — neurons are context snippets, not essays\n"
        "- You only see the neurons that were activated for this query, not the full graph\n\n"
        "You MUST respond with EXACTLY a JSON block:\n"
        "```json\n"
        '{\n'
        '  "reasoning": "<2-4 sentences explaining your high-level analysis>",\n'
        '  "neuron_vs_raw_verdict": "<where neurons helped, where they hurt, what raw captured that neurons missed>",\n'
        '  "updates": [\n'
        '    {"neuron_id": <id>, "field": "<content|summary|label|is_active>", '
        '"old_value": "<current>", "new_value": "<improved>", "reason": "<why>"}\n'
        '  ],\n'
        '  "new_neurons": [\n'
        '    {"parent_id": <id|null>, "layer": <0-5>, "node_type": "<type>", '
        '"label": "<label>", "content": "<content>", "summary": "<summary>", '
        '"department": "<dept|null>", "role_key": "<key|null>", "reason": "<why>"}\n'
        '  ]\n'
        '}\n'
        "```\n"
        "No text outside the JSON block."
    )
    return base_prompt


def _build_refine_user_prompt(
    query: "Query", neurons: list, eval_scores: list, user_context: str | None
) -> str:
    neuron_sections = []
    for n in neurons:
        neuron_sections.append(
            f"Neuron #{n.id} (L{n.layer} {n.node_type})\n"
            f"  Label: {n.label}\n"
            f"  Department: {n.department or 'none'}\n"
            f"  Role Key: {n.role_key or 'none'}\n"
            f"  Summary: {n.summary or 'none'}\n"
            f"  Content:\n{n.content or '(empty)'}\n"
            f"  Invocations: {n.invocations}, Avg Utility: {n.avg_utility:.3f}, Active: {n.is_active}"
        )

    eval_lines = []
    for es in eval_scores:
        eval_lines.append(
            f"  {es.answer_label} ({es.answer_mode}): "
            f"accuracy={es.accuracy} completeness={es.completeness} "
            f"clarity={es.clarity} faithfulness={es.faithfulness} overall={es.overall}"
        )
    eval_summary = "\n".join(eval_lines)
    verdict = query.eval_text or "No verdict"

    # Include ALL responses (both neuron-enhanced and raw baseline)
    slots = _parse_slots(query)
    response_sections = []
    for i, slot in enumerate(slots):
        letter = chr(65 + i)
        slot_type = "[NEURON-ENHANCED]" if slot.neurons else "[RAW BASELINE]"
        response_sections.append(f"## Response {letter} — {slot.mode} {slot_type}\n{slot.response}")

    prompt = (
        f"## User Question\n{query.user_message}\n\n"
        f"## Eval Scores\n{eval_summary}\n\n"
        f"## Eval Verdict\n{verdict}\n\n"
        f"## All Responses\n"
        + "\n\n".join(response_sections) + "\n\n"
        f"## Activated Neurons ({len(neurons)} total)\n"
        + "\n---\n".join(neuron_sections)
    )
    if user_context and user_context.strip():
        prompt += (
            f"\n\n## Additional Context from User\n"
            f"The user has provided the following information to help guide refinement. "
            f"Use this to fill knowledge gaps, correct inaccuracies, or better define "
            f"neuron content:\n\n{user_context.strip()}"
        )
    return prompt


def _parse_refine_response(
    raw_text: str,
) -> tuple[str, str, list[NeuronUpdateSuggestion], list[NewNeuronSuggestion]]:
    reasoning = ""
    neuron_vs_raw_verdict = ""
    updates: list[NeuronUpdateSuggestion] = []
    new_neurons: list[NewNeuronSuggestion] = []
    try:
        json_str = raw_text
        if "```" in json_str:
            json_str = json_str.split("```")[1]
            if json_str.startswith("json"):
                json_str = json_str[4:]
            json_str = json_str.strip()
        parsed = json.loads(json_str)
        reasoning = parsed.get("reasoning", "")
        neuron_vs_raw_verdict = parsed.get("neuron_vs_raw_verdict", "")
        for u in parsed.get("updates", []):
            updates.append(NeuronUpdateSuggestion(
                neuron_id=u["neuron_id"],
                field=u["field"],
                old_value=str(u.get("old_value", "")),
                new_value=str(u.get("new_value", "")),
                reason=u.get("reason", ""),
            ))
        for n in parsed.get("new_neurons", []):
            new_neurons.append(NewNeuronSuggestion(
                parent_id=n.get("parent_id"),
                layer=n.get("layer", 3),
                node_type=n.get("node_type", "knowledge"),
                label=n.get("label", ""),
                content=n.get("content", ""),
                summary=n.get("summary", ""),
                department=n.get("department"),
                role_key=n.get("role_key"),
                reason=n.get("reason", ""),
            ))
    except (json.JSONDecodeError, KeyError, TypeError):
        reasoning = raw_text
        neuron_vs_raw_verdict = ""
    return reasoning, neuron_vs_raw_verdict, updates, new_neurons


@router.post("/query/{query_id}/refine", response_model=RefineResponse)
async def refine_query(
    query_id: int, req: RefineRequest, db: AsyncSession = Depends(get_db)
):
    """Generate LLM-driven neuron update and creation suggestions based on eval results.

    Opus acts as a third-party observer comparing neuron-enhanced vs. raw slot responses.
    It analyzes where neurons helped or hurt, identifies inaccuracies in neuron content,
    and proposes targeted improvements grounded in evidence from the responses.
    """
    query, eval_scores, neurons = await _load_refine_prerequisites(query_id, db)

    # Ground truth for analysis: prefer the original briefing (see evaluate).
    domain_knowledge = query.assembled_prompt or ""
    if not domain_knowledge:
        from app.services.executor import prepare_context
        try:
            neuron_context = await prepare_context(db, query.user_message, token_budget=8000, top_k=50)
            domain_knowledge = neuron_context.system_prompt if neuron_context else ""
        except Exception:
            domain_knowledge = ""
    domain_knowledge = _strip_citation_keys(domain_knowledge)

    system_prompt = _build_refine_system_prompt(domain_knowledge)
    user_prompt = _build_refine_user_prompt(query, neurons, eval_scores, req.user_context)

    result = await llm_chat(system_prompt, user_prompt, max_tokens=req.max_tokens, model=req.model)
    reasoning, neuron_vs_raw_verdict, updates, new_neurons = _parse_refine_response(result["text"].strip())

    response = RefineResponse(
        query_id=query_id,
        model=req.model,
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        reasoning=reasoning,
        neuron_vs_raw_verdict=neuron_vs_raw_verdict,
        updates=updates,
        new_neurons=new_neurons,
    )

    query.refine_json = response.model_dump_json()
    await db.commit()

    return response


@router.post("/query/{query_id}/refine/apply", response_model=ApplyRefineResponse)
async def apply_refinements(
    query_id: int, req: ApplyRefineRequest,
    db: AsyncSession = Depends(get_db),
    identity: UserIdentity = Depends(resolve_identity),
):
    """Apply selected neuron update and creation suggestions from a prior refine call."""
    query = await db.get(Query, query_id)
    if not query:
        raise HTTPException(status_code=404, detail="Query not found")
    if not query.refine_json:
        raise HTTPException(status_code=400, detail="No refinement suggestions stored — run /refine first")

    stored = json.loads(query.refine_json)
    updates_list = stored.get("updates", [])
    new_neurons_list = stored.get("new_neurons", [])

    updated_count = 0
    for idx in req.update_ids:
        if idx < 0 or idx >= len(updates_list):
            continue
        u = updates_list[idx]
        result = await action_bus.submit(
            db=db, kind="neuron.refine", actor=identity, actor_type="user",
            source_query_id=query_id,
            input_data={
                "target_neuron_id": u["neuron_id"],
                "field": u["field"],
                "old_value": str(u.get("old_value", "")),
                "new_value": str(u["new_value"]),
                "query_id": query_id,
                "reason": u.get("reason", ""),
            },
        )
        if result.state == "applied":
            updated_count += 1

    created_count = 0
    state = await get_system_state(db)
    for idx in req.new_neuron_ids:
        if idx < 0 or idx >= len(new_neurons_list):
            continue
        n = new_neurons_list[idx]
        result = await action_bus.submit(
            db=db, kind="neuron.create", actor=identity, actor_type="user",
            source_query_id=query_id,
            input_data={
                "spec": {
                    "parent_id": n.get("parent_id"),
                    "layer": n.get("layer", 3),
                    "node_type": n.get("node_type", "knowledge"),
                    "label": n.get("label", ""),
                    "content": n.get("content", ""),
                    "summary": n.get("summary", ""),
                    "department": n.get("department"),
                    "role_key": n.get("role_key"),
                    "source_origin": "manual",
                },
                "query_id": query_id,
                "total_queries": state.total_queries,
                "reason": n.get("reason", ""),
            },
        )
        if result.state == "applied":
            created_count += 1

    await db.commit()
    return ApplyRefineResponse(updated=updated_count, created=created_count)
