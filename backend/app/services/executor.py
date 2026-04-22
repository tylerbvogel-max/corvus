"""Full pipeline orchestration: classify → score → assemble → execute → record."""

import asyncio
import json
import time
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Callable, Awaitable

# Optional callback for streaming pipeline progress
StageCallback = Callable[[str, dict], Awaitable[None]] | None

from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.config import settings
from app.models import Neuron, Query, NeuronEdge
from app.services.classifier import classify_query
from app.services.llm_provider import llm_chat, MODEL_REGISTRY
from app.services.neuron_service import (
    get_neurons_by_filter,
    get_system_state,
    score_candidates,
    spread_activation,
    record_firing,
    apply_diversity_floor,
    select_with_hierarchy,
)
from app.services.prompt_assembler import assemble_prompt
from app.services.propagation import propagate_activation
from app.services.neuron_service import NeuronCandidate
from app.services.scoring_engine import NeuronScoreBreakdown
from app.tenant import tenant


@dataclass
class PreparedContext:
    """Result of the classify → score → spread → inhibit → assemble pipeline."""
    system_prompt: str
    intent: str
    departments: list[str]
    role_keys: list[str]
    keywords: list[str]
    neuron_scores: list[dict] = field(default_factory=list)
    neurons_activated: int = 0
    neuron_map: dict[int, Neuron] = field(default_factory=dict)
    all_scored: list[NeuronScoreBreakdown] = field(default_factory=list)
    classify_cost_usd: float = 0.0
    classify_input_tokens: int = 0
    classify_output_tokens: int = 0
    # Pattern #5: typed pipeline DAG telemetry — per-stage timing + status.
    # Populated when prepare_context runs through the pipeline runner;
    # None for structural fast-path results (no pipeline ran).
    stage_telemetry: list[dict] = field(default_factory=list)


async def _embed_query_async(user_message: str):
    import concurrent.futures
    loop = asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        from app.services.embedding_service import embed_text
        return await loop.run_in_executor(pool, embed_text, user_message)


async def _embed_and_classify(user_message: str) -> tuple[dict, object, str, list, list, list]:
    embed_task = asyncio.create_task(_embed_query_async(user_message))
    classify_task = asyncio.create_task(classify_query(user_message))

    classify_result = await classify_task
    query_embedding = None
    try:
        query_embedding = await embed_task
    except Exception as e:
        print(f"Query embedding failed, falling back to keywords: {e}")

    classification = classify_result["classification"]
    intent = classification.get("intent", "general_query")
    departments = classification.get("departments", [])
    role_keys = classification.get("role_keys", [])
    keywords = classification.get("keywords", [])

    assert isinstance(classify_result, dict), "classify_result must be a dict"
    assert isinstance(intent, str), "intent must be a string"
    return classify_result, query_embedding, intent, departments, role_keys, keywords


async def _select_and_score_candidates(
    db: AsyncSession,
    query_embedding,
    effective_pool: int,
    keywords: list[str],
    departments: list[str],
    role_keys: list[str],
    total_queries: int,
) -> tuple[list[NeuronScoreBreakdown], list[NeuronScoreBreakdown]]:
    """Score neuron and engram candidates.  Returns (scored_neurons, scored_engrams)."""
    assert isinstance(effective_pool, int) and effective_pool > 0, \
        "effective_pool must be a positive integer"

    scored_engrams: list[NeuronScoreBreakdown] = []
    semantic_results: list[tuple[int, str, float]] | None = None

    if settings.semantic_prefilter_enabled and query_embedding is not None:
        from app.services.semantic_prefilter import semantic_prefilter
        semantic_results = await semantic_prefilter(db, query_embedding, top_n_override=effective_pool)

    if semantic_results:
        # Partition into neurons and engrams
        neuron_sims = {eid: sim for eid, etype, sim in semantic_results if etype == "neuron"}
        engram_sims = {eid: sim for eid, etype, sim in semantic_results if etype == "engram"}

        # Score neurons
        sem_ids = list(neuron_sims.keys())
        candidates = await _load_candidates_by_ids(db, sem_ids, keywords) if sem_ids else []
        scored = await score_candidates(
            db, candidates, total_queries, keywords,
            departments, role_keys,
            query_embedding=query_embedding,
            precomputed_similarities=neuron_sims,
        )

        # Score engrams if any matched
        if engram_sims and settings.engram_resolve_enabled:
            from app.services.engram_service import get_engram_candidates, score_engram_candidates
            engram_cands = await get_engram_candidates(db, keywords)
            # Filter to only engrams that appeared in semantic results
            engram_cands = [c for c in engram_cands if c.id in engram_sims]
            if engram_cands:
                scored_engrams = await score_engram_candidates(
                    db, engram_cands, total_queries, keywords,
                    precomputed_similarities=engram_sims,
                )
    else:
        candidates = await get_neurons_by_filter(db, departments, role_keys, keywords)
        if not candidates:
            candidates = await get_neurons_by_filter(db)
        scored = await score_candidates(
            db, candidates, total_queries, keywords, departments, role_keys,
            query_embedding=query_embedding,
        )

    assert isinstance(scored, list), "scored must be a list"
    return scored, scored_engrams


async def _apply_inhibition_and_boost(
    db: AsyncSession,
    scored: list[NeuronScoreBreakdown],
    effective_top_k: int,
    project_path: str | None,
) -> tuple[list[NeuronScoreBreakdown], int]:
    if settings.inhibition_enabled:
        from app.services.inhibitory_service import apply_inhibition
        all_scored, effective_top_k = await apply_inhibition(db, scored, effective_top_k)
    else:
        all_scored = await apply_diversity_floor(db, scored, effective_top_k)

    if project_path and getattr(settings, 'project_cache_enabled', False):
        from app.services.project_cache import get_project_boost
        candidate_ids = [s.neuron_id for s in all_scored[:effective_top_k]]
        boosts = await get_project_boost(db, project_path, candidate_ids)
        if boosts:
            for s in all_scored[:effective_top_k]:
                s.combined *= boosts.get(s.neuron_id, 1.0)
            all_scored[:effective_top_k] = sorted(
                all_scored[:effective_top_k], key=lambda s: s.combined, reverse=True
            )

    assert len(all_scored) >= 0, "all_scored must not be negative length"
    assert effective_top_k >= 0, f"effective_top_k must be non-negative, got {effective_top_k}"
    return all_scored, effective_top_k


