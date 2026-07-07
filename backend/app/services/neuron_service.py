"""Neuron CRUD, candidate pre-filtering, and firing record management."""

import datetime
import json
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select, func, and_, text, literal_column, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Neuron, NeuronFiring, NeuronScoreOverride, SystemState
from app.services.scoring_engine import (
    compute_score, calc_relevance, calc_hybrid_relevance, NeuronScoreBreakdown,
    ColdstartInputs, apply_score_overrides,
    calc_burst_batch, calc_impact_batch, calc_precision_batch,
    calc_novelty_batch, calc_recency_batch, calc_coldstart_term_batch,
)


@dataclass
class NeuronCandidate:
    """Lightweight neuron representation for scoring (no content blob)."""
    id: int
    label: str
    summary: str | None
    department: str | None
    role_key: str | None
    avg_utility: float
    invocations: int
    created_at_query_count: int
    keyword_hits: int = 0
    # Cold-start prior inputs (authority + freshness + centrality)
    authority_level: str | None = None
    freshness_days: float | None = None
    centrality: float = 0.0

    @property
    def region(self) -> str | None:
        """Generic region vocabulary — silos are labeled regions."""
        return self.department


# SQL expression for days since the most authoritative provenance date.
_FRESHNESS_SQL = (
    "EXTRACT(EPOCH FROM (now() - COALESCE(last_verified, "
    "effective_date::timestamp, created_at))) / 86400.0"
)


def _coldstart_fields(candidate) -> tuple[str | None, float | None, float, int]:
    """Resolve (authority, freshness_days, centrality, invocations)
    from either a NeuronCandidate or a full Neuron ORM object."""
    authority = getattr(candidate, "authority_level", None)
    freshness = getattr(candidate, "freshness_days", None)
    if freshness is None:
        stamp = (
            getattr(candidate, "last_verified", None)
            or getattr(candidate, "effective_date", None)
            or getattr(candidate, "created_at", None)
        )
        if isinstance(stamp, datetime.date) and not isinstance(stamp, datetime.datetime):
            stamp = datetime.datetime.combine(stamp, datetime.time())
        if isinstance(stamp, datetime.datetime):
            freshness = max(0.0, (datetime.datetime.utcnow() - stamp).total_seconds() / 86400.0)
    centrality = getattr(candidate, "centrality", 0.0) or 0.0
    invocations = getattr(candidate, "invocations", 0) or 0
    return authority, freshness, centrality, invocations


async def get_neuron(db: AsyncSession, neuron_id: int) -> Neuron | None:
    return await db.get(Neuron, neuron_id)


def _build_filter_conditions(
    departments: list[str] | None,
    role_keys: list[str] | None,
    params: dict,
) -> list[str]:
    conditions = ["is_active = true"]

    if role_keys:
        role_placeholders = ", ".join(f":role_{i}" for i in range(len(role_keys)))
        for i, r in enumerate(role_keys):
            params[f"role_{i}"] = r
        if departments:
            dept_placeholders = ", ".join(f":dept_{i}" for i in range(len(departments)))
            for i, d in enumerate(departments):
                params[f"dept_{i}"] = d
            conditions.append(
                f"(role_key IN ({role_placeholders}) "
                f"OR (department IN ({dept_placeholders}) AND layer <= 1))"
            )
        else:
            conditions.append(f"role_key IN ({role_placeholders})")
    elif departments:
        placeholders = ", ".join(f":dept_{i}" for i in range(len(departments)))
        conditions.append(f"department IN ({placeholders})")
        for i, d in enumerate(departments):
            params[f"dept_{i}"] = d

    return conditions


def _build_keyword_expr(keywords: list[str] | None, params: dict) -> str:
    kw_parts = []
    if keywords:
        for i, kw in enumerate(keywords):
            param_name = f"kw_{i}"
            params[param_name] = f"%{kw.lower()}%"
            kw_parts.append(
                f"(CASE WHEN lower(label) LIKE :{param_name} THEN 1 ELSE 0 END + "
                f"CASE WHEN lower(summary) LIKE :{param_name} THEN 1 ELSE 0 END + "
                f"CASE WHEN lower(content) LIKE :{param_name} THEN 1 ELSE 0 END)"
            )
    return " + ".join(kw_parts) if kw_parts else "0"


def _build_candidate_sql(where_clause: str, kw_expr: str) -> str:
    return f"""
        SELECT id, label, summary, department, role_key, avg_utility,
               invocations, created_at_query_count, ({kw_expr}) AS keyword_hits,
               authority_level, ({_FRESHNESS_SQL}) AS freshness_days, centrality
        FROM neurons
        WHERE {where_clause}
        ORDER BY keyword_hits DESC, avg_utility DESC
        LIMIT :lim
    """


