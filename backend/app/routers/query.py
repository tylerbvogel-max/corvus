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
    SlotResult,  # For backward-compat: parsing legacy multi-slot query data
)
from app.governance.output_guard import GuardResult, run_guards
from app.services.executor import execute_query, prepare_context
from app.services.pipeline import PipelineStageError
from app.services.llm_provider import llm_chat, estimate_cost, MODEL_REGISTRY
from app.services import action_bus
from app.middleware.rbac import UserIdentity, resolve_identity


def get_available_models() -> list[dict]:
    return [{"display_name": n, "provider": "anthropic", "api_id": m.api_id, "tier": "frontier", "input_price": m.input_price, "output_price": m.output_price} for n, m in MODEL_REGISTRY.items()]


def get_valid_model_names() -> set[str]:
    return set(MODEL_REGISTRY.keys())
from app.services.neuron_service import get_system_state, score_candidates
from app.services.scoring_engine import update_impact_ema
from app.services.input_guard import check_input, check_output_risk, check_output_grounding
from pydantic import BaseModel, Field
from sqlalchemy import select, func

router = APIRouter(tags=["query"])


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
        neuron_scores=ctx.neuron_scores,
        classify_cost_usd=ctx.classify_cost_usd,
    )


@router.get("/models")
async def list_available_models():
    """Return list of LLM models whose provider API key is configured."""
    return get_available_models()


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    model: str = Field("haiku")
    history: list[dict] = Field(default_factory=list)


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
    result = await llm_chat(system_prompt, user_message, max_tokens=2048, model=req.model)
    cost = estimate_cost(req.model, result["input_tokens"], result["output_tokens"])
    return ChatResponse(
        response=result["text"],
        model=req.model,
        input_tokens=result["input_tokens"],
        output_tokens=result["output_tokens"],
        cost_usd=cost,
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


def _parse_pending_refine(refine_json: str | None) -> dict | None:
    if not refine_json:
        return None
    try:
        parsed = json.loads(refine_json)
        assert parsed is None or isinstance(parsed, dict), "pending_refine must be dict or None"
        return parsed
    except json.JSONDecodeError:
        return None


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
    pending_refine = _parse_pending_refine(query.refine_json)

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
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Input blocked by safety filter",
                "flags": guard_result.flags,
            },
        )

    try:
        slot_dicts = [s.model_dump() for s in req.slots] if req.slots else None
        result = await execute_query(
            db, req.message,
            slots=slot_dicts,
            prior_neuron_ids=req.prior_neuron_ids,
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

    return QueryResponse(**result)


@router.post("/query/stream")
async def post_query_stream(req: QueryRequest, db: AsyncSession = Depends(get_db)):
    """SSE streaming version of POST /query — emits pipeline stage events in real time."""

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
                await queue.put({"event": "error", "data": {"message": "Input blocked by safety filter"}})
                return

            # Execute pipeline with stage callbacks (Session 3+ multi-slot path)
            # Convert QuerySlotRequest list to dict list for executor
            slot_dicts = None
            if req.slots:
                slot_dicts = [s.model_dump() for s in req.slots]

            result = await execute_query(
                db, req.message,
                slots=slot_dicts,
                on_stage=on_stage,
                prior_neuron_ids=req.prior_neuron_ids,
            )

            # Output checks
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


def _eval_slot_label(slot: SlotResult) -> str:
    if slot.label:
        return slot.label
    parts = [slot.model.title()]
    if slot.neurons:
        parts.append("+ Neurons")
    if slot.token_budget is not None and slot.neurons:
        parts.append(f"@ {slot.token_budget // 1000}K")
    return " ".join(parts)


def _build_eval_prompts(
    user_message: str, slots: list[SlotResult], domain_knowledge: str = "",
) -> tuple[str, str, list[tuple[str, SlotResult]]]:
    answer_map: list[tuple[str, SlotResult]] = []
    sections = [f"User's question:\n{user_message}"]
    for i, slot in enumerate(slots):
        label = _eval_slot_label(slot)
        letter = chr(65 + i)
        answer_map.append((letter, slot))
        sections.append(f"Answer {letter} ({label}):\n{slot.response}")

    score_template = []
    for letter, slot in answer_map:
        label = _eval_slot_label(slot)
        score_template.append(
            f'  {{"answer": "{letter}", "label": "{label}", '
            f'"accuracy": <1-5>, "completeness": <1-5>, "clarity": <1-5>, '
            f'"faithfulness": <1-5>, "overall": <1-5>}}'
        )

    eval_prompt = "\n\n---\n\n".join(sections)
    assert len(sections) >= 3, "eval prompt must include question + at least 2 answers"

    # Build system prompt with optional domain knowledge
    if domain_knowledge:
        eval_system = (
            "You are a domain expert evaluating AI responses against authoritative knowledge.\n\n"
            "## Domain Knowledge (Ground Truth)\n"
            "Use the following domain facts to assess accuracy and faithfulness:\n\n"
            + domain_knowledge + "\n\n"
            "## Evaluation Instructions\n"
            "Score each answer on these dimensions (1=poor, 5=excellent):\n"
            "- Accuracy: aligns with the domain knowledge above; factually correct\n"
            "- Completeness: covers the full question\n"
            "- Clarity: well-structured, easy to understand\n"
            "- Faithfulness: no hallucinations or claims unsupported by domain knowledge (5=fully faithful)\n"
            "- Overall: holistic quality\n\n"
            "You MUST respond with EXACTLY this format — a JSON block followed by your verdict:\n\n"
            "```json\n"
            '{"scores": [\n'
            + ",\n".join(score_template) + "\n"
            "],\n"
            '"winner": "<letter or tie>",\n'
            '"verdict": "<2-4 sentence comparison explaining your reasoning>"\n'
            "}\n"
            "```\n\n"
            "No other text outside the JSON block. Use the answer labels (A, B, etc.) in your verdict."
        )
    else:
        eval_system = (
            "You are a blind evaluator comparing AI responses. You have NO prior context — "
            "only the user's question and the answers provided.\n\n"
            "Score each answer on these dimensions (1=poor, 5=excellent):\n"
            "- Accuracy: factual correctness\n"
            "- Completeness: covers the full question\n"
            "- Clarity: well-structured, easy to understand\n"
            "- Faithfulness: no hallucinations or unsupported claims (5=fully faithful)\n"
            "- Overall: holistic quality\n\n"
            "You MUST respond with EXACTLY this format — a JSON block followed by your verdict:\n\n"
            "```json\n"
            '{"scores": [\n'
            + ",\n".join(score_template) + "\n"
            "],\n"
            '"winner": "<letter or tie>",\n'
            '"verdict": "<2-4 sentence comparison explaining your reasoning>"\n'
            "}\n"
            "```\n\n"
            "No other text outside the JSON block. Use the answer labels (A, B, etc.) in your verdict."
        )
    assert eval_system and eval_prompt, "eval prompts must be non-empty"

    return eval_system, eval_prompt, answer_map


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
        row_dict = {
            "answer_label": letter,
            "answer_mode": mode,
            "accuracy": _clamp(ps.get("accuracy", 3)),
            "completeness": _clamp(ps.get("completeness", 3)),
            "clarity": _clamp(ps.get("clarity", 3)),
            "faithfulness": _clamp(ps.get("faithfulness", 3)),
            "overall": _clamp(ps.get("overall", 3)),
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

    # Assemble neuron context (ground truth for evaluation)
    from app.services.executor import prepare_context
    neuron_context = None
    domain_knowledge = ""
    try:
        neuron_context = await prepare_context(db, query.user_message, token_budget=8000, top_k=50)
        domain_knowledge = neuron_context.system_prompt if neuron_context else ""
    except Exception:
        # If prepare_context fails, fall back to blind evaluation
        domain_knowledge = ""

    eval_system, eval_prompt, answer_map = _build_eval_prompts(query.user_message, slots, domain_knowledge)
    assert len(answer_map) >= 2, "answer_map must have at least 2 entries"

    result = await llm_chat(eval_system, eval_prompt, max_tokens=2048, model=req.model)
    raw_text = result["text"].strip()

    parsed_scores, verdict_text, winner = _parse_eval_response(raw_text)
    score_rows = await _save_eval_scores(
        db, query_id, req.model, parsed_scores, verdict_text, answer_map, identity,
    )

    query.eval_text = verdict_text
    query.eval_model = req.model
    query.eval_input_tokens = result["input_tokens"]
    query.eval_output_tokens = result["output_tokens"]

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
        eval_input_tokens=result["input_tokens"],
        eval_output_tokens=result["output_tokens"],
        scores=score_rows,
        winner=winner,
        learning=learning_out,
    )


def _clamp(val: int | float, lo: int = 1, hi: int = 5) -> int:
    try:
        return max(lo, min(hi, int(val)))
    except (TypeError, ValueError):
        return 3


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

    # Assemble fresh neuron context (ground truth for analysis)
    from app.services.executor import prepare_context
    domain_knowledge = ""
    try:
        neuron_context = await prepare_context(db, query.user_message, token_budget=8000, top_k=50)
        domain_knowledge = neuron_context.system_prompt if neuron_context else ""
    except Exception:
        domain_knowledge = ""

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