async def _load_neuron_map(db: AsyncSession, neuron_ids: list[int]) -> dict[int, Neuron]:
    neuron_map: dict[int, Neuron] = {}
    if neuron_ids:
        result = await db.execute(select(Neuron).where(Neuron.id.in_(neuron_ids)))
        for neuron in result.scalars().all():
            neuron_map[neuron.id] = neuron
    assert isinstance(neuron_map, dict), "neuron_map must be a dict"
    assert len(neuron_map) <= len(neuron_ids), "neuron_map cannot exceed requested IDs"
    return neuron_map


def _build_neuron_score_dicts(
    scored: list[NeuronScoreBreakdown],
    neuron_map: dict[int, Neuron],
) -> list[dict]:
    assert isinstance(scored, list), "scored must be a list"
    assert isinstance(neuron_map, dict), "neuron_map must be a dict"
    return [
        {"neuron_id": s.neuron_id, "combined": s.combined, "burst": s.burst,
         "impact": s.impact, "precision": s.precision, "novelty": s.novelty,
         "recency": s.recency, "relevance": s.relevance, "spread_boost": s.spread_boost,
         "label": neuron_map[s.neuron_id].label if s.neuron_id in neuron_map else None,
         "department": neuron_map[s.neuron_id].department if s.neuron_id in neuron_map else None,
         "layer": neuron_map[s.neuron_id].layer if s.neuron_id in neuron_map else 0,
         "parent_id": neuron_map[s.neuron_id].parent_id if s.neuron_id in neuron_map else None,
         "summary": neuron_map[s.neuron_id].summary if s.neuron_id in neuron_map else None}
        for s in scored
    ]


async def _resolve_fired_engrams(db, scored_engrams, effective_budget, _emit):
    """Fetch live regulatory text for fired engrams via eCFR API."""
    if not scored_engrams or not settings.engram_resolve_enabled:
        return []
    from app.services.regulatory_resolve import resolve_engrams
    from app.models import Engram
    engram_ids = [s.neuron_id for s in scored_engrams[:10]]
    engram_rows = (await db.execute(
        select(Engram).where(Engram.id.in_(engram_ids))
    )).scalars().all()
    engram_map = {e.id: e for e in engram_rows}
    fired_pairs = [
        (engram_map[s.neuron_id], s.combined)
        for s in scored_engrams[:10] if s.neuron_id in engram_map
    ]
    engram_budget = int(effective_budget * settings.engram_token_budget_fraction)
    resolved = await resolve_engrams(db, fired_pairs, engram_budget)
    await _emit("regulatory_resolve", {"status": "done", "detail": {
        "resolved": len(resolved),
        "cached": sum(1 for r in resolved if r.source == "cache"),
        "live": sum(1 for r in resolved if r.source == "live_api"),
        "fallback": sum(1 for r in resolved if r.source == "fallback_summary"),
    }})
    return resolved


async def prepare_context(
    db: AsyncSession, user_message: str, token_budget: int | None = None,
    top_k: int | None = None, project_path: str | None = None,
    on_stage: StageCallback = None, prior_neuron_ids: list[int] | None = None,
) -> PreparedContext:
    """Run the classify → score → spread → inhibit → resolve → assemble pipeline.

    Pattern #5: stages are composed via the typed pipeline runner; each stage
    emits timing + telemetry and any stage failure hard-fails with a
    `PipelineStageError` carrying the stage name.
    """
    assert isinstance(user_message, str) and len(user_message.strip()) > 0, \
        "user_message must be a non-empty string"

    from app.services.pipeline import PipelineContext, run_pipeline
    from app.services.pipeline.state import PipelineState
    from app.services.pipeline.stages import build_default_pipeline

    initial_state = PipelineState(
        user_message=user_message,
        effective_top_k=top_k or settings.top_k_neurons,
        effective_pool=settings.semantic_prefilter_top_n,
        effective_budget=token_budget or settings.token_budget,
        project_path=project_path,
        prior_neuron_ids=prior_neuron_ids,
    )
    pipeline_ctx = PipelineContext(db=db, on_stage=on_stage)

    final = await run_pipeline(build_default_pipeline(), initial_state, pipeline_ctx)

    # Structural resolve short-circuit returns the already-built PreparedContext.
    if isinstance(final, PreparedContext):
        final.stage_telemetry = pipeline_ctx.telemetry_json()
        return final

    state: PipelineState = final
    assert isinstance(state, PipelineState), "pipeline must return a PipelineState when not short-circuiting"

    if state.project_path and getattr(settings, 'project_cache_enabled', False):
        from app.services.project_cache import record_project_firings
        await record_project_firings(db, state.project_path, state.top_slice)

    result = PreparedContext(
        system_prompt=state.system_prompt, intent=state.intent,
        departments=state.departments, role_keys=state.role_keys, keywords=state.keywords,
        neuron_scores=_build_neuron_score_dicts(state.top_slice, state.neuron_map),
        neurons_activated=min(len(state.all_scored), state.effective_top_k),
        neuron_map=state.neuron_map, all_scored=state.top_slice,
        classify_cost_usd=state.classify_result.get("cost_usd", 0),
        classify_input_tokens=state.classify_result.get("input_tokens", 0),
        classify_output_tokens=state.classify_result.get("output_tokens", 0),
        stage_telemetry=pipeline_ctx.telemetry_json(),
    )
    assert isinstance(result.system_prompt, str) and len(result.system_prompt) > 0, \
        "PreparedContext.system_prompt must be a non-empty string"
    assert result.neurons_activated >= 0, \
        f"neurons_activated must be non-negative, got {result.neurons_activated}"
    return result


