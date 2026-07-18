"""Full pipeline orchestration: classify → score → assemble → execute → record."""

import asyncio
import json
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Callable, Awaitable

# Optional callback for streaming pipeline progress
StageCallback = Callable[[str, dict], Awaitable[None]] | None

from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.config import settings
from app.models import Neuron, Query, NeuronEdge, CitationHopSession
from app.services.llm_provider import llm_chat, MODEL_REGISTRY, effort_var
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
from app.services.citation_hopping import HopMap, mint_hop_map, serialize_hop_map
from app.services.tier_routing import TierDecision, decide_tier_escalation, escalated_model
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
    candidates_considered: int = 0
    neurons_delivered: int = 0
    estimated_memory_tokens: int = 0
    memory_context_chars: int = 0
    memory_context_utf8_bytes: int = 0
    memory_context_text: str = ""
    memory_token_budget: int = 0
    assembly_stop_reason: str = "no_candidates"
    redundancy_suppressed: int = 0
    token_estimator_version: str = ""
    oversized_first_neuron: bool = False
    memory_representations: dict[int, str] = field(default_factory=dict)
    recall_latency_ms: float = 0.0
    neuron_map: dict[int, Neuron] = field(default_factory=dict)
    all_scored: list[NeuronScoreBreakdown] = field(default_factory=list)
    classify_cost_usd: float = 0.0
    classify_input_tokens: int = 0
    classify_output_tokens: int = 0
    # Pattern #5: typed pipeline DAG telemetry — per-stage timing + status.
    # Populated when prepare_context runs through the pipeline runner;
    # None for structural fast-path results (no pipeline ran).
    stage_telemetry: list[dict] = field(default_factory=list)
    # Frequency-hopped citation grounding: the secret per-query key<->neuron
    # map used to render tokens and verify citations. None when disabled.
    # Never serialise to the client — it is secret to the analysis layer.
    hop_map: HopMap | None = None
    # Resolved eCFR regulations (ResolvedRegulation) surfaced this query — used
    # to label engram hop citations for the frontend. Empty when none resolved.
    resolved_regulations: list = field(default_factory=list)
    # Full post-spread/post-redundancy activation list for telemetry. Public
    # all_scored remains the delivered slice for attribution compatibility.
    activated_scores: list[NeuronScoreBreakdown] = field(default_factory=list)


async def _embed_query_async(user_message: str):
    import concurrent.futures
    loop = asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        from app.services.embedding_service import embed_text
        return await loop.run_in_executor(pool, embed_text, user_message)


def _tally_neighbor_votes(
    neuron_hits: list[tuple[int, float]],
    tag_rows: list[tuple[int, str | None, str | None]],
    max_tags: int = 3,
    min_share: float = 0.2,
) -> tuple[list[str], list[str]]:
    """Similarity-weighted vote: neighbors' region/role tags ARE the prediction.

    A tag wins when it carries at least min_share of the total similarity
    weight; at most max_tags per dimension, strongest first.
    """
    sim_by_id = dict(neuron_hits)
    region_votes: dict[str, float] = {}
    role_votes: dict[str, float] = {}
    total_weight = 0.0
    for nid, region, role_key in tag_rows:
        weight = sim_by_id.get(nid, 0.0)
        total_weight += weight
        if region:
            region_votes[region] = region_votes.get(region, 0.0) + weight
        if role_key:
            role_votes[role_key] = role_votes.get(role_key, 0.0) + weight
    if total_weight <= 0:
        return [], []

    def _winners(votes: dict[str, float]) -> list[str]:
        ranked = sorted(votes.items(), key=lambda kv: kv[1], reverse=True)
        return [tag for tag, w in ranked[:max_tags] if w / total_weight >= min_share]

    return _winners(region_votes), _winners(role_votes)


async def _neighbor_vote_classify(
    db: AsyncSession, query_embedding, k: int | None = None,
) -> tuple[list[str], list[str], float]:
    """Predict region/role tags from top-k semantic neighbors — no LLM.

    The graph classifies itself: the query lands somewhere in embedding
    space and its nearest neurons' tags are the classification (recognition,
    not deliberation). Returns (regions, role_keys, top_similarity); the
    caller uses top_similarity to decide whether recognition was confident
    enough or the LLM classifier should be consulted (adaptive escalation).
    """
    from sqlalchemy import text as sa_text
    from app.services.semantic_prefilter import semantic_prefilter

    k = k or settings.cheap_recall_neighbor_k
    results = await semantic_prefilter(db, query_embedding, top_n_override=k * 3)
    neuron_hits = [(eid, sim) for eid, etype, sim in results if etype == "neuron"][:k]
    if not neuron_hits:
        return [], [], 0.0

    rows = await db.execute(
        sa_text("SELECT id, department, role_key FROM neurons WHERE id = ANY(:ids)"),
        {"ids": [eid for eid, _sim in neuron_hits]},
    )
    regions, role_keys = _tally_neighbor_votes(neuron_hits, list(rows.all()))
    return regions, role_keys, neuron_hits[0][1]


async def _run_hybrid_lanes(
    db: AsyncSession, user_message: str | None,
) -> list[dict[int, float]]:
    """Hybrid-recall lanes (LLM-free, indexed SQL): keyword tsvector + entity
    match retrieve their own candidates so a named-thing memory can enter
    the pool even when it loses the cosine race. Fused by RRF downstream."""
    if not user_message or not (
            settings.keyword_lane_enabled or settings.entity_lane_enabled):
        return []
    from app.services.recall_lanes import (
        entity_lane, extract_query_entities, keyword_lane,
    )
    lanes: list[dict[int, float]] = []
    if settings.keyword_lane_enabled:
        lanes.append(
            await keyword_lane(db, user_message, settings.recall_lane_top_n))
    if settings.entity_lane_enabled:
        lanes.append(await entity_lane(
            db, extract_query_entities(user_message), settings.recall_lane_top_n))
    return [lane for lane in lanes if lane]


async def _select_and_score_candidates(
    db: AsyncSession,
    query_embedding,
    effective_pool: int,
    keywords: list[str],
    departments: list[str],
    role_keys: list[str],
    total_queries: int,
    requester=None,
    user_message: str | None = None,
) -> tuple[list[NeuronScoreBreakdown], list[NeuronScoreBreakdown]]:
    """Score neuron and engram candidates.  Returns (scored_neurons, scored_engrams).

    The requester's ACL scope filters candidates at load time (the semantic
    prefilter matrix is region-blind, so enforcement happens in SQL here).
    """
    assert isinstance(effective_pool, int) and effective_pool > 0, \
        "effective_pool must be a positive integer"

    scored_engrams: list[NeuronScoreBreakdown] = []
    semantic_results: list[tuple[int, str, float]] | None = None

    if settings.semantic_prefilter_enabled and query_embedding is not None:
        from app.services.semantic_prefilter import semantic_prefilter
        semantic_results = await semantic_prefilter(db, query_embedding, top_n_override=effective_pool)

    extra_lanes = await _run_hybrid_lanes(db, user_message)

    if semantic_results or extra_lanes:
        # Partition into neurons and engrams
        semantic_results = semantic_results or []
        neuron_sims = {eid: sim for eid, etype, sim in semantic_results if etype == "neuron"}
        engram_sims = {eid: sim for eid, etype, sim in semantic_results if etype == "engram"}

        # Score neurons: union of embedding-lane and lexical/entity-lane hits
        sem_ids = list(neuron_sims.keys())
        lane_only_ids = [nid for lane in extra_lanes for nid in lane
                         if nid not in neuron_sims]
        all_ids = sem_ids + list(dict.fromkeys(lane_only_ids))
        candidates = await _load_candidates_by_ids(db, all_ids, keywords, requester) if all_ids else []
        scored = await score_candidates(
            db, candidates, total_queries, keywords,
            departments, role_keys,
            query_embedding=query_embedding,
            precomputed_similarities=neuron_sims,
            extra_lanes=extra_lanes or None,
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
        candidates = await get_neurons_by_filter(db, departments, role_keys, keywords, requester)
        if not candidates:
            candidates = await get_neurons_by_filter(db, requester=requester)
        scored = await score_candidates(
            db, candidates, total_queries, keywords, departments, role_keys,
            query_embedding=query_embedding,
        )

    if settings.token_bounded_assembly_enabled:
        # Hybrid lane union can exceed the semantic lane's top_n. Rank first,
        # then enforce one independently observable activation candidate pool.
        scored = scored[:effective_pool]
    assert isinstance(scored, list), "scored must be a list"
    return scored, scored_engrams


async def _apply_inhibition_and_boost(
    db: AsyncSession,
    scored: list[NeuronScoreBreakdown],
    effective_top_k: int,
    project_path: str | None,
) -> tuple[list[NeuronScoreBreakdown], int, int]:
    redundancy_suppressed = 0
    if settings.token_bounded_assembly_enabled:
        if settings.inhibition_enabled:
            from app.services.inhibitory_service import apply_token_bounded_redundancy
            all_scored, redundancy_suppressed = await apply_token_bounded_redundancy(db, scored)
        else:
            all_scored = scored
        # Count is now a caller/safety cap, never an inferred survivor target.
        effective_top_k = min(effective_top_k, settings.memory_max_delivered_neurons)
    elif settings.inhibition_enabled:
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
    return all_scored, effective_top_k, redundancy_suppressed


async def _load_neuron_map(
    db: AsyncSession, neuron_ids: list[int], requester=None,
) -> dict[int, Neuron]:
    """Hydrate full neurons for assembly, enforcing requester ACL (defense
    in depth — candidates and promotions were already filtered upstream)."""
    neuron_map: dict[int, Neuron] = {}
    if neuron_ids:
        result = await db.execute(select(Neuron).where(Neuron.id.in_(neuron_ids)))
        neurons = list(result.scalars().all())
        if requester is not None:
            from app.services.region_policy import (
                get_region_policies, requester_can_see, restricted_regions,
            )
            restricted = set(restricted_regions(await get_region_policies(db)))
            neurons = [
                n for n in neurons
                if requester_can_see(requester, n.department, n.visibility, restricted)
            ]
        for neuron in neurons:
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
        "fallback": sum(1 for r in resolved if r.source == "fallback_summary"),
    }})
    return resolved