def _rows_to_candidates(rows: list) -> list[NeuronCandidate]:
    return [
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


async def _acl_clause_for(db: AsyncSession, requester, params: dict) -> str:
    """Resolve the requester's ACL SQL clause against active region policies."""
    from app.services.region_policy import (
        acl_sql_clause, get_region_policies, restricted_regions,
    )
    if requester is None:
        return "TRUE"
    policies = await get_region_policies(db)
    return acl_sql_clause(requester, restricted_regions(policies), params)


async def get_neurons_by_filter(
    db: AsyncSession,
    departments: list[str] | None = None,
    role_keys: list[str] | None = None,
    keywords: list[str] | None = None,
    requester=None,
) -> list[NeuronCandidate]:
    """Pre-filter candidate neurons by classification results.

    Returns lightweight NeuronCandidate objects (no content blob) ranked by
    SQL-side keyword hits, limited to candidate_limit. Full content is only
    loaded later for the final top-K during prompt assembly. The requester's
    ACL scope (region-restricted recall) is enforced in SQL.
    """
    params: dict = {}
    conditions = _build_filter_conditions(departments, role_keys, params)
    conditions.append(await _acl_clause_for(db, requester, params))
    kw_expr = _build_keyword_expr(keywords, params)
    params["lim"] = settings.candidate_limit

    where_clause = " AND ".join(conditions)
    sql = _build_candidate_sql(where_clause, kw_expr)

    result = await db.execute(text(sql), params)
    rows = result.all()

    if not rows and (departments or role_keys):
        acl_params: dict = {k: v for k, v in params.items() if k.startswith("acl_") or k.startswith("kw_") or k == "lim"}
        acl_fallback = await _acl_clause_for(db, requester, acl_params)
        fallback_sql = _build_candidate_sql(f"is_active = true AND {acl_fallback}", kw_expr)
        result = await db.execute(text(fallback_sql), acl_params)
        rows = result.all()

    return _rows_to_candidates(rows)


async def get_system_state(db: AsyncSession) -> SystemState:
    result = await db.execute(select(SystemState).where(SystemState.id == 1))
    state = result.scalar_one_or_none()
    if not state:
        state = SystemState(id=1, global_token_counter=0, total_queries=0)
        db.add(state)
        await db.flush()
    return state


async def _fetch_burst_counts(
    db: AsyncSession,
    candidate_ids: list[int],
    query_window: int,
) -> dict[int, int]:
    """Batch query: firings in recent window per neuron."""
    burst_result = await db.execute(
        select(
            NeuronFiring.neuron_id,
            func.count(NeuronFiring.id),
        )
        .where(
            and_(
                NeuronFiring.neuron_id.in_(candidate_ids),
                NeuronFiring.global_query_offset >= query_window,
            )
        )
        .group_by(NeuronFiring.neuron_id)
    )
    assert burst_result is not None, "Burst count query returned None"
    return dict(burst_result.all())


async def _fetch_neuron_fire_stats(
    db: AsyncSession,
    candidate_ids: list[int],
) -> tuple[dict[int, int], dict[int, int]]:
    """Batch query: per-neuron distinct query fires and last offset."""
    neuron_stats_result = await db.execute(
        select(
            NeuronFiring.neuron_id,
            func.count(func.distinct(NeuronFiring.query_id)),
            func.max(NeuronFiring.global_query_offset),
        )
        .where(NeuronFiring.neuron_id.in_(candidate_ids))
        .group_by(NeuronFiring.neuron_id)
    )
    neuron_fires_map: dict[int, int] = {}
    last_offset_map: dict[int, int] = {}
    for nid, fires, last_off in neuron_stats_result.all():
        neuron_fires_map[nid] = fires
        last_offset_map[nid] = last_off
    assert isinstance(neuron_fires_map, dict), "neuron_fires_map must be a dict"
    return neuron_fires_map, last_offset_map


async def _fetch_dept_fire_totals(
    db: AsyncSession,
    candidates: list[Neuron] | list[NeuronCandidate],
) -> dict[str, int]:
    """Batch query: dept-level total distinct query fires."""
    candidate_depts = list({n.department for n in candidates if n.department})
    if not candidate_depts:
        return {}
    dept_total_result = await db.execute(
        select(
            Neuron.department,
            func.count(func.distinct(NeuronFiring.query_id)),
        )
        .join(NeuronFiring, NeuronFiring.neuron_id == Neuron.id)
        .where(Neuron.department.in_(candidate_depts))
        .group_by(Neuron.department)
    )
    assert dept_total_result is not None, "Dept fire totals query returned None"
    return dict(dept_total_result.all())


async def _resolve_semantic_map(
    db: AsyncSession,
    candidate_ids: list[int],
    query_embedding: list[float] | None,
    precomputed_similarities: dict[int, float] | None,
) -> dict[int, float]:
    """Resolve semantic similarity scores from precomputed values or embeddings."""
    if precomputed_similarities is not None:
        return precomputed_similarities
    if query_embedding is None:
        return {}
    emb_result = await db.execute(
        text("SELECT id, embedding FROM neurons WHERE id IN :ids AND embedding IS NOT NULL"),
        {"ids": tuple(candidate_ids) if candidate_ids else (0,)},
    )
    neuron_vecs = []
    neuron_ids_with_emb = []
    for nid, emb_json in emb_result.all():
        neuron_ids_with_emb.append(nid)
        neuron_vecs.append(json.loads(emb_json))
    if not neuron_vecs:
        return {}
    from app.services.embedding_service import batch_cosine_similarity
    similarities = batch_cosine_similarity(query_embedding, neuron_vecs)
    assert len(similarities) == len(neuron_ids_with_emb), "Similarity count mismatch"
    return dict(zip(neuron_ids_with_emb, similarities))


async def _load_active_overrides(
    db: AsyncSession, candidate_ids: list[int],
) -> dict[int, list[dict]]:
    """Load active score overrides for a set of neuron IDs."""
    if not candidate_ids:
        return {}
    result = await db.execute(
        select(NeuronScoreOverride).where(
            NeuronScoreOverride.neuron_id.in_(candidate_ids),
            NeuronScoreOverride.is_active.is_(True),
        )
    )
    overrides: dict[int, list[dict]] = {}
    for ov in result.scalars().all():
        overrides.setdefault(ov.neuron_id, []).append({
            "signal": ov.signal,
            "floor": ov.floor,
            "ceiling": ov.ceiling,
            "multiplier": ov.multiplier,
        })
    return overrides


def _score_single_candidate(
    neuron: Neuron | NeuronCandidate,
    total_queries: int,
    keywords: list[str],
    burst_map: dict[int, int],
    neuron_fires_map: dict[int, int],
    dept_total_map: dict[str, int],
    last_offset_map: dict[int, int],
    semantic_map: dict[int, float],
    classified_departments: list[str] | None,
    classified_role_keys: list[str] | None,
    hybrid_map: dict[int, float] | None = None,
) -> NeuronScoreBreakdown:
    """Compute score breakdown for a single candidate neuron."""
    fires_in_window = burst_map.get(neuron.id, 0)
    dept_fires = neuron_fires_map.get(neuron.id, 0)
    dept_total = dept_total_map.get(neuron.department, 0)
    age_queries = total_queries - (neuron.created_at_query_count or 0)

    last_offset = last_offset_map.get(neuron.id)
    queries_since_last = total_queries - last_offset if last_offset is not None else total_queries

    content = getattr(neuron, 'content', None) or ''
    neuron_text = f"{neuron.label} {neuron.summary or ''} {content}"
    dept_match = bool(classified_departments and neuron.department in classified_departments)
    role_match = bool(classified_role_keys and neuron.role_key in classified_role_keys)

    hybrid_score = hybrid_map.get(neuron.id) if hybrid_map else None
    authority, freshness, centrality, invocations = _coldstart_fields(neuron)
    score = compute_score(
        fires_in_window=fires_in_window,
        avg_utility=neuron.avg_utility,
        dept_fires=dept_fires,
        dept_total_queries=dept_total,
        age_queries=age_queries,
        queries_since_last=queries_since_last,
        keywords=keywords,
        neuron_text=neuron_text,
        neuron_id=neuron.id,
        dept_match=dept_match,
        role_match=role_match,
        semantic_similarity=semantic_map.get(neuron.id),
        hybrid_score=hybrid_score,
        coldstart=ColdstartInputs(
            authority_level=authority,
            freshness_days=freshness,
            centrality=centrality,
            invocations=invocations,
        ),
    )
    assert score.combined >= 0, f"Score for neuron {neuron.id} is negative: {score.combined}"
    return score


async def score_candidates(
    db: AsyncSession,
    candidates: list[Neuron] | list[NeuronCandidate],
    total_queries: int,
    keywords: list[str],
    classified_departments: list[str] | None = None,
    classified_role_keys: list[str] | None = None,
    query_embedding: list[float] | None = None,
    precomputed_similarities: dict[int, float] | None = None,
) -> list[NeuronScoreBreakdown]:
    """Score all candidate neurons using 6 biomimetic signals.

    Accepts either full Neuron ORM objects or lightweight NeuronCandidate.
    Batches all DB lookups into 3 aggregate queries instead of 4 per candidate.

    If precomputed_similarities is provided (from semantic prefilter), uses those
    directly instead of loading embeddings from DB. Otherwise falls back to
    query_embedding-based lookup or keyword matching.
    """
    if not candidates:
        return []

    input_count = len(candidates)
    candidate_ids = [n.id for n in candidates]
    query_window = max(0, total_queries - settings.burst_window_queries)

    burst_map = await _fetch_burst_counts(db, candidate_ids, query_window)
    neuron_fires_map, last_offset_map = await _fetch_neuron_fire_stats(db, candidate_ids)
    dept_total_map = await _fetch_dept_fire_totals(db, candidates)
    semantic_map = await _resolve_semantic_map(
        db, candidate_ids, query_embedding, precomputed_similarities,
    )

    # Per-region scoring weights (plat-region-config): silos may override
    # the global signal weights because their epistemics differ.
    from app.services.region_policy import get_region_policies, resolve_scoring_weights
    policies = await get_region_policies(db)
    region_weights: dict[str, dict] | None = None
    if any((p.get("scoring_weights") or {}) for p in policies.values()):
        region_weights = {
            region: resolve_scoring_weights(region, policies)
            for region in {c.department for c in candidates if c.department}
        }

    # Hybrid RRF: fuse keyword + semantic scores when both are available
    hybrid_map: dict[int, float] | None = None
    if settings.hybrid_relevance_enabled and semantic_map and keywords:
        keyword_scores: dict[int, float] = {}
        for neuron in candidates:
            content = getattr(neuron, 'content', None) or ''
            neuron_text = f"{neuron.label} {neuron.summary or ''} {content}"
            keyword_scores[neuron.id] = calc_relevance(keywords, neuron_text)
        hybrid_map = calc_hybrid_relevance(keyword_scores, semantic_map, k=settings.rrf_k)

    scores = _score_candidates_vectorized(
        candidates, total_queries, keywords,
        burst_map, neuron_fires_map, dept_total_map,
        last_offset_map, semantic_map,
        classified_departments, classified_role_keys,
        hybrid_map, region_weights,
    )

    # Apply per-neuron score overrides (manual tuning)
    overrides_by_neuron = await _load_active_overrides(db, candidate_ids)
    if overrides_by_neuron:
        scores = apply_score_overrides(scores, overrides_by_neuron)

    scores.sort(key=lambda s: s.combined, reverse=True)

    assert all(s.combined >= 0 for s in scores), "All combined scores must be non-negative"
    assert len(scores) <= input_count, f"Output length {len(scores)} exceeds input length {input_count}"
    return scores


def _batch_coldstart_terms(candidates: list) -> np.ndarray:
    """Vectorized cold-start prior terms for a candidate batch."""
    rows = [_coldstart_fields(c) for c in candidates]
    return calc_coldstart_term_batch(
        authority_levels=[r[0] for r in rows],
        freshness_days=np.array(
            [float("nan") if r[1] is None else r[1] for r in rows],
            dtype=np.float64,
        ),
        centrality=np.array([r[2] for r in rows], dtype=np.float64),
        invocations=np.array([r[3] for r in rows], dtype=np.float64),
    )


def _weight_arrays(
    candidates: list, region_weights: dict[str, dict] | None,
) -> dict[str, np.ndarray] | None:
    """Per-candidate signal-weight arrays when any region overrides exist."""
    if not region_weights:
        return None
    from app.services.region_policy import WEIGHT_KEYS, default_weights
    defaults = default_weights()
    arrays: dict[str, np.ndarray] = {}
    for key in WEIGHT_KEYS:
        arrays[key] = np.array(
            [
                (region_weights.get(c.department) or defaults).get(key, defaults[key])
                for c in candidates
            ],
            dtype=np.float64,
        )
    return arrays


def _effective_weights(
    candidates: list, region_weights: dict[str, dict] | None,
) -> dict:
    """Signal weights for scoring: global scalars, or per-candidate arrays
    when region policies override them. coldstart_scale rescales the
    already-computed coldstart terms to each region's weight."""
    arrays = _weight_arrays(candidates, region_weights)
    if arrays is None:
        return {
            "weight_relevance": settings.weight_relevance,
            "weight_burst": settings.weight_burst,
            "weight_impact": settings.weight_impact,
            "weight_precision": settings.weight_precision,
            "weight_novelty": settings.weight_novelty,
            "weight_recency": settings.weight_recency,
            "coldstart_scale": 1.0,
        }
    base_coldstart = max(settings.weight_coldstart_prior, 1e-9)
    arrays["coldstart_scale"] = arrays.pop("weight_coldstart_prior") / base_coldstart
    return arrays


def _compute_base_signals(
    candidates: list, total_queries: int, burst_map: dict[int, int],
    neuron_fires_map: dict[int, int], dept_total_map: dict[str, int],
    last_offset_map: dict[int, int],
) -> tuple:
    """Compute the 5 usage signals (burst/impact/precision/novelty/recency) as arrays."""
    burst_counts = np.array([burst_map.get(c.id, 0) for c in candidates], dtype=np.float64)
    avg_utilities = np.array([c.avg_utility or 0.5 for c in candidates], dtype=np.float64)
    ages = np.array([total_queries - (c.created_at_query_count or 0) for c in candidates], dtype=np.float64)
    queries_since = np.array([
        total_queries - last_offset_map[c.id] if c.id in last_offset_map else total_queries
        for c in candidates
    ], dtype=np.float64)
    dept_fires_arr = np.array([neuron_fires_map.get(c.id, 0) for c in candidates], dtype=np.float64)
    dept_totals_arr = np.array([dept_total_map.get(c.department, 0) for c in candidates], dtype=np.float64)
    return (
        calc_burst_batch(burst_counts),
        calc_impact_batch(avg_utilities),
        calc_precision_batch(dept_fires_arr, dept_totals_arr),
        calc_novelty_batch(ages),
        calc_recency_batch(queries_since),
    )


def _resolve_relevance_arr(
    candidates: list, keywords: list[str], semantic_map: dict[int, float],
    hybrid_map: dict[int, float] | None,
) -> "np.ndarray":
    """Per-candidate relevance: hybrid > semantic > keyword fallback."""
    relevance_arr = np.empty(len(candidates), dtype=np.float64)
    for i, c in enumerate(candidates):
        if hybrid_map is not None and c.id in hybrid_map:
            relevance_arr[i] = max(0.0, min(1.0, hybrid_map[c.id]))
        elif c.id in semantic_map:
            relevance_arr[i] = max(0.0, min(1.0, semantic_map[c.id]))
        else:
            content = getattr(c, 'content', None) or ''
            neuron_text = f"{c.label} {c.summary or ''} {content}"
            relevance_arr[i] = calc_relevance(keywords, neuron_text)
    return relevance_arr


def _gated_combined(
    relevance_arr, burst, impact, precision, novelty, recency, coldstart_terms, w: dict,
) -> "np.ndarray":
    """Gated modulatory combine: stimulus + gate * modulatory, clamped at 0.

    The signed coldstart term may push inactive neurons negative, hence the clamp.
    """
    stimulus = w["weight_relevance"] * relevance_arr
    modulatory = (
        w["weight_burst"] * burst
        + w["weight_impact"] * impact
        + w["weight_precision"] * precision
        + w["weight_novelty"] * novelty
        + w["weight_recency"] * recency
        + coldstart_terms * w["coldstart_scale"]
    )
    threshold = settings.relevance_gate_threshold
    floor = settings.relevance_gate_floor
    gate = np.where(
        relevance_arr >= threshold,
        1.0,
        np.where(relevance_arr > 0, floor + (1.0 - floor) * (relevance_arr / threshold), floor),
    )
    return np.maximum(0.0, stimulus + modulatory * gate)


def _build_score_breakdowns(
    candidates: list, burst, impact, precision, novelty, recency, relevance_arr, combined,
) -> list[NeuronScoreBreakdown]:
    """Assemble NeuronScoreBreakdown rows from the computed signal arrays."""
    return [
        NeuronScoreBreakdown(
            neuron_id=candidates[i].id,
            burst=round(float(burst[i]), 4),
            impact=round(float(impact[i]), 4),
            precision=round(float(precision[i]), 4),
            novelty=round(float(novelty[i]), 4),
            recency=round(float(recency[i]), 4),
            relevance=round(float(relevance_arr[i]), 4),
            combined=round(float(combined[i]), 4),
        )
        for i in range(len(candidates))
    ]


def _score_candidates_vectorized(
    candidates: list,
    total_queries: int,
    keywords: list[str],
    burst_map: dict[int, int],
    neuron_fires_map: dict[int, int],
    dept_total_map: dict[str, int],
    last_offset_map: dict[int, int],
    semantic_map: dict[int, float],
    classified_departments: list[str] | None,
    classified_role_keys: list[str] | None,
    hybrid_map: dict[int, float] | None = None,
    region_weights: dict[str, dict] | None = None,
) -> list[NeuronScoreBreakdown]:
    """Vectorized batch scoring using numpy — 10-50x faster than per-neuron loop.

    region_weights ({region: resolved weight dict}) switches the weight
    scalars to per-candidate arrays so each silo scores by its own epistemics.
    """
    if len(candidates) == 0:
        return []

    burst, impact, precision, novelty, recency = _compute_base_signals(
        candidates, total_queries, burst_map, neuron_fires_map, dept_total_map, last_offset_map,
    )
    relevance_arr = _resolve_relevance_arr(candidates, keywords, semantic_map, hybrid_map)
    coldstart_terms = _batch_coldstart_terms(candidates)
    w = _effective_weights(candidates, region_weights)
    combined = _gated_combined(
        relevance_arr, burst, impact, precision, novelty, recency, coldstart_terms, w,
    )

    # Classification boosts (region_match x 1.25, role_match x 1.5)
    dept_set = set(classified_departments) if classified_departments else set()
    role_set = set(classified_role_keys) if classified_role_keys else set()
    region_match = np.array([c.department in dept_set for c in candidates])
    role_match = np.array([c.role_key in role_set for c in candidates])
    combined = combined * np.where(role_match, 1.5, np.where(region_match, 1.25, 1.0))

    return _build_score_breakdowns(
        candidates, burst, impact, precision, novelty, recency, relevance_arr, combined,
    )


def _compute_edge_activation(
    source_activation: float,
    edge_weight: float,
    edge_type: str,
) -> float | None:
    """Return activation for an edge, or None if the edge should be skipped."""
    if edge_type == "stellate":
        decay = settings.spread_stellate_decay
    elif edge_type == "instantiates":
        decay = settings.spread_instantiate_decay
        if edge_weight < settings.spread_instantiate_min_weight:
            return None
    else:
        decay = settings.spread_decay
        if edge_weight < settings.spread_pyramidal_min_weight:
            return None
    activation = source_activation * edge_weight * decay
    if activation < settings.spread_min_activation:
        return None
    return activation


def _propagate_frontier(
    frontier: dict[int, float],
    adjacency: dict[int, list[tuple[int, float, str]]],
    top_k_ids: set[int],
    visited: set[int],
    neighbor_activation: dict[int, float],
) -> dict[int, float]:
    """Propagate activation from frontier through adjacency, return next frontier."""
    next_frontier: dict[int, float] = {}
    for source_id, source_act in frontier.items():
        for neighbor_id, edge_weight, edge_type in adjacency.get(source_id, []):
            activation = _compute_edge_activation(source_act, edge_weight, edge_type)
            if activation is None:
                continue
            if neighbor_id in top_k_ids:
                continue
            if neighbor_id not in neighbor_activation or activation > neighbor_activation[neighbor_id]:
                neighbor_activation[neighbor_id] = activation
            if neighbor_id not in visited:
                if neighbor_id not in next_frontier or activation > next_frontier[neighbor_id]:
                    next_frontier[neighbor_id] = activation
    return next_frontier


def _fetch_frontier_neighbors_cached(
    frontier_ids: set[int],
) -> dict[int, list[tuple[int, float, str]]]:
    """Fetch neighbors for frontier from in-memory adjacency cache."""
    from app.services.adjacency_cache import get_cached_neighbors
    return get_cached_neighbors(frontier_ids, settings.spread_min_edge_weight)


def _build_promoted_scores(
    promotions: list[tuple[int, float]],
    score_by_id: dict[int, NeuronScoreBreakdown],
) -> list[NeuronScoreBreakdown]:
    """Apply boosts to existing scores or create new entries for unscored neighbors."""
    promoted: list[NeuronScoreBreakdown] = []
    for nid, activation in promotions:
        if nid in score_by_id:
            existing = score_by_id[nid]
            existing.spread_boost = round(activation, 4)
            existing.combined = round(existing.combined + activation, 4)
            promoted.append(existing)
        else:
            promoted.append(NeuronScoreBreakdown(
                neuron_id=nid,
                burst=0.0,
                impact=0.0,
                precision=0.0,
                novelty=0.0,
                recency=0.0,
                relevance=0.0,
                combined=round(activation, 4),
                spread_boost=round(activation, 4),
            ))
    return promoted


def _merge_promoted_into_scored(
    top_k: list[NeuronScoreBreakdown],
    below_cutoff: list[NeuronScoreBreakdown],
    promoted_scores: list[NeuronScoreBreakdown],
    top_k_count: int,
) -> list[NeuronScoreBreakdown]:
    """Merge promoted neurons into scored list, displacing lowest top-K as needed."""
    promoted_scores.sort(key=lambda s: s.combined, reverse=True)
    promoted_ids = {s.neuron_id for s in promoted_scores}
    below_cutoff = [s for s in below_cutoff if s.neuron_id not in promoted_ids]
    merged = list(top_k) + promoted_scores
    merged.sort(key=lambda s: s.combined, reverse=True)
    new_top_k = merged[:top_k_count]
    displaced = merged[top_k_count:]
    return new_top_k + displaced + below_cutoff


async def _select_promotion_targets(
    db: AsyncSession,
    neighbor_activation: dict[int, float],
    requester=None,
) -> list[tuple[int, float]]:
    """Filter neighbors to active, requester-visible neurons, sorted by activation.

    ACL matters here: spread can traverse pyramidal edges INTO a restricted
    region — the edge is allowed (coordination), but promoting the restricted
    neuron into a non-member requester's context is not.
    """
    neighbor_ids = list(neighbor_activation.keys())
    params: dict = {"ids": neighbor_ids}
    acl_clause = await _acl_clause_for(db, requester, params)
    active_result = await db.execute(
        text(
            "SELECT id FROM neurons "
            f"WHERE id = ANY(:ids) AND is_active = true AND {acl_clause}"
        ),
        params,
    )
    active_ids = {row[0] for row in active_result.all()}

    promotions: list[tuple[int, float]] = []
    for nid, activation in sorted(neighbor_activation.items(), key=lambda x: x[1], reverse=True):
        if nid not in active_ids:
            continue
        promotions.append((nid, activation))
        if len(promotions) >= settings.spread_max_neurons:
            break
    return promotions


def _spread_neighbors_python(
    scored: list[NeuronScoreBreakdown], top_k_count: int,
) -> dict[int, float]:
    """Reference frontier-BFS spread. Returns {node_id: max activation}.

    The authoritative semantics: multi-hop, per-edge-type decay, MAX-across-paths,
    with `visited` gating re-propagation (not the running activation max) and
    top-k never promoted.
    """
    top_k = scored[:top_k_count]
    top_k_ids = {s.neuron_id for s in top_k}
    neighbor_activation: dict[int, float] = {}
    frontier: dict[int, float] = {s.neuron_id: s.combined for s in top_k}
    visited: set[int] = set(top_k_ids)
    for _hop in range(settings.spread_max_hops):
        frontier_id_set = set(frontier.keys())
        if not frontier_id_set:
            break
        adjacency = _fetch_frontier_neighbors_cached(frontier_id_set)
        if not adjacency:
            break
        next_frontier = _propagate_frontier(
            frontier, adjacency, top_k_ids, visited, neighbor_activation,
        )
        if not next_frontier:
            break
        visited.update(next_frontier.keys())
        frontier = next_frontier
    return neighbor_activation


def _spread_edge_gates(csr: dict) -> tuple:
    """Static per-edge decay array + weight-OK mask (mirrors _compute_edge_activation)."""
    etype = csr["etype"]
    decay = np.where(
        etype == 1, settings.spread_stellate_decay,
        np.where(etype == 2, settings.spread_instantiate_decay, settings.spread_decay),
    ).astype(np.float64)
    min_w = np.where(
        etype == 0, settings.spread_pyramidal_min_weight, settings.spread_min_edge_weight,
    ).astype(np.float64)
    weight_ok = csr["weight"] >= np.maximum(min_w, float(settings.spread_min_edge_weight))
    return decay, weight_ok


def _spread_seed_frontier(
    scored: list[NeuronScoreBreakdown], top_k_count: int, id2idx: dict, n: int,
) -> tuple:
    """Build top-k mask, visited mask, and the initial frontier index/activation arrays."""
    topk_mask = np.zeros(n, dtype=bool)
    visited = np.zeros(n, dtype=bool)
    fr_idx: list[int] = []
    fr_act: list[float] = []
    for s in scored[:top_k_count]:
        i = id2idx.get(s.neuron_id)
        if i is None:
            continue
        topk_mask[i] = True
        visited[i] = True
        fr_idx.append(i)
        fr_act.append(s.combined)
    return (topk_mask, visited,
            np.array(fr_idx, dtype=np.int64), np.array(fr_act, dtype=np.float64))


def _spread_neighbors_vectorized(
    scored: list[NeuronScoreBreakdown], top_k_count: int,
) -> dict[int, float]:
    """Vectorized frontier-BFS spread — numpy scatter-max over CSR frontier edges.

    Provably equivalent to _spread_neighbors_python: same hop count (spread_max_hops,
    no cap), the same per-edge decay/min-weight/min-activation rules, MAX-across-paths,
    and the same visited/top-k gating. Only the inner per-edge Python loop is replaced;
    all math is float64 to bit-match the reference at the min-activation boundary.
    """
    from app.services.adjacency_cache import get_adjacency_csr
    csr = get_adjacency_csr()
    if not csr or int(csr["id_list"].size) == 0:
        return {}
    id_list = csr["id_list"]
    indptr = csr["indptr"]
    indices = csr["indices"]
    weight = csr["weight"]
    n = int(id_list.size)
    decay, weight_ok = _spread_edge_gates(csr)
    min_act = float(settings.spread_min_activation)
    topk_mask, visited, frontier_idx, frontier_act = _spread_seed_frontier(
        scored, top_k_count, csr["id2idx"], n,
    )
    neighbor = np.zeros(n, dtype=np.float64)

    for _hop in range(settings.spread_max_hops):
        if frontier_idx.size == 0:
            break
        starts = indptr[frontier_idx]
        lengths = indptr[frontier_idx + 1] - starts
        total = int(lengths.sum())
        if total == 0:
            break
        base = np.repeat(starts, lengths)                       # ragged gather of
        within = np.arange(total) - np.repeat(np.cumsum(lengths) - lengths, lengths)
        epos = base + within                                    # frontier out-edges
        cand = np.repeat(frontier_act, lengths) * weight[epos] * decay[epos]
        valid = weight_ok[epos] & (cand >= min_act)
        dst_v = indices[epos][valid]
        cand_v = cand[valid]
        if dst_v.size == 0:
            break
        newact = np.zeros(n, dtype=np.float64)
        np.maximum.at(newact, dst_v, cand_v)         # MAX across paths this hop
        newact[topk_mask] = 0.0                       # top-k never promoted/re-propagated
        np.maximum(neighbor, newact, out=neighbor)    # running MAX across hops
        next_mask = (newact > 0.0) & (~visited)       # only newly-seen nodes re-propagate
        frontier_idx = np.where(next_mask)[0]
        frontier_act = newact[frontier_idx]
        visited |= next_mask

    nz = np.where(neighbor > 0.0)[0]
    return {int(id_list[i]): float(neighbor[i]) for i in nz}


async def spread_activation(
    db: AsyncSession,
    scored: list[NeuronScoreBreakdown],
    top_k_count: int,
    requester=None,
) -> list[NeuronScoreBreakdown]:
    """Multi-hop spread activation through NeuronEdge co-firing graph.

    Propagates activation from top-K neurons through high-weight edges to discover
    associatively-linked neurons, including "bridge" entities not directly connected
    to the query but reachable via intermediate nodes. Based on spreading activation
    theory from cognitive science (Collins & Loftus 1975) and adapted for KG-based
    RAG per SA-RAG (Pavlovic et al., arXiv:2512.15922, Dec 2025).

    Each hop compounds decay: hop-N activation = source_activation * edge_weight * decay.
    Uses max (not sum) across paths to prevent hub bias.
    """
    assert top_k_count > 0, f"top_k_count must be positive, got {top_k_count}"
    input_length = len(scored)

    if not settings.spread_enabled or not scored:
        return scored

    top_k = scored[:top_k_count]
    below_cutoff = scored[top_k_count:]
    score_by_id = {s.neuron_id: s for s in scored}

    # Multi-hop neighbor discovery (3+ hops, max-across-paths). The vectorized
    # path is the numpy scatter-max reimplementation of the same BFS; the Python
    # path is the reference. Both return {node_id: max activation}.
    if settings.spread_vectorized:
        neighbor_activation = _spread_neighbors_vectorized(scored, top_k_count)
    else:
        neighbor_activation = _spread_neighbors_python(scored, top_k_count)

    if not neighbor_activation:
        return scored

    promotions = await _select_promotion_targets(db, neighbor_activation, requester)
    if not promotions:
        return scored

    promoted_scores = _build_promoted_scores(promotions, score_by_id)
    result = _merge_promoted_into_scored(top_k, below_cutoff, promoted_scores, top_k_count)
    assert len(result) >= input_length, f"Spread activation lost neurons: {len(result)} < {input_length}"
    return result


def _parse_cross_ref_departments(cross_ref_rows: list) -> set[str]:
    """Extract union of department names from cross_ref_departments JSON rows."""
    cross_ref_depts: set[str] = set()
    for _, cross_ref_json in cross_ref_rows:
        try:
            depts = json.loads(cross_ref_json)
            cross_ref_depts.update(depts)
        except (json.JSONDecodeError, TypeError):
            pass
    return cross_ref_depts


def _find_underrepresented_depts(
    cross_ref_depts: set[str],
    dept_counts: dict[str, int],
    top_k_count: int,
) -> dict[str, int]:
    """Return {dept: slots_needed} for departments below the diversity floor."""
    assert len(cross_ref_depts) > 0, "cross_ref_depts must not be empty"
    floor = max(settings.diversity_floor_min, top_k_count // len(cross_ref_depts))
    underrepresented: dict[str, int] = {}
    for dept in cross_ref_depts:
        current = dept_counts.get(dept, 0)
        if current < floor:
            underrepresented[dept] = floor - current
    return underrepresented


def _collect_diversity_candidates(
    all_scored: list[NeuronScoreBreakdown],
    top_k_neuron_ids: set[int],
    all_neuron_dept: dict[int, str],
    underrepresented: dict[str, int],
) -> list[NeuronScoreBreakdown]:
    """Pick highest-scoring neurons from underrepresented depts not already in top-K."""
    candidates: list[NeuronScoreBreakdown] = []
    remaining = dict(underrepresented)
    for score in all_scored:
        if score.neuron_id in top_k_neuron_ids:
            continue
        dept = all_neuron_dept.get(score.neuron_id, "")
        if dept in remaining and remaining[dept] > 0:
            candidates.append(score)
            remaining[dept] -= 1
    return candidates


def _find_displacement_targets(
    top_k: list[NeuronScoreBreakdown],
    needed: int,
    regulatory_ids: set[int],
    boosted_depts: set[str],
    neuron_dept_map: dict[int, str],
) -> set[int]:
    """Select lowest-scoring top-K neurons to displace, avoiding regulatory and boosted."""
    assert needed > 0, "needed must be positive"
    displacement_candidates: list[NeuronScoreBreakdown] = []
    for s in reversed(top_k):
        dept = neuron_dept_map.get(s.neuron_id, "")
        if s.neuron_id not in regulatory_ids and dept not in boosted_depts:
            displacement_candidates.append(s)
        if len(displacement_candidates) >= needed:
            break

    if len(displacement_candidates) < needed:
        existing = set(id(s) for s in displacement_candidates)
        for s in reversed(top_k):
            if id(s) in existing:
                continue
            if s.neuron_id not in regulatory_ids:
                displacement_candidates.append(s)
            if len(displacement_candidates) >= needed:
                break

    return {s.neuron_id for s in displacement_candidates[:needed]}


async def _fetch_cross_ref_depts(
    db: AsyncSession,
    top_k_ids: list[int],
) -> tuple[list, set[str]]:
    """Fetch cross_ref_departments rows for top-K and return (rows, dept_union)."""
    result = await db.execute(
        select(Neuron.id, Neuron.cross_ref_departments)
        .where(
            Neuron.id.in_(top_k_ids),
            Neuron.cross_ref_departments.isnot(None),
        )
    )
    cross_ref_rows = result.all()
    cross_ref_depts = _parse_cross_ref_departments(cross_ref_rows) if cross_ref_rows else set()
    return cross_ref_rows, cross_ref_depts


async def _build_dept_maps(
    db: AsyncSession,
    neuron_ids: list[int],
) -> tuple[dict[int, str], dict[str, int]]:
    """Fetch department for each neuron_id, return (id->dept, dept->count)."""
    dept_result = await db.execute(
        select(Neuron.id, Neuron.department).where(Neuron.id.in_(neuron_ids))
    )
    neuron_dept_map: dict[int, str] = {}
    dept_counts: dict[str, int] = {}
    for nid, dept in dept_result.all():
        neuron_dept_map[nid] = dept or ""
        dept_counts[dept or ""] = dept_counts.get(dept or "", 0) + 1
    return neuron_dept_map, dept_counts


async def apply_diversity_floor(
    db: AsyncSession,
    all_scored: list[NeuronScoreBreakdown],
    top_k_count: int,
) -> list[NeuronScoreBreakdown]:
    """Ensure department diversity when regulatory neurons with cross_ref_departments fire.

    1. Take top-K from scored list
    2. Check which top-K neurons have cross_ref_departments set
    3. If none, return top-K unchanged
    4. Collect union of cross-referenced department names
    5. For underrepresented departments, pull in highest-scoring candidates from full list
    6. Displace lowest-scoring non-regulatory, non-underrepresented neurons
    """
    assert top_k_count > 0, f"top_k_count must be positive, got {top_k_count}"
    top_k = all_scored[:top_k_count]
    if not top_k:
        return top_k

    top_k_ids = [s.neuron_id for s in top_k]
    cross_ref_rows, cross_ref_depts = await _fetch_cross_ref_depts(db, top_k_ids)
    if not cross_ref_depts:
        return top_k

    neuron_dept_map, dept_counts = await _build_dept_maps(db, top_k_ids)

    underrepresented = _find_underrepresented_depts(cross_ref_depts, dept_counts, top_k_count)
    if not underrepresented:
        return top_k

    all_neuron_ids = [s.neuron_id for s in all_scored]
    all_dept_result = await db.execute(
        select(Neuron.id, Neuron.department).where(Neuron.id.in_(all_neuron_ids))
    )
    all_neuron_dept: dict[int, str] = {nid: (dept or "") for nid, dept in all_dept_result.all()}

    top_k_neuron_ids = set(top_k_ids)
    candidates_to_add = _collect_diversity_candidates(
        all_scored, top_k_neuron_ids, all_neuron_dept, underrepresented,
    )
    if not candidates_to_add:
        return top_k

    regulatory_ids = {nid for nid, _ in cross_ref_rows}
    displace_ids = _find_displacement_targets(
        top_k, len(candidates_to_add), regulatory_ids, set(cross_ref_depts), neuron_dept_map,
    )
    result_list = [s for s in top_k if s.neuron_id not in displace_ids]
    result_list.extend(candidates_to_add)
    result_list.sort(key=lambda s: s.combined, reverse=True)

    return result_list


async def _load_neuron_meta_cached(
    db: AsyncSession,
    nid: int,
    cache: dict[int, dict],
) -> dict | None:
    """Load a single neuron's parent/meta, with cache."""
    if nid in cache:
        return cache[nid]
    row = await db.execute(
        select(Neuron.id, Neuron.parent_id, Neuron.label, Neuron.department,
               Neuron.layer, Neuron.summary)
        .where(Neuron.id == nid)
    )
    r = row.one_or_none()
    if r is None:
        return None
    meta = {
        "parent_id": r[1], "label": r[2], "department": r[3],
        "layer": r[4], "summary": r[5],
    }
    cache[nid] = meta
    return meta


async def _walk_ancestor_chain(
    neuron_id: int,
    selected_ids: set[int],
    cache: dict[int, dict],
    db: AsyncSession,
) -> list[int]:
    """Walk up parent_id chain, stopping at root or an already-selected node."""
    max_depth = 8
    chain: list[int] = [neuron_id]
    current_id = neuron_id
    for _ in range(max_depth):
        meta = await _load_neuron_meta_cached(db, current_id, cache)
        if meta is None:
            break
        pid = meta["parent_id"]
        if pid is None or pid in selected_ids:
            break
        chain.append(pid)
        current_id = pid
    return chain


async def select_with_hierarchy(
    db: AsyncSession,
    scored: list[NeuronScoreBreakdown],
    budget: int,
) -> list[NeuronScoreBreakdown]:
    """Select neurons with complete ancestor chains for tree-structured activation.

    Instead of a flat top-K slice, picks the highest-scoring neurons and
    includes their full parent chains up to the root. This produces deep,
    narrow trees instead of a star pattern where everything connects to prompt.
    """
    assert budget > 0, f"budget must be positive, got {budget}"
    if not scored:
        return []

    score_by_id: dict[int, NeuronScoreBreakdown] = {s.neuron_id: s for s in scored}

    # Pre-load parent_id and metadata for all scored neurons in one batch
    scored_ids = [s.neuron_id for s in scored]
    result = await db.execute(
        select(Neuron.id, Neuron.parent_id, Neuron.label, Neuron.department,
               Neuron.layer, Neuron.summary)
        .where(Neuron.id.in_(scored_ids))
    )
    cache: dict[int, dict] = {}
    for nid, pid, label, dept, layer, summary in result.all():
        cache[nid] = {
            "parent_id": pid, "label": label, "department": dept,
            "layer": layer, "summary": summary,
        }

    selected_ids: set[int] = set()
    selected: list[NeuronScoreBreakdown] = []

    for s in scored:
        if s.neuron_id in selected_ids:
            continue
        chain_ids = await _walk_ancestor_chain(s.neuron_id, selected_ids, cache, db)
        new_ids = [nid for nid in chain_ids if nid not in selected_ids]
        if not new_ids:
            continue
        if len(selected_ids) + len(new_ids) > budget:
            if len(selected_ids) + 2 > budget:
                break
            new_ids = new_ids[:budget - len(selected_ids)]

        for nid in new_ids:
            selected_ids.add(nid)
            if nid in score_by_id:
                selected.append(score_by_id[nid])
            else:
                selected.append(NeuronScoreBreakdown(
                    neuron_id=nid, burst=0.0, impact=0.0, precision=0.0,
                    novelty=0.0, recency=0.0, relevance=0.0,
                    combined=0.0, spread_boost=0.0,
                ))

    selected.sort(key=lambda s: s.combined, reverse=True)
    return selected


async def record_firing(
    db: AsyncSession,
    neuron_id: int,
    query_id: int,
    global_token_offset: int,
    context_type: str = "direct",
    global_query_offset: int = 0,
    score: NeuronScoreBreakdown | None = None,
    rank: int | None = None,
    prompt_position: int | None = None,
    was_included: bool | None = None,
) -> NeuronFiring:
    """Record a neuron firing event with optional score breakdown."""
    assert neuron_id > 0, f"neuron_id must be positive, got {neuron_id}"
    assert query_id > 0, f"query_id must be positive, got {query_id}"
    firing = NeuronFiring(
        neuron_id=neuron_id,
        query_id=query_id,
        context_type=context_type,
        global_token_offset=global_token_offset,
        global_query_offset=global_query_offset,
        rank=rank,
        combined_score=score.combined if score else None,
        burst=score.burst if score else None,
        impact=score.impact if score else None,
        precision=score.precision if score else None,
        novelty=score.novelty if score else None,
        recency=score.recency if score else None,
        relevance=score.relevance if score else None,
        spread_boost=score.spread_boost if score else None,
        prompt_position=prompt_position,
        was_included=was_included,
    )
    db.add(firing)

    neuron = await db.get(Neuron, neuron_id)
    if neuron:
        neuron.invocations = (neuron.invocations or 0) + 1
        neuron.last_accessed_at = firing.created_at

    return firing


async def get_neuron_tree(
    db: AsyncSession,
    department: str | None = None,
    role_key: str | None = None,
    max_depth: int | None = None,
) -> list[dict]:
    """Build nested tree structure for neurons.

    If max_depth is set, only builds tree to that depth (0=roots only, 2=roots+children+grandchildren).
    At 200K neurons, callers should use max_depth=2 or the /neurons/children endpoint.
    """
    if max_depth is not None:
        assert 0 <= max_depth <= 20, f"max_depth must be in [0, 20], got {max_depth}"

    conditions = [Neuron.layer >= 0]  # Exclude concept neurons (layer=-1); shown separately
    if department:
        conditions.append(Neuron.department == department)
    if role_key:
        conditions.append(Neuron.role_key == role_key)
    if max_depth is not None:
        conditions.append(Neuron.layer <= max_depth)

    stmt = select(Neuron).where(*conditions)
    stmt = stmt.order_by(Neuron.layer, Neuron.id)
    result = await db.execute(stmt)
    all_neurons = list(result.scalars().all())

    # Build parent→children map
    children_map: dict[int | None, list[Neuron]] = {}
    for n in all_neurons:
        children_map.setdefault(n.parent_id, []).append(n)

    def build_node(neuron: Neuron) -> dict:
        """Iterative tree builder — no recursion (JPL-1)."""
        max_depth = 10
        root_node: dict = {}
        # Stack: (neuron, depth, parent_dict) — build nodes breadth-first via stack
        stack: list[tuple[Neuron, int, dict | None]] = [(neuron, 0, None)]
        while stack:
            cur, depth, parent = stack.pop()
            assert depth <= max_depth, f"build_node exceeded max depth {max_depth}"
            node = {
                "id": cur.id,
                "layer": cur.layer,
                "node_type": cur.node_type,
                "label": cur.label,
                "department": cur.department,
                "role_key": cur.role_key,
                "invocations": cur.invocations,
                "avg_utility": cur.avg_utility,
            }
            if parent is None:
                root_node = node
            else:
                parent.setdefault("children", []).append(node)
            kids = children_map.get(cur.id, [])
            if kids and depth < max_depth:
                for child in reversed(kids):
                    stack.append((child, depth + 1, node))
        return root_node

    roots = children_map.get(None, [])
    return [build_node(r) for r in roots]


async def get_graph_stats(db: AsyncSession) -> dict:
    """Get neuron graph statistics."""
    total = (await db.execute(select(func.count(Neuron.id)))).scalar() or 0

    by_layer = {}
    for layer in range(-1, 6):
        count = (await db.execute(
            select(func.count(Neuron.id)).where(Neuron.layer == layer)
        )).scalar() or 0
        if count > 0 or layer >= 0:
            by_layer[f"layer_{layer}"] = count

    by_type = {}
    type_result = await db.execute(
        select(Neuron.node_type, func.count(Neuron.id)).group_by(Neuron.node_type)
    )
    for node_type, count in type_result.all():
        by_type[node_type] = count

    dept_result = await db.execute(
        select(Neuron.department, func.count(Neuron.id))
        .where(Neuron.department.isnot(None))
        .group_by(Neuron.department)
    )
    by_dept = {dept: count for dept, count in dept_result.all()}

    # Per-department role breakdown: { dept: { role_label: count } }
    # Get L1 role neurons and count all descendants per role
    from sqlalchemy import text
    role_breakdown_result = await db.execute(text("""
        SELECT r.department, r.label, COUNT(n.id)
        FROM neurons r
        JOIN neurons n ON n.department = r.department
            AND n.role_key = r.role_key
        WHERE r.layer = 1
            AND r.department IS NOT NULL
        GROUP BY r.department, r.label
        ORDER BY r.department, COUNT(n.id) DESC
    """))
    by_dept_roles: dict[str, dict[str, int]] = {}
    for dept, role_label, count in role_breakdown_result.all():
        by_dept_roles.setdefault(dept, {})[role_label] = count

    total_firings = (await db.execute(select(func.count(NeuronFiring.id)))).scalar() or 0

    # Role bubble data: neuron_count, total_invocations, avg_utility per L1 role
    bubble_result = await db.execute(text("""
        SELECT r.label, r.department,
               COUNT(n.id) AS neuron_count,
               SUM(n.invocations) AS total_invocations,
               AVG(n.avg_utility) AS avg_utility
        FROM neurons r
        JOIN neurons n ON n.department = r.department
            AND n.role_key = r.role_key
        WHERE r.layer = 1
            AND r.department IS NOT NULL
        GROUP BY r.label, r.department
        ORDER BY COUNT(n.id) DESC
    """))
    role_bubbles = [
        {
            "role": role, "department": dept,
            "neuron_count": count,
            "total_invocations": int(invoc or 0),
            "avg_utility": round(float(util or 0.5), 3),
        }
        for role, dept, count, invoc, util in bubble_result.all()
    ]

    return {
        "total_neurons": total,
        "by_layer": by_layer,
        "by_type": by_type,
        "by_department": by_dept,
        "by_department_roles": by_dept_roles,
        "role_bubbles": role_bubbles,
        "total_firings": total_firings,
    }