async def _assemble_top_slice(
    db: AsyncSession,
    all_scored: list[NeuronScoreBreakdown],
    effective_top_k: int,
    intent: str,
    effective_budget: int,
    prior_neuron_ids: list[int] | None,
    resolved_regulations: list,
) -> tuple[list[NeuronScoreBreakdown], dict[int, Neuron], str]:
    """Select top-k neurons, load their data, and assemble the system prompt."""
    if settings.hierarchy_selection_enabled:
        top_slice = await select_with_hierarchy(db, all_scored, effective_top_k)
    else:
        top_slice = all_scored[:effective_top_k]
    neuron_map = await _load_neuron_map(db, [s.neuron_id for s in top_slice])

    prior_neuron_map: dict[int, Neuron] | None = None
    if prior_neuron_ids:
        missing_ids = [nid for nid in prior_neuron_ids if nid not in neuron_map]
        if missing_ids:
            extra = await _load_neuron_map(db, missing_ids)
            prior_neuron_map = {**neuron_map, **extra}
        else:
            prior_neuron_map = neuron_map

    system_prompt = assemble_prompt(
        intent, top_slice, neuron_map, budget_tokens=effective_budget,
        prior_neuron_ids=prior_neuron_ids, prior_neuron_map=prior_neuron_map,
        resolved_regulations=resolved_regulations,
    )
    return top_slice, neuron_map, system_prompt


# Each slot is a dict: {mode, model, neurons, response, input_tokens, output_tokens, cost_usd}
# Modes: "{model}_neuron" or "{model}_raw" for any model in MODEL_REGISTRY.

def _build_model_map() -> MappingProxyType:
    """Generate MODEL_MAP from MODEL_REGISTRY: {mode_key: model_display_name}."""
    mapping: dict[str, str] = {}
    for name in MODEL_REGISTRY:
        mapping[f"{name}_neuron"] = name
        mapping[f"{name}_raw"] = name
    return MappingProxyType(mapping)

MODEL_MAP = _build_model_map()
NEURON_MODES = frozenset(k for k in MODEL_MAP if k.endswith("_neuron"))

# LLM PROMPT INTENT: Minimal baseline system prompt for raw (non-neuron) comparison slots.
#   Provides only the organizational context without neuron-assembled knowledge, serving as
#   a control condition to measure the value added by the neuron graph.
# INPUT: User's natural-language query sent as user message. No neuron context is injected.
# OUTPUT FORMAT: Free-form natural-language response (no structured format required).
# FAILURE MODES: None specific to this prompt. LLM may produce generic or less domain-specific
#   answers compared to neuron-enhanced slots, which is the expected baseline behavior.
# Loaded from tenant config — each tenant defines its own baseline context.
# Falls back to empty string if tenant.yaml omits baseline_prompt.
def _get_raw_baseline_prompt() -> str:
    from app.tenant import tenant
    return tenant.baseline_prompt

# LLM PROMPT INTENT: Efficiency prefix prepended to system prompts when using the Sonnet model,
#   reducing verbosity and token waste since Sonnet tends toward longer, more elaborate responses.
# INPUT: Prepended before either the neuron-assembled prompt or RAW_BASELINE_PROMPT.
#   The user query is sent separately as the user message.
# OUTPUT FORMAT: No change to expected format — this is a behavioral modifier, not a format constraint.
# FAILURE MODES: If prepended to a non-Sonnet model by mistake, may cause unnecessarily terse
#   responses but is otherwise harmless. The prefix is only applied when is_sonnet is True.
SONNET_EFFICIENCY_PREFIX = "Be precise and concise. Prioritize accuracy over elaboration. Do not repeat the question or restate what is already known — lead with the answer.\n\n"

# LLM PROMPT INTENT: Conversational style wrapper appended to system prompts when chat_style
#   is "conversational". Makes responses shorter, more engaging, and format-friendly for human
#   readers in the primary chat UI. Avoids markdown tables and encourages follow-up exploration.
# INPUT: Appended after the neuron-assembled or raw system prompt.
# OUTPUT FORMAT: Short conversational prose with bold/italic for emphasis, no markdown tables.
# FAILURE MODES: If appended to Query Lab prompts by mistake, responses would be overly casual
#   and truncated — gated on chat_style to prevent this.
CONVERSATIONAL_STYLE_SUFFIX = (
    "\n\n## Response Style\n"
    "You are having a conversation, not writing a report. Follow these rules strictly:\n"
    "- Keep answers SHORT — 2-4 sentences for simple questions, 1-2 short paragraphs max for complex ones.\n"
    "- Write like you're talking to a colleague: warm, direct, no filler.\n"
    "- Use **bold** and *italic* for emphasis. Never use markdown tables (no |---|---| separators).\n"
    "- Use short bullet lists only when listing 3+ items. Prefer flowing prose otherwise.\n"
    "- End with a brief engagement hook that connects to related topics from your knowledge context. "
    "For example: 'This also ties into [related topic] — want me to dig into that?' or "
    "'There's an interesting angle with [related concept] if you're curious.'\n"
    "- Do NOT start with 'Great question' or similar filler. Lead with the answer."
)


def _build_neuron_score_dicts(
    scored: list[NeuronScoreBreakdown],
    neuron_map: dict[int, Neuron],
) -> list[dict]:
    assert isinstance(scored, list), "scored must be a list"
    assert isinstance(neuron_map, dict), "neuron_map must be a dict"
    return [
        {"neuron_id": s.neuron_id, "combined": s.combined, "burst": s.burst,
         "impact": s.impact, "precision": s.precision, "novelty": s.novelty,
         "recency": s.recency, "relevance": s.relevance, "spread_boost": s.spread_boost,
         "entity_type": getattr(s, "entity_type", "neuron"),
         "label": neuron_map[s.neuron_id].label if s.neuron_id in neuron_map else None,
         "department": neuron_map[s.neuron_id].department if s.neuron_id in neuron_map else None,
         "layer": neuron_map[s.neuron_id].layer if s.neuron_id in neuron_map else 0,
         "parent_id": neuron_map[s.neuron_id].parent_id if s.neuron_id in neuron_map else None,
         "summary": neuron_map[s.neuron_id].summary if s.neuron_id in neuron_map else None}
        for s in scored
    ]