async def prepare_context(
    db: AsyncSession, user_message: str, token_budget: int | None = None,
    top_k: int | None = None, project_path: str | None = None,
    on_stage: StageCallback = None, prior_neuron_ids: list[int] | None = None,
    recall_mode: str | None = None, requester=None,
    spread_hops: int | None = None, spread_floor: float | None = None,
) -> PreparedContext:
    """Run the classify → score → spread → inhibit → resolve → assemble pipeline.

    Pattern #5: stages are composed via the typed pipeline runner; each stage
    emits timing + telemetry and any stage failure hard-fails with a
    `PipelineStageError` carrying the stage name.

    recall_mode selects the classify stage (only "cheap" is registered — the LLM
    classify was removed); None falls back to settings.recall_mode. requester
    (RequesterContext) bounds recall to the requester's regions; None = unrestricted.
    """
    assert isinstance(user_message, str) and len(user_message.strip()) > 0, \
        "user_message must be a non-empty string"

    from app.services.pipeline import PipelineContext, run_pipeline
    from app.services.pipeline.state import PipelineState
    from app.services.pipeline.stages import build_default_pipeline

    started_at = time.monotonic()
    effective_recall_mode = recall_mode or settings.recall_mode
    if settings.token_bounded_assembly_enabled:
        resolved_top_k = (
            top_k if top_k is not None
            else min(settings.memory_candidate_limit, settings.memory_max_delivered_neurons)
        )
        resolved_pool = settings.memory_candidate_limit
    else:
        resolved_top_k = top_k if top_k is not None else settings.top_k_neurons
        resolved_pool = settings.semantic_prefilter_top_n
    initial_state = PipelineState(
        user_message=user_message,
        effective_top_k=resolved_top_k,
        effective_pool=resolved_pool,
        effective_budget=token_budget if token_budget is not None else settings.token_budget,
        project_path=project_path,
        prior_neuron_ids=prior_neuron_ids,
        requester=requester,
        spread_hops=spread_hops,
        spread_floor=spread_floor,
    )
    pipeline_ctx = PipelineContext(db=db, on_stage=on_stage)

    final = await run_pipeline(
        build_default_pipeline(effective_recall_mode), initial_state, pipeline_ctx,
    )

    # Structural resolve short-circuit returns the already-built PreparedContext.
    if isinstance(final, PreparedContext):
        final.stage_telemetry = pipeline_ctx.telemetry_json()
        final.recall_latency_ms = round((time.monotonic() - started_at) * 1000, 1)
        return final

    state: PipelineState = final
    assert isinstance(state, PipelineState), "pipeline must return a PipelineState when not short-circuiting"

    if state.project_path and getattr(settings, 'project_cache_enabled', False):
        from app.services.project_cache import record_project_firings
        await record_project_firings(db, state.project_path, state.top_slice)

    result = _state_to_prepared_context(state, pipeline_ctx)
    result.recall_latency_ms = round((time.monotonic() - started_at) * 1000, 1)
    return result


def _state_to_prepared_context(state, pipeline_ctx) -> PreparedContext:
    """Project the final PipelineState into the public PreparedContext."""
    result = PreparedContext(
        system_prompt=state.system_prompt, intent=state.intent,
        departments=state.departments, role_keys=state.role_keys, keywords=state.keywords,
        neuron_scores=_build_neuron_score_dicts(state.top_slice, state.neuron_map),
        neurons_activated=(
            state.neurons_activated or len(state.all_scored)
            if settings.token_bounded_assembly_enabled
            else min(len(state.all_scored), state.effective_top_k)
        ),
        candidates_considered=state.candidates_considered,
        neurons_delivered=state.neurons_delivered or len(state.top_slice),
        estimated_memory_tokens=state.estimated_memory_tokens,
        memory_context_chars=state.memory_context_chars,
        memory_context_utf8_bytes=state.memory_context_utf8_bytes,
        memory_context_text=state.memory_context_text,
        memory_token_budget=state.memory_token_budget,
        assembly_stop_reason=state.assembly_stop_reason,
        redundancy_suppressed=state.redundancy_suppressed,
        token_estimator_version=state.token_estimator_version,
        oversized_first_neuron=state.oversized_first_neuron,
        memory_representations=state.memory_representations,
        neuron_map=state.neuron_map, all_scored=state.top_slice,
        classify_cost_usd=state.classify_result.get("cost_usd", 0),
        classify_input_tokens=state.classify_result.get("input_tokens", 0),
        classify_output_tokens=state.classify_result.get("output_tokens", 0),
        stage_telemetry=pipeline_ctx.telemetry_json(),
        hop_map=state.hop_map,
        resolved_regulations=state.resolved_regulations,
        activated_scores=state.all_scored,
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
    requester=None,
) -> tuple[list[NeuronScoreBreakdown], dict[int, Neuron], str, HopMap | None, dict]:
    """Select top-k neurons, load their data, and assemble the system prompt.

    When ``settings.citation_hopping_enabled`` a per-query frequency-hop key is
    minted for each selected neuron and returned as the 4th tuple element (the
    secret map the exit layer verifies against); None otherwise."""
    if settings.token_bounded_assembly_enabled:
        # The candidate/safety cap bounds hydration; tokens decide delivery.
        candidate_slice = all_scored[:effective_top_k]
        neuron_map = await _load_neuron_map(
            db, [s.neuron_id for s in candidate_slice], requester)
        candidate_slice = [s for s in candidate_slice if s.neuron_id in neuron_map]

        from app.services.memory_assembly import (
            assemble_memory_packet, estimate_memory_tokens, render_memory_entry,
        )
        if settings.citation_hopping_enabled:
            placeholder = (
                settings.citation_hop_prefix
                + ("0" * settings.citation_hop_hex_width)
            )
            placeholder_labels = {s.neuron_id: placeholder for s in candidate_slice}
        else:
            placeholder_labels = {
                s.neuron_id: str(i + 1) for i, s in enumerate(candidate_slice)
            }
        memory_budget = min(settings.memory_context_token_budget, effective_budget)
        packet = assemble_memory_packet(
            candidate_slice,
            neuron_map,
            memory_budget,
            min(effective_top_k, settings.memory_max_delivered_neurons),
            citation_labels=placeholder_labels,
            estimator=settings.memory_token_estimator,
        )
        top_slice = packet.scores
        neuron_map = {s.neuron_id: neuron_map[s.neuron_id] for s in top_slice}
    else:
        if settings.hierarchy_selection_enabled:
            top_slice = await select_with_hierarchy(db, all_scored, effective_top_k)
        else:
            top_slice = all_scored[:effective_top_k]
        neuron_map = await _load_neuron_map(db, [s.neuron_id for s in top_slice], requester)
        top_slice = [s for s in top_slice if s.neuron_id in neuron_map]

    prior_neuron_map: dict[int, Neuron] | None = None
    if prior_neuron_ids:
        missing_ids = [nid for nid in prior_neuron_ids if nid not in neuron_map]
        if missing_ids:
            extra = await _load_neuron_map(db, missing_ids)
            prior_neuron_map = {**neuron_map, **extra}
        else:
            prior_neuron_map = neuron_map

    hop_map: HopMap | None = None
    citation_tokens: dict[int, str] | None = None
    engram_citation_tokens: dict[int, str] | None = None
    if settings.citation_hopping_enabled:
        engram_ids = [r.engram_id for r in resolved_regulations] if resolved_regulations else []
        hop_map = mint_hop_map([s.neuron_id for s in top_slice], engram_ids)
        citation_tokens = hop_map.token_by_neuron
        engram_citation_tokens = hop_map.token_by_engram

    memory_entries: list[str] | None = None
    assembly_telemetry = {
        "neurons_delivered": len(top_slice),
        "estimated_memory_tokens": 0,
        "memory_context_chars": 0,
        "memory_context_utf8_bytes": 0,
        "memory_context_text": "",
        "memory_token_budget": 0,
        "assembly_stop_reason": "legacy_count_limit",
        "oversized_first_neuron": False,
        "token_estimator_version": "",
        "memory_representations": {},
    }
    if settings.token_bounded_assembly_enabled:
        labels = citation_tokens or {
            s.neuron_id: str(i + 1) for i, s in enumerate(top_slice)
        }
        memory_entries = [
            render_memory_entry(
                score, neuron_map[score.neuron_id], labels.get(score.neuron_id),
                packet.representations[score.neuron_id],
            )
            for score in top_slice
        ]
        memory_text = "\n\n".join(memory_entries)
        assembly_telemetry = {
            "neurons_delivered": len(top_slice),
            "estimated_memory_tokens": estimate_memory_tokens(
                memory_text, settings.memory_token_estimator),
            "memory_context_chars": len(memory_text),
            "memory_context_utf8_bytes": len(memory_text.encode("utf-8")),
            "memory_context_text": memory_text,
            "memory_token_budget": packet.budget,
            "assembly_stop_reason": packet.stop_reason,
            "oversized_first_neuron": packet.oversized_first_neuron,
            "token_estimator_version": packet.estimator_version,
            "memory_representations": dict(packet.representations),
        }

    system_prompt = assemble_prompt(
        intent, top_slice, neuron_map, budget_tokens=effective_budget,
        prior_neuron_ids=prior_neuron_ids, prior_neuron_map=prior_neuron_map,
        resolved_regulations=resolved_regulations,
        citation_tokens=citation_tokens,
        engram_citation_tokens=engram_citation_tokens,
        memory_entries=memory_entries,
    )
    return top_slice, neuron_map, system_prompt, hop_map, assembly_telemetry


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


