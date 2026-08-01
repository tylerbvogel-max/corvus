"""Recall primitives: the algorithms the query-prep pipeline is built from.

Extracted from ``executor`` by roadmap record durability-modular-monolith
(04, seam 4). ``executor`` was doing two jobs — it held these algorithms AND it
built and ran the pipeline that calls them — so the thin stage wrappers in
``app/services/pipeline/stages/`` had to import the executor back. That was the
second of the two loops braided into the eight-module cycle.

Vocabulary note, because the two words are close: a STAGE is the pipeline
wrapper that handles state, telemetry and short-circuiting; a PRIMITIVE here is
the algorithm the stage delegates to. The stage is orchestration, this is work.

The direction is now one-way and must stay that way:

    executor -> pipeline.stages -> recall_primitives
    executor -> pipeline.stages -> structural_resolver -> prepared_context

Nothing in this module may import ``executor`` or ``app.services.pipeline``.
The extraction was only clean because these nine functions reference no
module-level state from executor and — verified at the time — no remaining
executor function calls any of them.
"""

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Neuron
from app.services.citation_hopping import HopMap, mint_hop_map
from app.services.neuron_candidate import NeuronCandidate
from app.services.neuron_service import (
    get_neurons_by_filter,
    score_candidates,
    select_with_hierarchy,
    apply_diversity_floor,
)
from app.services.prompt_assembler import assemble_prompt
from app.services.scoring_engine import NeuronScoreBreakdown


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
) -> list[tuple[str, dict[int, float]]]:
    """Hybrid-recall lanes (LLM-free, indexed SQL): keyword tsvector + entity
    match retrieve their own candidates so a named-thing memory can enter
    the pool even when it loses the cosine race. Fused by RRF downstream.
    Returns (lane_name, hits) pairs so telemetry can attribute each hit."""
    if not user_message or not (
            settings.keyword_lane_enabled or settings.entity_lane_enabled):
        return []
    from app.services.recall_lanes import (
        entity_lane, extract_query_entities, keyword_lane,
    )
    lanes: list[tuple[str, dict[int, float]]] = []
    if settings.keyword_lane_enabled:
        lanes.append(("keyword",
                      await keyword_lane(db, user_message, settings.recall_lane_top_n)))
    if settings.entity_lane_enabled:
        lanes.append(("entity", await entity_lane(
            db, extract_query_entities(user_message), settings.recall_lane_top_n)))
    return [(name, lane) for name, lane in lanes if lane]


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
) -> tuple[list[NeuronScoreBreakdown], list[NeuronScoreBreakdown],
           dict[str, list[int]], dict[int, float]]:
    """Score neuron and engram candidates.
    Returns (scored_neurons, scored_engrams, lane_hits, embedding_sims).
    lane_hits maps lane name (embedding/keyword/entity/filter) → candidate
    neuron ids; embedding_sims carries the raw pre-RRF cosine per neuron —
    both observe-only, for retrieval telemetry. The raw cosine matters because
    RRF rank normalization pins the top-1 fused score to a constant, so only
    the pre-fusion magnitudes can express retrieval confidence.

    The requester's ACL scope filters candidates at load time (the semantic
    prefilter matrix is region-blind, so enforcement happens in SQL here).
    """
    assert isinstance(effective_pool, int) and effective_pool > 0, \
        "effective_pool must be a positive integer"

    scored_engrams: list[NeuronScoreBreakdown] = []
    semantic_results: list[tuple[int, str, float]] | None = None
    lane_hits: dict[str, list[int]] = {}
    embedding_sims: dict[int, float] = {}

    if settings.semantic_prefilter_enabled and query_embedding is not None:
        from app.services.semantic_prefilter import semantic_prefilter
        semantic_results = await semantic_prefilter(db, query_embedding, top_n_override=effective_pool)

    named_lanes = await _run_hybrid_lanes(db, user_message)
    extra_lanes = [lane for _name, lane in named_lanes]
    lane_hits.update({name: list(lane) for name, lane in named_lanes})

    if semantic_results or extra_lanes:
        # Partition into neurons and engrams
        semantic_results = semantic_results or []
        neuron_sims = {eid: sim for eid, etype, sim in semantic_results if etype == "neuron"}
        engram_sims = {eid: sim for eid, etype, sim in semantic_results if etype == "engram"}

        # Score neurons: union of embedding-lane and lexical/entity-lane hits
        sem_ids = list(neuron_sims.keys())
        lane_hits["embedding"] = sem_ids
        embedding_sims = dict(neuron_sims)
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
        lane_hits["filter"] = [c.id for c in candidates]
        scored = await score_candidates(
            db, candidates, total_queries, keywords, departments, role_keys,
            query_embedding=query_embedding,
        )

    if settings.token_bounded_assembly_enabled:
        # Hybrid lane union can exceed the semantic lane's top_n. Rank first,
        # then enforce one independently observable activation candidate pool.
        scored = scored[:effective_pool]
    assert isinstance(scored, list), "scored must be a list"
    return scored, scored_engrams, lane_hits, embedding_sims


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
    if (
        settings.neuron_index_enabled
        and settings.cache_coherence_mode == "process-local"
        and requester is None
    ):
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
    from app.services.neuron_candidate import FRESHNESS_SQL
    from app.services.neuron_service import _acl_clause_for
    acl_clause = await _acl_clause_for(db, requester, params)
    params["id_list"] = list(neuron_ids)
    sql = f"""
        SELECT id, label, summary, department, role_key, avg_utility,
               invocations, created_at_query_count, ({kw_expr}) AS keyword_hits,
               authority_level, ({FRESHNESS_SQL}) AS freshness_days, centrality
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