def _normalize_slot_specs(
    modes: list[str],
    token_budget: int | None,
    slots_v2: list[dict] | None,
) -> list[dict]:
    assert (modes and len(modes) > 0) or (slots_v2 and len(slots_v2) > 0), \
        "Either modes or slots_v2 must be provided and non-empty"
    if slots_v2:
        slot_specs = slots_v2
    else:
        slot_specs = [
            {"mode": m, "token_budget": token_budget or settings.token_budget,
             "top_k": settings.top_k_neurons, "label": None}
            for m in modes
        ]
    assert len(slot_specs) > 0, "slot_specs must be non-empty after normalization"
    return slot_specs


def _compute_neuron_slot_max(slot_specs: list[dict], attr: str, default_val: int) -> int:
    assert isinstance(slot_specs, list), "slot_specs must be a list"
    assert isinstance(attr, str), "attr must be a string"
    return max(
        (s.get(attr, default_val) for s in slot_specs if s["mode"] in NEURON_MODES),
        default=default_val,
    )


async def _run_neuron_pipeline(
    db: AsyncSession,
    user_message: str,
    slot_specs: list[dict],
    max_top_k: int,
    on_stage: StageCallback,
    prior_neuron_ids: list[int] | None = None,
) -> tuple[PreparedContext | None, dict]:
    assert isinstance(user_message, str) and len(user_message.strip()) > 0, \
        "user_message must be non-empty"
    needs_neurons = any(s["mode"] in NEURON_MODES for s in slot_specs)
    default_classify = {"classification": {}, "input_tokens": 0, "output_tokens": 0}
    if not needs_neurons:
        return None, default_classify
    neuron_budget = max(s["token_budget"] for s in slot_specs if s["mode"] in NEURON_MODES)
    ctx = await prepare_context(
        db, user_message,
        token_budget=neuron_budget,
        top_k=max_top_k,
        on_stage=on_stage,
        prior_neuron_ids=prior_neuron_ids,
    )
    classify_result = {
        "classification": {},
        "input_tokens": ctx.classify_input_tokens,
        "output_tokens": ctx.classify_output_tokens,
        "cost_usd": ctx.classify_cost_usd,
    }
    return ctx, classify_result


def _get_cached_prompt(
    prompt_cache: dict[tuple[int, int], str],
    budget: int,
    slot_top_k: int,
    intent: str,
    all_scored: list[NeuronScoreBreakdown],
    neuron_map: dict[int, Neuron],
) -> str:
    assert isinstance(budget, int) and budget > 0, "budget must be a positive int"
    assert isinstance(slot_top_k, int) and slot_top_k > 0, "slot_top_k must be a positive int"
    key = (budget, slot_top_k)
    if key not in prompt_cache:
        prompt_cache[key] = assemble_prompt(intent, all_scored[:slot_top_k], neuron_map, budget_tokens=budget)
    return prompt_cache[key]


def _create_query_record(
    user_message: str,
    ctx: PreparedContext | None,
    needs_neurons: bool,
    slot_specs: list[dict],
    primary_prompt: str,
    classify_result: dict,
) -> Query:
    assert isinstance(user_message, str), "user_message must be a string"
    assert isinstance(slot_specs, list) and len(slot_specs) > 0, \
        "slot_specs must be non-empty"
    selected_ids = [s.neuron_id for s in ctx.all_scored] if ctx else []
    # Pattern #5: snapshot per-stage telemetry onto the Query row as JSONB.
    stage_telemetry = ctx.stage_telemetry if ctx else []
    return Query(
        user_message=user_message,
        classified_intent=ctx.intent if needs_neurons else None,
        classified_departments=json.dumps(ctx.departments if ctx else []),
        classified_role_keys=json.dumps(ctx.role_keys if ctx else []),
        classified_keywords=json.dumps(ctx.keywords if ctx else []),
        selected_neuron_ids=json.dumps(selected_ids),
        assembled_prompt=primary_prompt if needs_neurons else None,
        classify_input_tokens=classify_result["input_tokens"],
        classify_output_tokens=classify_result["output_tokens"],
        run_neuron=needs_neurons,
        run_opus=any(s["mode"] == "opus_raw" for s in slot_specs),
        stage_telemetry_json=stage_telemetry if stage_telemetry else None,
    )


def _build_slot_system_prompt(
    mode: str, neuron_prompt: str, chat_style: str | None = None,
) -> tuple[str, int]:
    assert mode in MODEL_MAP, f"Unknown mode: {mode}"
    model = MODEL_MAP[mode]
    is_sonnet = model == "sonnet"
    if mode in NEURON_MODES:
        prompt = (SONNET_EFFICIENCY_PREFIX + neuron_prompt) if is_sonnet else neuron_prompt
        if chat_style == "conversational":
            prompt += CONVERSATIONAL_STYLE_SUFFIX
        return prompt, 4096
    baseline = _get_raw_baseline_prompt()
    raw = (SONNET_EFFICIENCY_PREFIX + baseline) if is_sonnet else baseline
    if chat_style == "conversational":
        raw += CONVERSATIONAL_STYLE_SUFFIX
    return raw, 4096


def _format_slot_result(spec: dict, result: dict) -> dict:
    assert "mode" in spec, "spec must contain 'mode'"
    assert "text" in result, "result must contain 'text'"
    mode = spec["mode"]
    budget = spec.get("token_budget", settings.token_budget)
    slot_top_k = spec.get("top_k", settings.top_k_neurons)
    return {
        "mode": mode,
        "model": MODEL_MAP.get(mode, mode.rsplit("_", 1)[0]),
        "neurons": mode in NEURON_MODES,
        "response": result["text"],
        "input_tokens": result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "cost_usd": result["cost_usd"],
        "cache_creation_tokens": result.get("cache_creation_tokens", 0),
        "cache_read_tokens": result.get("cache_read_tokens", 0),
        "duration_ms": result.get("duration_ms", 0),
        "token_budget": budget if mode in NEURON_MODES else None,
        "top_k": slot_top_k if mode in NEURON_MODES else None,
        "label": spec.get("label"),
        "model_version": result.get("model_version"),
        "error": result.get("error", False),
    }