# Stage name where per-spread-config groups diverge: everything before it
# (structural resolve, classify, prefilter, scoring, continuity boost) is
# identical across groups and runs ONCE; spread onward differs per config.
_PIPELINE_FORK_STAGE = "spread_activation"


async def _prepare_contexts_forked(
    db: AsyncSession,
    user_message: str,
    group_params: list[dict],
    on_stage: StageCallback,
    prior_neuron_ids: list[int] | None,
) -> list[PreparedContext]:
    """Run the recall pipeline with a shared prefix and per-group suffixes.

    classify -> prefilter -> score -> continuity run once; each group then
    gets its own spread -> inhibit -> resolve -> assemble pass over a DEEP
    COPY of the scored list (post-fork stages mutate score objects in
    place: spread_boost, combined). Stage events stream only for the first
    group. Returns one PreparedContext per group, same order.
    """
    import copy

    from app.services.pipeline import PipelineContext, run_pipeline
    from app.services.pipeline.state import PipelineState
    from app.services.pipeline.stages import build_default_pipeline

    started_at = time.monotonic()
    assert group_params, "group_params must be non-empty"
    stages = build_default_pipeline(settings.recall_mode)
    split = next(i for i, st in enumerate(stages) if st.name == _PIPELINE_FORK_STAGE)
    prefix, suffix = stages[:split], stages[split:]

    initial = PipelineState(
        user_message=user_message,
        effective_top_k=max(g["top_k"] for g in group_params),
        effective_pool=(
            settings.memory_candidate_limit
            if settings.token_bounded_assembly_enabled
            else settings.semantic_prefilter_top_n
        ),
        effective_budget=max(g["budget"] for g in group_params),
        prior_neuron_ids=prior_neuron_ids,
    )
    shared_pctx = PipelineContext(db=db, on_stage=on_stage)
    shared = await run_pipeline(prefix, initial, shared_pctx)
    if isinstance(shared, PreparedContext):
        # Structural resolve short-circuit: one answer context for everyone.
        shared.stage_telemetry = shared_pctx.telemetry_json()
        shared.recall_latency_ms = round((time.monotonic() - started_at) * 1000, 1)
        return [shared for _ in group_params]

    results: list[PreparedContext] = []
    for i, g in enumerate(group_params):
        st = copy.copy(shared)
        st.scored = copy.deepcopy(shared.scored)
        st.scored_engrams = copy.deepcopy(shared.scored_engrams)
        st.effective_top_k = g["top_k"]
        st.effective_budget = g["budget"]
        st.spread_hops = g.get("spread_hops")
        st.spread_floor = g.get("spread_floor")
        group_pctx = PipelineContext(
            db=db,
            on_stage=on_stage if i == 0 else None,
            telemetry=list(shared_pctx.telemetry),
        )
        final = await run_pipeline(suffix, st, group_pctx)
        if isinstance(final, PreparedContext):
            final.stage_telemetry = group_pctx.telemetry_json()
            final.recall_latency_ms = round((time.monotonic() - started_at) * 1000, 1)
            results.append(final)
        else:
            prepared = _state_to_prepared_context(final, group_pctx)
            prepared.recall_latency_ms = round((time.monotonic() - started_at) * 1000, 1)
            results.append(prepared)
    return results


def _slot_spread_cfg(slot: dict) -> tuple:
    """Context-shaping slot config; exact keys must not share a packet."""
    assert isinstance(slot, dict), "slot must be a dict"
    requested_top_k = slot.get("top_k")
    if requested_top_k is None:
        requested_top_k = (
            min(settings.memory_candidate_limit, settings.memory_max_delivered_neurons)
            if settings.token_bounded_assembly_enabled
            else settings.top_k_neurons
        )
    budget = slot.get("token_budget")
    if budget is None:
        budget = settings.token_budget
    return (
        slot.get("spread_hops"), slot.get("spread_floor"),
        requested_top_k, budget,
    )


async def _prepare_slot_contexts(
    db: AsyncSession,
    user_message: str,
    slots: list[dict],
    on_stage: StageCallback,
    prior_neuron_ids: list[int] | None,
) -> tuple[dict[tuple, PreparedContext | None], dict]:
    """One context prep per distinct context-shaping slot config.

    Slots sharing spread, explicit top-k, and budget share a PreparedContext. The
    pipeline prefix (classify -> prefilter -> score) runs ONCE regardless of
    group count; distinct configs fork at the spread stage and get their own
    spread -> assemble suffix, so the packed context differs where the knobs
    differ and nowhere else. Stage events stream only for the first group.
    """
    assert isinstance(slots, list) and slots, "slots must be non-empty"
    neuron_slots = [s for s in slots if s.get("mode", "") in NEURON_MODES]
    totals = {"classification": {}, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0}
    if not neuron_slots:
        return {}, totals
    group_keys: list[tuple] = []
    for s in neuron_slots:
        key = _slot_spread_cfg(s)
        if key not in group_keys:
            group_keys.append(key)
    group_params = []
    for key in group_keys:
        group_params.append({
            "top_k": key[2],
            "budget": key[3],
            "spread_hops": key[0],
            "spread_floor": key[1],
        })
    ctxs = await _prepare_contexts_forked(
        db, user_message, group_params, on_stage, prior_neuron_ids,
    )
    ctx_by_cfg: dict[tuple, PreparedContext | None] = dict(zip(group_keys, ctxs))
    first = ctxs[0]
    totals["input_tokens"] = first.classify_input_tokens
    totals["output_tokens"] = first.classify_output_tokens
    totals["cost_usd"] = first.classify_cost_usd
    return ctx_by_cfg, totals


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