async def _execute_and_collect_slots(
    slot_specs: list[dict],
    user_message: str,
    prompt_cache: dict[tuple[int, int], str],
    intent: str,
    all_scored: list[NeuronScoreBreakdown],
    neuron_map: dict[int, Neuron],
    on_stage: StageCallback,
    chat_style: str | None = None,
) -> list[dict]:
    assert len(slot_specs) > 0, "slot_specs must be non-empty"

    async def _timed_chat(*args, **kwargs):
        t0 = time.monotonic()
        result = await llm_chat(*args, **kwargs)
        result["duration_ms"] = round((time.monotonic() - t0) * 1000)
        return result

    tasks: list[asyncio.Task] = []
    for spec in slot_specs:
        mode = spec["mode"]
        budget = spec.get("token_budget", settings.token_budget)
        slot_top_k = spec.get("top_k", settings.top_k_neurons)
        model = MODEL_MAP.get(mode)
        if mode in NEURON_MODES:
            neuron_prompt = _get_cached_prompt(prompt_cache, budget, slot_top_k, intent, all_scored, neuron_map)
        else:
            neuron_prompt = ""
        prompt, default_max_tokens = _build_slot_system_prompt(mode, neuron_prompt, chat_style)
        max_tokens = spec.get("max_output_tokens") or default_max_tokens
        tasks.append(asyncio.create_task(
            _timed_chat(prompt, user_message, max_tokens=max_tokens, model=model)
        ))

    slot_results: list[dict | None] = [None] * len(slot_specs)

    async def _collect_slot(i: int, spec: dict, task: asyncio.Task):
        try:
            result = await task
        except Exception as e:
            # Per-slot resilience: capture error without killing other slots
            error_msg = str(e)
            # Extract a concise message from rate-limit / quota errors
            if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
                error_msg = f"Rate limited — try again shortly or use a different model"
            elif "401" in error_msg or "403" in error_msg:
                error_msg = f"Authentication failed for this provider"
            slot_results[i] = _format_slot_result(spec, {
                "text": f"[Error: {error_msg}]",
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "model_version": None,
                "duration_ms": 0,
                "error": True,
            })
            if on_stage:
                await on_stage("execute_llm", {"status": "error", "detail": {
                    "slot_index": i, "mode": spec["mode"],
                    "error": error_msg,
                }})
            return
        slot_results[i] = _format_slot_result(spec, result)
        if on_stage:
            await on_stage("execute_llm", {"status": "done", "detail": {
                "slot_index": i, "mode": spec["mode"],
                "model": MODEL_MAP.get(spec["mode"], spec["mode"].rsplit("_", 1)[0]),
                "duration_ms": result.get("duration_ms", 0),
            }})

    collect_tasks = [
        asyncio.create_task(_collect_slot(i, spec, tasks[i]))
        for i, spec in enumerate(slot_specs)
    ]
    await asyncio.gather(*collect_tasks)
    assert all(s is not None for s in slot_results), "All slot results must be populated"
    return slot_results


async def _run_direct_call(
    ctx: PreparedContext,
    user_message: str,
    on_stage: StageCallback,
    model: str = "haiku",
) -> dict:
    """Execute direct call path: single LLM call with assembled neurons.

    Uses ctx.system_prompt (already assembled by prepare_context) + user message.
    Returns dict with text, input_tokens, output_tokens, cost_usd, cache tokens.
    """
    assert ctx is not None, "ctx must be populated from prepare_context"
    assert model, "model must be non-empty"

    t0 = time.monotonic()
    result = await llm_chat(
        system_prompt=ctx.system_prompt,
        user_message=user_message,
        max_tokens=4096,
        model=model,
    )
    duration_ms = round((time.monotonic() - t0) * 1000)

    if on_stage:
        await on_stage("execute_llm", {
            "status": "done",
            "detail": {
                "model": model,
                "duration_ms": duration_ms,
                "tokens_in": result.get("input_tokens", 0),
                "tokens_out": result.get("output_tokens", 0),
            },
        })

    return {
        "text": result.get("text", ""),
        "input_tokens": result.get("input_tokens", 0),
        "output_tokens": result.get("output_tokens", 0),
        "cost_usd": result.get("cost_usd", 0),
        "cache_creation_tokens": result.get("cache_creation_tokens", 0),
        "cache_read_tokens": result.get("cache_read_tokens", 0),
        "model_version": result.get("model_version"),
    }


def _populate_query_from_results(
    query: Query,
    slot_results: list[dict],
    all_scored: list[NeuronScoreBreakdown],
    neuron_map: dict[int, Neuron],
    classify_result: dict,
) -> float:
    assert len(slot_results) > 0, "slot_results must be non-empty"
    query.results_json = json.dumps(slot_results)
    if all_scored:
        query.neuron_scores_json = json.dumps(
            _build_neuron_score_dicts(all_scored, neuron_map)
        )
    for slot in slot_results:
        if slot["mode"] == "haiku_neuron" and not query.response_text:
            query.response_text = slot["response"]
            query.execute_input_tokens = slot["input_tokens"]
            query.execute_output_tokens = slot["output_tokens"]
        elif slot["mode"] == "opus_raw" and not query.opus_response_text:
            query.opus_response_text = slot["response"]
            query.opus_input_tokens = slot["input_tokens"]
            query.opus_output_tokens = slot["output_tokens"]
    total_cost = sum(s["cost_usd"] for s in slot_results) + classify_result.get("cost_usd", 0)
    query.cost_usd = total_cost
    for slot in slot_results:
        mv = slot.get("model_version")
        if mv:
            query.model_version = mv
            break
    assert total_cost >= 0, f"total_cost must be non-negative, got {total_cost}"
    return total_cost


def _build_response(
    query: Query,
    ctx: PreparedContext | None,
    needs_neurons: bool,
    all_scored: list[NeuronScoreBreakdown],
    max_top_k: int,
    neuron_map: dict[int, Neuron],
    classify_result: dict,
    slot_results: list[dict],
    total_cost: float,
) -> dict:
    assert total_cost >= 0, f"total_cost must be non-negative, got {total_cost}"

    return {
        "query_id": query.id,
        "intent": ctx.intent if needs_neurons and ctx else None,
        "departments": ctx.departments if ctx else [],
        "role_keys": ctx.role_keys if ctx else [],
        "keywords": ctx.keywords if ctx else [],
        "neurons_activated": min(len(all_scored), max_top_k),
        "neurons_candidates": len(all_scored),
        "neuron_scores": _build_neuron_score_dicts(all_scored, neuron_map),
        "classify_cost": classify_result.get("cost_usd", 0),
        "classify_input_tokens": classify_result["input_tokens"],
        "classify_output_tokens": classify_result["output_tokens"],
        "slots": slot_results,
        "total_cost": total_cost,
        # Pattern #5: per-stage timing + status for the query-prep DAG.
        "stage_telemetry": ctx.stage_telemetry if ctx else [],
    }


async def _update_counters_and_fire(
    db: AsyncSession,
    query: Query,
    slot_results: list[dict],
    classify_result: dict,
    needs_neurons: bool,
    all_scored: list[NeuronScoreBreakdown],
):
    assert isinstance(slot_results, list), "slot_results must be a list"
    assert isinstance(classify_result, dict), "classify_result must be a dict"
    state = await get_system_state(db)
    total_tokens = classify_result["input_tokens"] + classify_result["output_tokens"]
    for slot in slot_results:
        total_tokens += slot["input_tokens"] + slot["output_tokens"]
    state.global_token_counter += total_tokens
    state.total_queries += 1

    if needs_neurons:
        included_k = settings.top_k_neurons
        for idx, score in enumerate(all_scored):
            await record_firing(
                db, score.neuron_id, query.id,
                state.global_token_counter,
                global_query_offset=state.total_queries,
                score=score,
                rank=idx + 1,
                prompt_position=idx if idx < included_k else None,
                was_included=idx < included_k,
            )
            await propagate_activation(db, score.neuron_id, score.combined, query.id)
        cofire_neurons = [s for s in all_scored if s.combined >= settings.min_cofire_score]
        cofire_ids = [s.neuron_id for s in cofire_neurons]
        if len(cofire_ids) >= 2:
            await _batch_update_edges(db, cofire_ids, state.total_queries)
        from app.services.concept_service import cofire_concept_neurons
        await cofire_concept_neurons(db, cofire_ids, state.total_queries)

    await db.commit()


async def _execute_slot(
    db: AsyncSession,
    slot: dict,
    slot_index: int,
    user_message: str,
    ctx: PreparedContext | None,
    on_stage: StageCallback = None,
) -> dict:
    """Execute a single slot with its model configuration.

    Returns SlotResult dict with response, tokens, cost, error info.
    Emits per-slot completion events via on_stage callback.
    """
    assert isinstance(slot, dict), "slot must be a dict"
    assert isinstance(user_message, str) and user_message.strip(), "user_message must be non-empty"

    mode = slot.get("mode", "haiku_neuron")
    token_budget = slot.get("token_budget", settings.token_budget)
    label = slot.get("label")

    # Parse mode: "{model}_{type}" (e.g., "haiku_neuron", "sonnet_raw")
    parts = mode.rsplit("_", 1)
    model_name = parts[0] if parts else "haiku"
    slot_type = parts[1] if len(parts) > 1 else "neuron"
    uses_neurons = slot_type == "neuron"

    start_time = time.monotonic()

    try:
        result_data = await _execute_slot_llm(
            db, user_message, ctx, model_name, uses_neurons, on_stage,
        )

        duration_ms = round((time.monotonic() - start_time) * 1000)

        result = {
            "mode": mode,
            "model": model_name,
            "neurons": uses_neurons,
            "response": result_data["response_text"],
            "input_tokens": result_data["input_tokens"],
            "output_tokens": result_data["output_tokens"],
            "cost_usd": result_data["cost_usd"],
            "cache_creation_tokens": result_data["cache_creation"],
            "cache_read_tokens": result_data["cache_read"],
            "token_budget": token_budget,
            "top_k": ctx.neurons_activated if ctx else 0,
            "label": label or _eval_slot_label(mode, uses_neurons, token_budget),
            "model_version": result_data.get("model_version"),
        }

        if on_stage:
            await on_stage("execute_llm", {"status": "done", "detail": {
                "slot_index": slot_index,
                "mode": mode,
                "model": model_name,
                "duration_ms": duration_ms,
                "output_tokens": result_data["output_tokens"],
            }})

        return result
    except Exception as e:
        duration_ms = round((time.monotonic() - start_time) * 1000)
        error_msg = str(e)[:200] or f"{type(e).__name__}: (no message)"

        if on_stage:
            await on_stage("execute_llm", {"status": "error", "detail": {
                "slot_index": slot_index,
                "mode": mode,
                "error": error_msg,
            }})

        return _build_error_slot_result(
            mode, model_name, uses_neurons, token_budget,
            label, error_msg,
        )


async def _execute_slot_llm(
    db: AsyncSession,
    user_message: str,
    ctx: PreparedContext | None,
    model_name: str,
    uses_neurons: bool,
    on_stage: StageCallback,
) -> dict:
    """Run the LLM call for a single slot. Returns unified result dict.

    Two paths: direct neuron call or raw baseline.
    All paths return actual API-reported token counts (no estimates).
    """
    assert model_name, "model_name must be non-empty"
    assert isinstance(uses_neurons, bool), "uses_neurons must be a bool"

    if uses_neurons and ctx:
        # Direct call path with neurons — uses slot's model, real API tokens
        direct_result = await _run_direct_call(ctx, user_message, on_stage, model=model_name)
        return {
            "response_text": direct_result.get("text", ""),
            "input_tokens": direct_result.get("input_tokens", 0),
            "output_tokens": direct_result.get("output_tokens", 0),
            "cost_usd": direct_result.get("cost_usd", 0.0),
            "cache_creation": direct_result.get("cache_creation_tokens", 0),
            "cache_read": direct_result.get("cache_read_tokens", 0),
            "model_version": direct_result.get("model_version"),
        }

    # Raw path: no neurons, no system prompt — just the user's question.
    # Raw slots are the control group; they get zero domain context from Corvus.
    llm_result = await llm_chat(
        system_prompt="",
        user_message=user_message,
        max_tokens=4096,
        model=model_name,
    )
    return {
        "response_text": llm_result.get("text", ""),
        "input_tokens": llm_result.get("input_tokens", 0),
        "output_tokens": llm_result.get("output_tokens", 0),
        "cost_usd": llm_result.get("cost_usd", 0.0),
        "cache_creation": llm_result.get("cache_creation_tokens", 0),
        "cache_read": llm_result.get("cache_read_tokens", 0),
        "model_version": llm_result.get("model_version"),
    }