# Drift-gated recall (persisted sessions): the freshly-packed context block
# is only re-sent when it materially differs from the session's ACTIVE block.
# The gate metric is packed-set overlap — |fresh ∩ active| / |fresh| — NOT
# question similarity: bare follow-up questions are anaphoric ("who signs off
# on that?") and embed nowhere near their topic, so question-cosine cannot
# separate follow-ups from drift (measured 2026-07: same-topic 0.16-0.77 vs
# drift 0.06-0.19 — overlapping ranges). Overlap compares what recall
# ACTUALLY produced, so reuse never changes what the model grounds on.
# State is in-memory and bounded — losing it (restart/reload) merely forces
# one extra repack, which is always safe.
_session_ctx_cache: OrderedDict = OrderedDict()  # session_id -> {"packed_ids", "ctx"}
_SESSION_CTX_MAX = 500


def _packed_ids(ctx: PreparedContext) -> frozenset:
    return frozenset(s.neuron_id for s in getattr(ctx, "all_scored", []) or [])


def _remember_session_context(session_id: str, ctx: PreparedContext) -> None:
    """Store/refresh a session's active context; evict oldest beyond the cap."""
    assert session_id, "session_id must be non-empty"
    assert ctx is not None, "ctx must not be None"
    _session_ctx_cache[session_id] = {"packed_ids": _packed_ids(ctx), "ctx": ctx}
    _session_ctx_cache.move_to_end(session_id)
    while len(_session_ctx_cache) > _SESSION_CTX_MAX:
        _session_ctx_cache.popitem(last=False)


def _apply_drift_gate(
    session_spec: dict | None, slots: list[dict],
    fresh_ctx: PreparedContext | None,
) -> tuple[PreparedContext | None, float | None]:
    """Decide whether this turn can reuse the session's active context block.

    Returns (reused_ctx, overlap). Reuse fires only when the fresh pack is a
    near-duplicate of the active one (overlap >= chat_context_reuse_overlap,
    conservative by default) — the block is already in the session transcript
    (prompt-cached), so only the question is sent, and grounding guards grade
    against the SAME stored ctx the model is citing. Everything else repacks:
    first turn, real drift, explicit refresh, gate off, multi-slot compare,
    state lost. Repacking is always safe — it is today's behavior.
    """
    if session_spec is None or fresh_ctx is None:
        return None, None
    if not settings.chat_context_drift_gate or len(slots) != 1:
        return None, None
    if not session_spec.get("resume") or session_spec.get("refresh"):
        return None, None
    state = _session_ctx_cache.get(session_spec.get("session_id", ""))
    if not state:
        return None, None
    fresh_ids = _packed_ids(fresh_ctx)
    if not fresh_ids:
        return None, None
    overlap = len(fresh_ids & state["packed_ids"]) / len(fresh_ids)
    if overlap >= settings.chat_context_reuse_overlap:
        session_spec["reuse_context"] = True
        return state["ctx"], overlap
    return None, overlap


def _update_session_context(
    session_spec: dict, slot_results: list[dict],
    active_ctx: PreparedContext | None,
) -> None:
    """Re-key the active context to the session id the CLI actually returned
    (resume can fork) and remember it for the next turn's gate."""
    if active_ctx is None:
        return
    returned = (slot_results[0] or {}).get("llm_session_id") if slot_results else None
    sid = returned or session_spec.get("session_id")
    if sid:
        _remember_session_context(sid, active_ctx)


# Stable system prompt for persisted hero-chat sessions. MUST NOT vary per
# turn or per query: it is the first block of the prompt-cache prefix, and any
# change re-creates the whole cache instead of reading it at 0.1x price. The
# per-turn packed context therefore travels INSIDE the user message.
_CHAT_SESSION_PREAMBLE = (
    "You are a knowledge assistant grounded in a curated organizational "
    "knowledge graph. User messages may begin with a [Knowledge context] "
    "block retrieved for the current question. Ground your answers in the "
    "MOST RECENT [Knowledge context] block in the conversation and follow "
    "the citation-key instructions it contains. A message without a new "
    "block continues under the most recent one; when blocks disagree, "
    "always prefer the most recent."
)


def _session_call_payload(
    ctx: PreparedContext | None, user_message: str, include_context: bool = True,
) -> tuple[str, str]:
    """(system_prompt, user_message) for a persisted-session call.

    The system prompt is the static preamble (cache-stable across turns); the
    freshly-packed neuron context rides in the user message, so the entire
    conversation prefix — preamble + every prior turn — stays cacheable.
    include_context=False (drift-gate reuse) sends only the question: the
    active block already sits in the session transcript.
    """
    assert isinstance(user_message, str) and user_message.strip(), \
        "user_message must be non-empty"
    if ctx is None or not getattr(ctx, "system_prompt", ""):
        return "", user_message
    if not include_context:
        return _CHAT_SESSION_PREAMBLE, user_message
    combined = (
        f"[Knowledge context for this question]\n{ctx.system_prompt}\n\n"
        f"[Question]\n{user_message}"
    )
    return _CHAT_SESSION_PREAMBLE, combined


async def _run_direct_call(
    ctx: PreparedContext,
    user_message: str,
    on_stage: StageCallback,
    model: str = "haiku",
    session_spec: dict | None = None,
) -> dict:
    """Execute direct call path: single LLM call with assembled neurons.

    Uses ctx.system_prompt (already assembled by prepare_context) + user message.
    With session_spec ({"session_id", "resume"}), the call runs inside a
    persisted CLI session: static system preamble, packed context in the user
    message, conversation prefix served from the prompt cache. A failed resume
    (expired/missing session) falls back once to a fresh session — the answer
    still grounds in this turn's freshly-packed context; only cross-turn
    conversational memory is lost.
    Returns dict with text, input_tokens, output_tokens, cost_usd, cache tokens.
    """
    assert ctx is not None, "ctx must be populated from prepare_context"
    assert model, "model must be non-empty"

    t0 = time.monotonic()
    if session_spec:
        include_context = not session_spec.get("reuse_context")
        sys_prompt, message = _session_call_payload(ctx, user_message, include_context)
        try:
            result = await llm_chat(
                system_prompt=sys_prompt, user_message=message,
                max_tokens=4096, model=model, session=session_spec,
            )
        except AssertionError:
            if not session_spec.get("resume"):
                raise
            # Fresh session has no transcript — a reuse-turn fallback must
            # carry the full context block or the answer would be ungrounded.
            sys_prompt, message = _session_call_payload(ctx, user_message, True)
            fresh = {"session_id": str(uuid.uuid4()), "resume": False}
            result = await llm_chat(
                system_prompt=sys_prompt, user_message=message,
                max_tokens=4096, model=model, session=fresh,
            )
    else:
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
                "tokens_in": (
                    result.get("input_tokens", 0)
                    + result.get("cache_creation_tokens", 0)
                    + result.get("cache_read_tokens", 0)
                ),
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
        "session_id": result.get("session_id"),
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
            query.execute_input_tokens = slot.get(
                "observed_total_input_tokens",
                slot["input_tokens"] + slot.get("cache_creation_tokens", 0)
                + slot.get("cache_read_tokens", 0),
            )
            query.execute_output_tokens = slot["output_tokens"]
        elif slot["mode"] == "opus_raw" and not query.opus_response_text:
            query.opus_response_text = slot["response"]
            query.opus_input_tokens = slot.get(
                "observed_total_input_tokens",
                slot["input_tokens"] + slot.get("cache_creation_tokens", 0)
                + slot.get("cache_read_tokens", 0),
            )
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


def _build_citation_map(ctx: PreparedContext | None, neuron_map: dict[int, Neuron]) -> dict:
    """Token -> source descriptor so the frontend can render frequency-hop
    citations as clean numbered superscripts (neuron label or CFR ref). Empty
    when hopping is off — the frontend then falls back to numeric [N] citations.
    This map is safe to expose: it is secret to the *LLM*, not to the client."""
    if ctx is None or getattr(ctx, "hop_map", None) is None:
        return {}
    hop_map = ctx.hop_map
    cmap: dict[str, dict] = {}
    for token, nid in hop_map.neuron_by_token.items():
        neuron = neuron_map.get(nid)
        cmap[token] = {"kind": "neuron", "id": nid, "label": neuron.label if neuron else f"Neuron {nid}"}
    cfr_by_engram = {r.engram_id: r.cfr_ref for r in (ctx.resolved_regulations or [])}
    for token, eid in hop_map.engram_by_token.items():
        cmap[token] = {"kind": "engram", "id": eid, "label": cfr_by_engram.get(eid, f"Engram {eid}")}
    return cmap


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

    observed_total_model_input = sum(
        s.get("observed_total_input_tokens", (
            s.get("input_tokens", 0)
            + s.get("cache_creation_tokens", 0)
            + s.get("cache_read_tokens", 0)
        ))
        for s in slot_results
    )
    return {
        "query_id": query.id,
        "intent": ctx.intent if needs_neurons and ctx else None,
        "departments": ctx.departments if ctx else [],
        "role_keys": ctx.role_keys if ctx else [],
        "keywords": ctx.keywords if ctx else [],
        "neurons_activated": ctx.neurons_activated if ctx else 0,
        "neurons_candidates": ctx.candidates_considered if ctx else 0,
        "candidates_considered": ctx.candidates_considered if ctx else 0,
        "neurons_delivered": ctx.neurons_delivered if ctx else 0,
        "estimated_memory_tokens": ctx.estimated_memory_tokens if ctx else 0,
        "memory_context_chars": ctx.memory_context_chars if ctx else 0,
        "memory_context_utf8_bytes": ctx.memory_context_utf8_bytes if ctx else 0,
        "memory_token_budget": ctx.memory_token_budget if ctx else 0,
        "assembly_stop_reason": ctx.assembly_stop_reason if ctx else "no_candidates",
        "redundancy_suppressed": ctx.redundancy_suppressed if ctx else 0,
        "token_estimator_version": ctx.token_estimator_version if ctx else "",
        "oversized_first_neuron": ctx.oversized_first_neuron if ctx else False,
        "recall_latency_ms": ctx.recall_latency_ms if ctx else 0.0,
        "observed_total_model_input_tokens": observed_total_model_input,
        "neuron_scores": _build_neuron_score_dicts(all_scored, neuron_map),
        "classify_cost": classify_result.get("cost_usd", 0),
        "classify_input_tokens": classify_result["input_tokens"],
        "classify_output_tokens": classify_result["output_tokens"],
        "llm_session_id": (slot_results[0] or {}).get("llm_session_id") if slot_results else None,
        "slots": slot_results,
        "total_cost": total_cost,
        # Pattern #5: per-stage timing + status for the query-prep DAG.
        "stage_telemetry": ctx.stage_telemetry if ctx else [],
        # Frequency-hop citations: token -> source, for numbered superscripts.
        "citation_map": _build_citation_map(ctx, neuron_map),
    }


async def _update_counters_and_fire(
    db: AsyncSession,
    query: Query,
    slot_results: list[dict],
    classify_result: dict,
    needs_neurons: bool,
    all_scored: list[NeuronScoreBreakdown],
    fired_engram_ids: list[int] | None = None,
):
    assert isinstance(slot_results, list), "slot_results must be a list"
    assert isinstance(classify_result, dict), "classify_result must be a dict"
    state = await get_system_state(db)
    total_tokens = classify_result["input_tokens"] + classify_result["output_tokens"]
    for slot in slot_results:
        total_tokens += (
            slot["input_tokens"]
            + slot.get("cache_creation_tokens", 0)
            + slot.get("cache_read_tokens", 0)
            + slot["output_tokens"]
        )
    state.global_token_counter += total_tokens
    state.total_queries += 1

    if needs_neurons:
        included_ids = {s.neuron_id for s in all_scored}
        for idx, score in enumerate(all_scored):
            await record_firing(
                db, score.neuron_id, query.id,
                state.global_token_counter,
                global_query_offset=state.total_queries,
                score=score,
                rank=idx + 1,
                prompt_position=idx if score.neuron_id in included_ids else None,
                was_included=score.neuron_id in included_ids,
            )
            await propagate_activation(db, score.neuron_id, score.combined, query.id)
        cofire_neurons = [s for s in all_scored if s.combined >= settings.min_cofire_score]
        cofire_ids = [s.neuron_id for s in cofire_neurons]
        if len(cofire_ids) >= 2:
            await _batch_update_edges(db, cofire_ids, state.total_queries)
        from app.services.concept_service import cofire_concept_neurons
        await cofire_concept_neurons(db, cofire_ids, state.total_queries)

        # Grow the engram<->neuron association: regulations that fired this
        # query co-fire with the top neurons (fixes a gap — never recorded).
        if settings.engram_cofire_recording_enabled and fired_engram_ids:
            from app.services.engram_service import (
                record_engram_firings, record_engram_cofiring,
            )
            await record_engram_firings(db, fired_engram_ids, query.id, state.total_queries)
            top_neuron_ids = [s.neuron_id for s in all_scored[:settings.engram_cofire_max_neurons]]
            await record_engram_cofiring(db, fired_engram_ids, top_neuron_ids, state.total_queries)

    await db.commit()


_EFFORT_RANK = MappingProxyType({"low": 0, "medium": 1, "high": 2})


def _apply_primary_overrides(
    model_name: str,
    skip_effort: bool = False,
    tier_decision: TierDecision | None = None,
) -> str:
    """Primary-slot quality floor: raise reasoning effort (never lower it) and
    optionally swap to a stronger model, per settings. Returns the effective
    model name. skip_effort=True leaves effort alone (the slot set its own).

    tier_decision (tier-elastic routing, arch-tier-routing) escalates the
    model one tier when prep-time uncertainty signals fired; an explicit
    primary_answer_model setting wins over routing, and routing never
    downgrades (see tier_routing.escalated_model).

    Must run INSIDE the slot's asyncio task: each task gets its own copy of the
    execution context, so the effort_var set here is visible only to this
    slot's LLM calls — compare slots keep the request-level effort.
    """
    assert isinstance(model_name, str) and model_name, "model_name must be non-empty"

    floor = settings.primary_answer_effort
    if not skip_effort and floor in _EFFORT_RANK:
        current = effort_var.get() or settings.default_effort
        if _EFFORT_RANK.get(current, 0) < _EFFORT_RANK[floor]:
            effort_var.set(floor)

    override = settings.primary_answer_model
    if override and override in MODEL_REGISTRY:
        return override
    return escalated_model(model_name, tier_decision)


def _primed_ctx(ctx: PreparedContext) -> PreparedContext:
    """Slot-scoped workspace-priming variant of the shared prepared context.

    Returns a shallow copy whose system prompt is prefixed with a one-line
    topic preamble built from the PACKED sources only — packed neurons (score
    order) plus resolved regulations. The copy (not a mutation) keeps the
    priming strictly per-slot, and downstream grounding checks see exactly the
    prompt the model saw.
    """
    assert ctx is not None, "ctx must be populated for priming"
    from app.services.prompt_assembler import build_priming_line

    packed_ids = set(ctx.hop_map.token_by_neuron.keys()) if ctx.hop_map else set(ctx.neuron_map.keys())
    labels = [
        ctx.neuron_map[s.neuron_id].label
        for s in ctx.all_scored
        if s.neuron_id in packed_ids and s.neuron_id in ctx.neuron_map
    ]
    labels.extend(r.cfr_ref for r in (ctx.resolved_regulations or []))
    line = build_priming_line(ctx.intent, labels)
    if not line:
        return ctx
    return replace(ctx, system_prompt=line + ctx.system_prompt)


def _apply_slot_overrides(
    slot: dict, model_name: str, is_primary: bool,
    tier_decision: TierDecision | None = None,
) -> str:
    """Per-slot effort override + primary quality floor. Returns the effective
    model name. Must run INSIDE the slot's asyncio task (own context copy) so
    the effort_var set here stays slot-local.

    An EXPLICIT slot effort always wins — including over the primary floor: a
    user comparing the same model at low vs high effort must not have slot 0
    silently bumped to the floor.

    An audit-grade slot is the explicit opus@low action (arch-tier-routing):
    the measured audit profile — max faithfulness, terse — exists ONLY at low
    effort, so the effort floor, tier routing, and primary_answer_model are
    all bypassed. Explicit action beats every adaptive override.
    """
    assert isinstance(slot, dict), "slot must be a dict"
    if slot.get("audit"):
        effort_var.set("low")
        return "opus"
    explicit = slot.get("effort") in _EFFORT_RANK
    if explicit:
        effort_var.set(slot["effort"])
    if is_primary:
        return _apply_primary_overrides(
            model_name, skip_effort=explicit, tier_decision=tier_decision,
        )
    return model_name