def _build_error_slot_result(
    mode: str,
    model_name: str,
    uses_neurons: bool,
    token_budget: int,
    label: str | None,
    error_msg: str,
) -> dict:
    """Build error result dict for a failed slot execution."""
    assert isinstance(mode, str), "mode must be a string"
    assert isinstance(error_msg, str), "error_msg must be a string"

    return {
        "mode": mode,
        "model": model_name,
        "neurons": uses_neurons,
        "response": "",
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "token_budget": token_budget,
        "top_k": 0,
        "label": label or _eval_slot_label(mode, uses_neurons, token_budget),
        "error": True,
        "error_message": error_msg,
    }


def _eval_slot_label(mode: str, uses_neurons: bool, token_budget: int) -> str:
    """Generate a descriptive label for a slot."""
    model_part = mode.split("_")[0].title() if "_" in mode else mode.title()
    neuron_part = "+ Neurons" if uses_neurons else "Raw"
    budget_part = f"@ {token_budget // 1000}K"
    return f"{model_part} {neuron_part} {budget_part}"


async def execute_query(
    db: AsyncSession,
    user_message: str,
    slots: list[dict] | None = None,
    prior_neuron_ids: list[int] | None = None,
    on_stage: StageCallback = None,
) -> dict:
    """Run multi-slot query pipeline.

    Each slot can independently control:
    - Model (haiku, sonnet, opus)
    - Use neurons (yes/no)
    - Token budgets and top_k

    Shared pipeline:
    - Single neuron classify → score → spread (shared across all slots)
    - Each slot executes independently
    """
    # Preconditions (JPL Rule 5)
    assert user_message and user_message.strip(), "user_message must be non-empty"

    # Default to single haiku neuron slot if not specified
    if not slots:
        slots = [{
            "mode": "haiku_neuron",
            "token_budget": settings.token_budget,
            "top_k": settings.top_k_neurons,
        }]

    # Always run neuron pipeline once (classify → score → spread → assemble)
    # Slots will reuse this context
    ctx, classify_result = await _run_neuron_pipeline(
        db, user_message, slots, max(s.get("top_k", settings.top_k_neurons) for s in slots),
        on_stage, prior_neuron_ids=prior_neuron_ids,
    )

    # Determine if neurons are actually needed based on slot composition
    needs_neurons = any(s["mode"] in NEURON_MODES for s in slots)

    # Create query record early (before execution)
    query = _create_query_record(user_message, ctx, needs_neurons=needs_neurons, slot_specs=slots, primary_prompt="", classify_result=classify_result)
    db.add(query)
    await db.flush()

    intent = ctx.intent if ctx else "general_query"
    all_scored = ctx.all_scored if ctx else []
    neuron_map = ctx.neuron_map if ctx else {}

    # Execute each slot in parallel
    slot_tasks = []
    for i, slot in enumerate(slots):
        # Raw slots bypass neuron context; only neuron-enriched slots get it
        # This ensures raw slots are vanilla Claude without any knowledge graph enrichment
        mode = slot.get("mode", "haiku_neuron")
        parts = mode.rsplit("_", 1)
        slot_type = parts[1] if len(parts) > 1 else "neuron"
        uses_neurons = slot_type == "neuron"

        # Pass ctx only if this slot should receive neuron context
        slot_ctx = ctx if uses_neurons else None

        task = _execute_slot(
            db=db,
            slot=slot,
            slot_index=i,
            user_message=user_message,
            ctx=slot_ctx,
            on_stage=on_stage,
        )
        slot_tasks.append(task)

    slot_results = await asyncio.gather(*slot_tasks)
    total_cost = classify_result.get("cost_usd", 0) + sum(s.get("cost_usd", 0) for s in slot_results)

    # Set primary response from first successful slot
    for slot_result in slot_results:
        if slot_result.get("response") and not query.response_text:
            query.response_text = slot_result["response"]
            query.execute_input_tokens = slot_result.get("input_tokens", 0)
            query.execute_output_tokens = slot_result.get("output_tokens", 0)
            query.model_version = slot_result.get("model")
            break

    query.cost_usd = total_cost
    query.results_json = json.dumps(slot_results)
    await _update_counters_and_fire(db, query, slot_results, classify_result, needs_neurons=needs_neurons, all_scored=all_scored)

    # Postcondition (JPL Rule 5)
    assert total_cost >= 0, f"total_cost must be non-negative, got {total_cost}"

    return _build_response(
        query, ctx, needs_neurons=needs_neurons, all_scored=all_scored, max_top_k=len(all_scored),
        neuron_map=neuron_map, classify_result=classify_result, slot_results=slot_results, total_cost=total_cost,
    )