async def _slot_grounding_guards(
    ctx: PreparedContext | None, user_message: str, response_text: str,
) -> tuple[str, int, int]:
    """Per-slot grounding exits, applied to EVERY slot's answer before it streams.

    1. Citation exit: strip fabricated [FQ] citations and count them, so every
       compare slot is guarded — not just the primary — and the UI can show
       which models fabricate.
    2. Ungrounded-authority detection: standards/regs the answer NAMED that
       aren't in the retrieved context (the frequency-hop layer can't see
       these — they're not [FQ-] keys). Deterministic string check against the
       assembled prompt. The normalised ref list feeds the inline UI marks;
       its length is the badge count.

    3. Citation relevance (layer 2, LOG-ONLY): max-pooled embedding cosine of
       each cited claim vs its cited sources — catches wrong-source citation.
       Advisory calibration data; nothing is flagged or stripped.

    Returns (cleaned_text, citations_fabricated, ungrounded_ref_list, relevance).
    """
    assert isinstance(response_text, str), "response_text must be a string"

    cleaned, citations_fabricated = await _clean_answer_citations(ctx, user_message, response_text)
    ungrounded_list: list[str] = []
    if ctx is not None and getattr(ctx, "system_prompt", None):
        from app.services.regulatory_coverage import list_ungrounded_refs
        ungrounded_list = list_ungrounded_refs(cleaned, ctx.system_prompt)
    relevance = None
    if (settings.citation_relevance_enabled and ctx is not None
            and getattr(ctx, "hop_map", None) is not None):
        from app.services.citation_relevance import (
            apply_relevance_bands, escalate_borderline, score_citation_relevance,
        )
        relevance = await asyncio.to_thread(
            score_citation_relevance, cleaned, ctx.hop_map, ctx.neuron_map,
            ctx.resolved_regulations, settings.citation_relevance_max_claims,
        )
        if relevance is not None:
            apply_relevance_bands(relevance)
            if settings.citation_relevance_escalate:
                await escalate_borderline(
                    relevance, ctx.hop_map, ctx.neuron_map, ctx.resolved_regulations,
                )
    return cleaned, citations_fabricated, ungrounded_list, relevance


def _format_slot_result_dict(
    mode, model_name, slot_type, uses_neurons, effective_effort,
    citations_fabricated, ungrounded_list, relevance, result_data,
    token_budget, ctx, label,
) -> dict:
    """Shape one slot's SlotResult payload from its execution artifacts."""
    assert isinstance(result_data, dict), "result_data must be a dict"
    observed_total_input = (
        result_data["input_tokens"]
        + result_data["cache_creation"]
        + result_data["cache_read"]
    )
    estimated_memory = ctx.estimated_memory_tokens if ctx else 0
    return {
        "mode": mode,
        "model": model_name,
        "neurons": uses_neurons,
        "effort": effective_effort,
        "citations_fabricated": citations_fabricated,
        "ungrounded_refs": len(ungrounded_list),
        "ungrounded_ref_list": ungrounded_list,
        "citation_relevance": relevance,
        "response": result_data["response_text"],
        "input_tokens": result_data["input_tokens"],
        "output_tokens": result_data["output_tokens"],
        "cost_usd": result_data["cost_usd"],
        "cache_creation_tokens": result_data["cache_creation"],
        "cache_read_tokens": result_data["cache_read"],
        "observed_total_input_tokens": observed_total_input,
        "estimated_memory_tokens": estimated_memory,
        # This is memory-estimate vs WHOLE model input, not an authoritative
        # memory-only tokenizer error; prompt/question overhead is included.
        "memory_estimation_error_tokens": (
            observed_total_input - estimated_memory if ctx else None
        ),
        "token_budget": token_budget,
        "top_k": ctx.neurons_delivered if ctx else 0,
        # Label from the EFFECTIVE model (primary override may differ from mode)
        "label": label or _eval_slot_label(f"{model_name}_{slot_type}", uses_neurons, token_budget),
        "model_version": result_data.get("model_version"),
        "llm_session_id": result_data.get("llm_session_id"),
    }


async def _execute_slot(
    db: AsyncSession,
    slot: dict,
    slot_index: int,
    user_message: str,
    ctx: PreparedContext | None,
    on_stage: StageCallback = None,
    is_primary: bool = False,
    session_spec: dict | None = None,
    tier_decision: TierDecision | None = None,
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
    if slot.get("audit") and not label:
        label = "Audit-grade (Opus @ low)"

    # Parse mode: "{model}_{type}" (e.g., "haiku_neuron", "sonnet_raw")
    parts = mode.rsplit("_", 1)
    model_name = parts[0] if parts else "haiku"
    slot_type = parts[1] if len(parts) > 1 else "neuron"
    uses_neurons = slot_type == "neuron"

    model_name = _apply_slot_overrides(slot, model_name, is_primary, tier_decision)
    effective_effort = effort_var.get() or settings.default_effort
    if slot.get("priming") and ctx is not None:
        ctx = _primed_ctx(ctx)

    start_time = time.monotonic()

    try:
        result_data = await _execute_slot_llm(
            db, user_message, ctx, model_name, uses_neurons, on_stage,
            session_spec=session_spec,
        )

        result_data["response_text"], citations_fabricated, ungrounded_list, relevance = (
            await _slot_grounding_guards(ctx, user_message, result_data["response_text"])
        )

        duration_ms = round((time.monotonic() - start_time) * 1000)

        result = _format_slot_result_dict(
            mode, model_name, slot_type, uses_neurons, effective_effort,
            citations_fabricated, ungrounded_list, relevance, result_data,
            token_budget, ctx, label,
        )
        # Routing telemetry rides the persisted slot result (results_json):
        # signals are recorded on every routed query — escalated or not — so
        # thresholds stay recalibratable from production data.
        if slot.get("audit"):
            result["audit_grade"] = True
        if tier_decision is not None:
            result["routing"] = tier_decision.to_payload()

        if on_stage:
            await on_stage("execute_llm", {"status": "done", "detail": {
                "slot_index": slot_index,
                "mode": mode,
                "model": model_name,
                "duration_ms": duration_ms,
                "output_tokens": result_data["output_tokens"],
            }})
            # Progressive population: emit this slot's full answer the moment it
            # finishes, so the UI fills slots in as they complete (fast models
            # first) instead of all-at-once after the slowest slot.
            await on_stage("slot_result", {"status": "done", "detail": {
                "slot_index": slot_index, **result,
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
    session_spec: dict | None = None,
) -> dict:
    """Run the LLM call for a single slot. Returns unified result dict.

    Two paths: direct neuron call or raw baseline.
    All paths return actual API-reported token counts (no estimates).
    """
    assert model_name, "model_name must be non-empty"
    assert isinstance(uses_neurons, bool), "uses_neurons must be a bool"

    if uses_neurons and ctx:
        # Direct call path with neurons — uses slot's model, real API tokens
        direct_result = await _run_direct_call(
            ctx, user_message, on_stage, model=model_name, session_spec=session_spec,
        )
        return {
            "response_text": direct_result.get("text", ""),
            "input_tokens": direct_result.get("input_tokens", 0),
            "output_tokens": direct_result.get("output_tokens", 0),
            "cost_usd": direct_result.get("cost_usd", 0.0),
            "cache_creation": direct_result.get("cache_creation_tokens", 0),
            "cache_read": direct_result.get("cache_read_tokens", 0),
            "model_version": direct_result.get("model_version"),
            "llm_session_id": direct_result.get("session_id"),
        }

    # Raw path: no neurons, no system prompt — just the user's question.
    # Raw slots are the control group; they get zero domain context from Corvus.
    llm_result = await llm_chat(
        system_prompt="",
        user_message=user_message,
        max_tokens=4096,
        model=model_name,
        session=session_spec,
    )
    return {
        "response_text": llm_result.get("text", ""),
        "input_tokens": llm_result.get("input_tokens", 0),
        "output_tokens": llm_result.get("output_tokens", 0),
        "cost_usd": llm_result.get("cost_usd", 0.0),
        "cache_creation": llm_result.get("cache_creation_tokens", 0),
        "cache_read": llm_result.get("cache_read_tokens", 0),
        "model_version": llm_result.get("model_version"),
        "llm_session_id": llm_result.get("session_id"),
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


async def _repair_citations(ctx, user_message, answer, hop_map, result):
    """One bounded LLM retry citing only valid keys, then re-verify (JPL-2)."""
    assert ctx is not None, "ctx must be populated for repair"
    from app.services.citation_hopping import (
        repair_instruction, extract_citation_tokens, verify_citations,
    )
    instruction = repair_instruction(hop_map, result.hallucinated)
    retry = await llm_chat(
        system_prompt=ctx.system_prompt + "\n\n" + instruction,
        user_message=user_message,
        max_tokens=4096,
        model="haiku",
    )
    new_answer = retry.get("text", "") or answer
    new_result = verify_citations(
        extract_citation_tokens(new_answer), hop_map,
        require_all=settings.citation_hop_require_all,
    )
    return new_answer, new_result


async def _clean_answer_citations(ctx, user_message: str, answer: str) -> tuple[str, int]:
    """Verify an answer's citation tokens against the per-query hop map and remove
    fabricated ones per settings.citation_hop_failure_mode.

    Applied to EVERY slot's answer (not just the primary) so a weaker model can't
    slip fabricated citations through the Query Lab compare grid. Returns
    (cleaned_answer, num_fabricated) — the count is the model's original fabrication
    count, reported even in 'detect' mode so the UI can surface it.
    """
    hop_map = getattr(ctx, "hop_map", None) if ctx else None
    if not settings.citation_hopping_enabled or hop_map is None or not answer:
        return answer, 0
    from app.services.citation_hopping import (
        extract_citation_tokens, verify_citations, strip_hallucinated,
    )
    require_all = settings.citation_hop_require_all
    result = verify_citations(extract_citation_tokens(answer), hop_map, require_all=require_all)
    num_fabricated = len(result.hallucinated)
    if result.ok:
        return answer, 0
    mode = settings.citation_hop_failure_mode
    if mode == "repair":
        answer, result = await _repair_citations(ctx, user_message, answer, hop_map, result)
    if not result.ok and mode in ("strip", "repair") and result.hallucinated:
        answer = strip_hallucinated(answer, result.hallucinated)
    return answer, num_fabricated


async def _apply_citation_hop_exit(
    db: AsyncSession,
    query: Query,
    ctx: PreparedContext | None,
    user_message: str,
) -> None:
    """Exit layer: grade the answer's citation keys against the secret hop map.

    Detects fabricated neuron references (cited keys absent from the per-query
    map), applies the configured failure mode (detect | strip | repair), and
    persists a CitationHopSession audit linked to the query. No-op when hopping
    is disabled, no map was minted, or the answer is empty.
    """
    if not settings.citation_hopping_enabled:
        return
    hop_map = getattr(ctx, "hop_map", None) if ctx else None
    if hop_map is None or not query.response_text:
        return

    from app.services.citation_hopping import (
        extract_citation_tokens, verify_citations, strip_hallucinated,
    )
    require_all = settings.citation_hop_require_all
    used = extract_citation_tokens(query.response_text)
    result = verify_citations(used, hop_map, require_all=require_all)

    mode = settings.citation_hop_failure_mode
    if not result.ok and mode == "repair":
        query.response_text, result = await _repair_citations(
            ctx, user_message, query.response_text, hop_map, result,
        )
    if not result.ok and mode in ("strip", "repair") and result.hallucinated:
        query.response_text = strip_hallucinated(query.response_text, result.hallucinated)

    session = CitationHopSession(
        token_map_json=serialize_hop_map(hop_map),
        required_json=sorted(hop_map.tokens()) if require_all else None,
        audit_json=result.to_dict(),
    )
    db.add(session)
    await db.flush()
    query.citation_hop_session_id = session.id


async def _finalize_query_results(
    db: AsyncSession,
    query: Query,
    ctx: PreparedContext | None,
    user_message: str,
    slot_results: list[dict],
    classify_result: dict,
    needs_neurons: bool,
    all_scored: list[NeuronScoreBreakdown],
    total_cost: float,
) -> None:
    """Record the primary response, run the citation-exit + regulatory-coverage
    layers, persist cost/results, and fire neurons + engrams."""
    for slot_result in slot_results:
        if slot_result.get("response") and not query.response_text:
            query.response_text = slot_result["response"]
            query.execute_input_tokens = slot_result.get(
                "observed_total_input_tokens",
                slot_result.get("input_tokens", 0)
                + slot_result.get("cache_creation_tokens", 0)
                + slot_result.get("cache_read_tokens", 0),
            )
            query.execute_output_tokens = slot_result.get("output_tokens", 0)
            query.model_version = slot_result.get("model")
            query.citation_relevance_json = slot_result.get("citation_relevance")
            break

    # Exit layer: verify citation keys against the secret per-query hop map.
    await _apply_citation_hop_exit(db, query, ctx, user_message)

    # Regulatory coverage: queue CFR refs cited but not resolved this query.
    if ctx is not None and ctx.resolved_regulations is not None:
        from app.services.regulatory_coverage import record_regulatory_coverage_gaps
        resolved_refs = {r.cfr_ref for r in ctx.resolved_regulations}
        await record_regulatory_coverage_gaps(db, query.response_text or "", resolved_refs, query.id)

    query.cost_usd = total_cost
    query.results_json = json.dumps(slot_results)
    fired_engram_ids = [r.engram_id for r in ctx.resolved_regulations] if ctx and ctx.resolved_regulations else []
    await _update_counters_and_fire(
        db, query, slot_results, classify_result,
        needs_neurons=needs_neurons, all_scored=all_scored, fired_engram_ids=fired_engram_ids,
    )


async def _acquire_query_contexts(
    db: AsyncSession,
    user_message: str,
    slots: list[dict],
    on_stage: StageCallback,
    prior_neuron_ids: list[int] | None,
    session_spec: dict | None,
) -> tuple:
    """Contexts for this query: drift-gate reuse or fresh per-config prep.

    Returns (ctx_by_cfg, classify_result, reused_ctx, overlap). The recall
    pipeline ALWAYS runs (embed-only, $0) — the gate then swaps in the
    session's active context when the fresh pack is a near-duplicate of it,
    so the redundant block is not re-sent into the transcript.
    """
    assert isinstance(slots, list) and slots, "slots must be non-empty"
    ctx_by_cfg, classify_result = await _prepare_slot_contexts(
        db, user_message, slots, on_stage, prior_neuron_ids,
    )
    fresh_ctx = ctx_by_cfg.get(_slot_spread_cfg(slots[0])) if ctx_by_cfg else None
    reused_ctx, overlap = _apply_drift_gate(session_spec, slots, fresh_ctx)
    if reused_ctx is not None:
        ctx_by_cfg = dict(ctx_by_cfg)
        ctx_by_cfg[_slot_spread_cfg(slots[0])] = reused_ctx
    return ctx_by_cfg, classify_result, reused_ctx, overlap


def _decide_primary_tier(
    slots: list[dict], ctx: PreparedContext | None,
    user_message: str, ctx_overlap: float | None,
) -> TierDecision | None:
    """Tier-elastic routing (arch-tier-routing): decide the primary slot's tier
    BEFORE execution from prep-time signals. Single-slot neuron queries only —
    multi-slot compares must not have slot 0 silently swapped (A/B integrity),
    and the audit action is explicit, never routed.
    """
    assert isinstance(slots, list) and slots, "slots must be non-empty"
    if not settings.tier_routing_enabled or len(slots) != 1 or ctx is None:
        return None
    if slots[0].get("mode", "haiku_neuron") not in NEURON_MODES or slots[0].get("audit"):
        return None
    return decide_tier_escalation(ctx, user_message, ctx_overlap)


async def execute_query(
    db: AsyncSession,
    user_message: str,
    slots: list[dict] | None = None,
    prior_neuron_ids: list[int] | None = None,
    on_stage: StageCallback = None,
    session_spec: dict | None = None,
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
            "top_k": None,
            "priming": True,
        }]

    gate = await _acquire_query_contexts(
        db, user_message, slots, on_stage, prior_neuron_ids, session_spec,
    )
    ctx_by_cfg, classify_result, reused_ctx, ctx_overlap = gate

    # Determine if neurons are actually needed based on slot composition
    needs_neurons = any(s["mode"] in NEURON_MODES for s in slots)
    # The primary (slot 0) group's context represents the query for
    # persistence / response metadata; falls back to the first prepared one.
    primary_key = _slot_spread_cfg(slots[0])
    ctx = ctx_by_cfg.get(primary_key) if needs_neurons else None
    if ctx is None and ctx_by_cfg:
        ctx = next(iter(ctx_by_cfg.values()))

    # Create query record early (before execution). primary_prompt is the
    # ground truth later evals judge against (query.py evaluate_query) — it
    # must be the context the primary answer actually grounded on, including
    # a drift-gate-reused session block. Was "" from 2026-04 to 2026-07-10,
    # silently forcing every eval onto the fresh-prep fallback.
    query = _create_query_record(
        user_message, ctx, needs_neurons=needs_neurons, slot_specs=slots,
        primary_prompt=(ctx.system_prompt if ctx else ""),
        classify_result=classify_result,
    )
    db.add(query)
    await db.flush()

    intent = ctx.intent if ctx else "general_query"
    all_scored = ctx.all_scored if ctx else []
    neuron_map = ctx.neuron_map if ctx else {}

    tier_decision = _decide_primary_tier(slots, ctx, user_message, ctx_overlap)

    # Execute each slot in parallel. Raw slots get no neuron context (vanilla
    # control group); slot 0 is the primary answer (effort/model floor) and the
    # only slot a persisted session applies to — compare slots stay stateless.
    slot_tasks = []
    for i, slot in enumerate(slots):
        mode = slot.get("mode", "haiku_neuron")
        parts = mode.rsplit("_", 1)
        slot_type = parts[1] if len(parts) > 1 else "neuron"
        uses_neurons = slot_type == "neuron"
        slot_ctx = ctx_by_cfg.get(_slot_spread_cfg(slot)) if uses_neurons else None
        task = _execute_slot(
            db=db,
            slot=slot,
            slot_index=i,
            user_message=user_message,
            ctx=slot_ctx,
            on_stage=on_stage,
            is_primary=(i == 0),
            session_spec=session_spec if i == 0 else None,
            tier_decision=tier_decision if i == 0 else None,
        )
        slot_tasks.append(task)

    slot_results = await asyncio.gather(*slot_tasks)
    if session_spec:
        _update_session_context(session_spec, slot_results, ctx)
    total_cost = classify_result.get("cost_usd", 0) + sum(s.get("cost_usd", 0) for s in slot_results)

    await _finalize_query_results(
        db, query, ctx, user_message, slot_results, classify_result,
        needs_neurons, all_scored, total_cost,
    )

    # Postcondition (JPL Rule 5)
    assert total_cost >= 0, f"total_cost must be non-negative, got {total_cost}"

    response = _build_response(
        query, ctx, needs_neurons=needs_neurons, all_scored=all_scored, max_top_k=len(all_scored),
        neuron_map=neuron_map, classify_result=classify_result, slot_results=slot_results, total_cost=total_cost,
    )
    response["context_reused"] = reused_ctx is not None
    response["context_overlap"] = ctx_overlap
    response["tier_routing"] = tier_decision.to_payload() if tier_decision else None
    return response


async def _load_candidates_by_ids(
    db: AsyncSession,
    neuron_ids: list[int],
    keywords: list[str],
    requester=None,
) -> list[NeuronCandidate]:
    """Load lightweight NeuronCandidate objects for a set of neuron IDs.

    Used when the semantic prefilter has already selected the candidate set,
    so we just need to hydrate the scoring-relevant fields. The requester's
    ACL scope is enforced here (the prefilter matrix is region-blind).
    """
    # Preconditions (JPL Power of Ten Rule 5)
    assert all(isinstance(nid, int) and nid > 0 for nid in neuron_ids), \
        "All neuron_ids must be positive integers"

    if not neuron_ids:
        return []

    # Fast path: serve from the materialized NeuronIndex (unrestricted requesters
    # only — ACL filtering stays on the DB path).
    if settings.neuron_index_enabled and requester is None:
        from app.services.neuron_index import ensure_index_loaded, get_index
        await ensure_index_loaded(db)
        return get_index().candidates(neuron_ids, keywords)

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
    from app.services.neuron_service import _FRESHNESS_SQL, _acl_clause_for
    acl_clause = await _acl_clause_for(db, requester, params)
    params["id_list"] = list(neuron_ids)
    sql = f"""
        SELECT id, label, summary, department, role_key, avg_utility,
               invocations, created_at_query_count, ({kw_expr}) AS keyword_hits,
               authority_level, ({_FRESHNESS_SQL}) AS freshness_days, centrality
        FROM neurons
        WHERE id = ANY(:id_list) AND is_active = true AND {acl_clause}
        ORDER BY id
    """
    result = await db.execute(text(sql), params)
    rows = result.all()

    candidates = [
        NeuronCandidate(
            id=r[0], label=r[1], summary=r[2], department=r[3], role_key=r[4],
            avg_utility=r[5] or 0.5, invocations=r[6] or 0,
            created_at_query_count=r[7] or 0, keyword_hits=r[8] or 0,
            authority_level=r[9],
            freshness_days=float(r[10]) if r[10] is not None else None,
            centrality=r[11] or 0.0,
        )
        for r in rows
    ]

    # Postcondition (JPL Power of Ten Rule 5)
    assert len(candidates) <= len(neuron_ids), \
        f"Output length ({len(candidates)}) must not exceed input length ({len(neuron_ids)})"

    return candidates


async def _fetch_regions_for(db: AsyncSession, neuron_ids: list[int]) -> dict[int, str | None]:
    """Load the region tag (department column) for a set of neurons."""
    from sqlalchemy import text
    if not neuron_ids:
        return {}
    result = await db.execute(
        text("SELECT id, department FROM neurons WHERE id = ANY(:ids)"),
        {"ids": list(neuron_ids)},
    )
    return {int(r[0]): r[1] for r in result.all()}


def _derive_edge_type(region_a: str | None, region_b: str | None) -> str:
    """Stellate = intra-region (local), pyramidal = cross-region (long-range).

    Unknown regions default to pyramidal (conservative: stronger decay
    threshold, weaker spread) — matches the pre-region behavior.
    """
    if region_a and region_b and region_a == region_b:
        return "stellate"
    return "pyramidal"


async def _batch_update_edges(db: AsyncSession, neuron_ids: list[int], query_offset: int):
    """Batch update co-firing edges for a set of neurons (tiered storage).

    Promoted edges (above threshold) stay in neuron_edges table.
    Weak edges live in JSONB on the neurons table. New edges are typed
    stellate/pyramidal from region membership of their endpoints.
    """
    # Precondition (JPL Power of Ten Rule 5)
    assert len(neuron_ids) >= 2, \
        f"_batch_update_edges requires >= 2 neuron_ids, got {len(neuron_ids)}"

    pairs = [(min(a, b), max(a, b))
             for i, a in enumerate(neuron_ids)
             for b in neuron_ids[i + 1:]]

    region_by_id = await _fetch_regions_for(db, neuron_ids)
    edge_type_for = {
        (s, t): _derive_edge_type(region_by_id.get(s), region_by_id.get(t))
        for s, t in pairs
    }

    # Check which pairs already exist in the promoted table
    existing = await _find_promoted_pairs(db, pairs)
    promoted_pairs = [p for p in pairs if p in existing]
    weak_pairs = [p for p in pairs if p not in existing]

    # Update promoted edges in table
    cache_pairs, cache_weights = await _cofire_promoted(
        db, promoted_pairs, query_offset,
    )

    # Update weak edges in JSONB, promoting any that cross threshold
    newly_promoted = await _cofire_weak(db, weak_pairs, query_offset, edge_type_for)

    # Update adjacency cache for table-level changes
    from app.services.adjacency_cache import is_adjacency_loaded, update_adjacency_incremental
    all_cache_pairs = cache_pairs + [(s, t) for s, t, _w in newly_promoted]
    all_cache_weights = cache_weights + [w for _s, _t, w in newly_promoted]
    if is_adjacency_loaded() and all_cache_pairs:
        update_adjacency_incremental(
            pairs=all_cache_pairs,
            weights=all_cache_weights,
            edge_types=[
                edge_type_for.get((s, t), "pyramidal") for s, t in all_cache_pairs
            ],
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
    edge_type_for: dict[tuple[int, int], str] | None = None,
) -> list[tuple[int, int, float]]:
    """Increment co-fire for weak edges in JSONB, creating new ones as needed.

    New edges are typed from region membership (stellate intra-region,
    pyramidal cross-region); existing edges keep their recorded type.
    Returns list of (src, tgt, weight) for edges that were promoted to table.
    """
    from app.services.edge_tier import (
        get_weak_edge, upsert_weak_edge, maybe_promote,
    )
    promoted: list[tuple[int, int, float]] = []
    for src, tgt in pairs:
        entry = await get_weak_edge(db, src, tgt)
        derived_type = (edge_type_for or {}).get((src, tgt), "pyramidal")
        if entry is not None:
            new_c = entry.get("c", 0) + 1
            new_w = min(1.0, (new_c + 1) / 20.0)
            edge_type = entry.get("t") or derived_type
        else:
            new_c = 1
            new_w = min(1.0, 2 / 20.0)
            edge_type = derived_type
        data = {
            "w": new_w, "t": edge_type, "c": new_c,
            "s": "organic", "q": qoff,
        }
        await upsert_weak_edge(db, src, tgt, data)
        if await maybe_promote(db, src, tgt, new_w, new_c):
            promoted.append((src, tgt, new_w))
    return promoted