async def _load_candidates_by_ids(
    db: AsyncSession,
    neuron_ids: list[int],
    keywords: list[str],
) -> list[NeuronCandidate]:
    """Load lightweight NeuronCandidate objects for a set of neuron IDs.

    Used when the semantic prefilter has already selected the candidate set,
    so we just need to hydrate the scoring-relevant fields.
    """
    # Preconditions (JPL Power of Ten Rule 5)
    assert all(isinstance(nid, int) and nid > 0 for nid in neuron_ids), \
        "All neuron_ids must be positive integers"

    if not neuron_ids:
        return []

    from sqlalchemy import text

    # Build keyword hit expression
    params: dict = {}
    kw_parts = []
    if keywords:
        for i, kw in enumerate(keywords):
            param_name = f"kw_{i}"
            params[param_name] = f"%{kw.lower()}%"
            kw_parts.append(
                f"(CASE WHEN lower(label) LIKE :{param_name} THEN 1 ELSE 0 END + "
                f"CASE WHEN lower(summary) LIKE :{param_name} THEN 1 ELSE 0 END)"
            )
    kw_expr = " + ".join(kw_parts) if kw_parts else "0"

    # Use ANY(ARRAY[...]) for asyncpg compatibility with large ID lists
    params["id_list"] = list(neuron_ids)
    sql = f"""
        SELECT id, label, summary, department, role_key, avg_utility,
               invocations, created_at_query_count, ({kw_expr}) AS keyword_hits
        FROM neurons
        WHERE id = ANY(:id_list) AND is_active = true
    """
    result = await db.execute(text(sql), params)
    rows = result.all()

    candidates = [
        NeuronCandidate(
            id=r[0], label=r[1], summary=r[2], department=r[3], role_key=r[4],
            avg_utility=r[5] or 0.5, invocations=r[6] or 0,
            created_at_query_count=r[7] or 0, keyword_hits=r[8] or 0,
        )
        for r in rows
    ]

    # Postcondition (JPL Power of Ten Rule 5)
    assert len(candidates) <= len(neuron_ids), \
        f"Output length ({len(candidates)}) must not exceed input length ({len(neuron_ids)})"

    return candidates


async def _batch_update_edges(db: AsyncSession, neuron_ids: list[int], query_offset: int):
    """Batch update co-firing edges for a set of neurons (tiered storage).

    Promoted edges (above threshold) stay in neuron_edges table.
    Weak edges live in JSONB on the neurons table.
    """
    # Precondition (JPL Power of Ten Rule 5)
    assert len(neuron_ids) >= 2, \
        f"_batch_update_edges requires >= 2 neuron_ids, got {len(neuron_ids)}"

    from sqlalchemy import text

    pairs = [(min(a, b), max(a, b))
             for i, a in enumerate(neuron_ids)
             for b in neuron_ids[i + 1:]]

    # Check which pairs already exist in the promoted table
    existing = await _find_promoted_pairs(db, pairs)
    promoted_pairs = [p for p in pairs if p in existing]
    weak_pairs = [p for p in pairs if p not in existing]

    # Update promoted edges in table
    cache_pairs, cache_weights = await _cofire_promoted(
        db, promoted_pairs, query_offset,
    )

    # Update weak edges in JSONB, promoting any that cross threshold
    newly_promoted = await _cofire_weak(db, weak_pairs, query_offset)

    # Update adjacency cache for table-level changes
    from app.services.adjacency_cache import is_adjacency_loaded, update_adjacency_incremental
    all_cache_pairs = cache_pairs + [(s, t) for s, t, _w in newly_promoted]
    all_cache_weights = cache_weights + [w for _s, _t, w in newly_promoted]
    if is_adjacency_loaded() and all_cache_pairs:
        update_adjacency_incremental(
            pairs=all_cache_pairs,
            weights=all_cache_weights,
            edge_types=["pyramidal"] * len(all_cache_pairs),
        )


async def _find_promoted_pairs(
    db: AsyncSession, pairs: list[tuple[int, int]],
) -> set[tuple[int, int]]:
    """Check which (src, tgt) pairs exist in the neuron_edges table."""
    from sqlalchemy import text
    if not pairs:
        return set()
    # Build a VALUES list for batch lookup
    values = ", ".join(f"({s}, {t})" for s, t in pairs)
    result = await db.execute(text(
        f"SELECT source_id, target_id FROM neuron_edges "
        f"WHERE (source_id, target_id) IN ({values})"
    ))
    return {(int(r[0]), int(r[1])) for r in result.all()}


async def _cofire_promoted(
    db: AsyncSession,
    pairs: list[tuple[int, int]],
    qoff: int,
) -> tuple[list[tuple[int, int]], list[float]]:
    """Increment co-fire count for edges already in the promoted table.

    Returns (pairs, weights) for adjacency cache update.
    """
    from sqlalchemy import text
    cache_pairs: list[tuple[int, int]] = []
    cache_weights: list[float] = []
    for src, tgt in pairs:
        result = await db.execute(text(
            "UPDATE neuron_edges "
            "SET co_fire_count = co_fire_count + 1, "
            "    weight = LEAST(1.0, (co_fire_count + 1) / 20.0), "
            "    last_updated_query = :qoff, "
            "    source = CASE WHEN source = 'bootstrap' THEN 'organic' ELSE source END, "
            "    last_adjusted = now() "
            "WHERE source_id = :src AND target_id = :tgt "
            "RETURNING weight"
        ), {"src": src, "tgt": tgt, "qoff": qoff})
        row = result.one_or_none()
        if row:
            cache_pairs.append((src, tgt))
            cache_weights.append(float(row[0]))
    return cache_pairs, cache_weights


async def _cofire_weak(
    db: AsyncSession,
    pairs: list[tuple[int, int]],
    qoff: int,
) -> list[tuple[int, int, float]]:
    """Increment co-fire for weak edges in JSONB, creating new ones as needed.

    Returns list of (src, tgt, weight) for edges that were promoted to table.
    """
    from app.services.edge_tier import (
        get_weak_edge, upsert_weak_edge, maybe_promote,
    )
    promoted: list[tuple[int, int, float]] = []
    for src, tgt in pairs:
        entry = await get_weak_edge(db, src, tgt)
        if entry is not None:
            new_c = entry.get("c", 0) + 1
            new_w = min(1.0, (new_c + 1) / 20.0)
        else:
            new_c = 1
            new_w = min(1.0, 2 / 20.0)
        data = {
            "w": new_w, "t": "pyramidal", "c": new_c,
            "s": "organic", "q": qoff,
        }
        await upsert_weak_edge(db, src, tgt, data)
        if await maybe_promote(db, src, tgt, new_w, new_c):
            promoted.append((src, tgt, new_w))
    return promoted
