"""Engram CRUD, candidate scoring, and co-firing management.

Engrams are retrieval indices for external authoritative sources.  They
participate in the same 6-signal scoring pipeline as neurons but have no
department, role, or hierarchy.  Their ranking comes from relevance alone.
"""

import json
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Engram, EngramFiring, EngramEdge, SystemState
from app.services.scoring_engine import (
    NeuronScoreBreakdown,
    calc_burst_batch, calc_impact_batch, calc_precision_batch,
    calc_novelty_batch, calc_recency_batch,
)


@dataclass
class EngramCandidate:
    """Lightweight engram representation for scoring."""
    id: int
    label: str
    summary: str | None
    avg_utility: float
    invocations: int
    created_at_query_count: int
    keyword_hits: int = 0
    # Scoring compatibility — engrams have no department/role
    department: str | None = None
    role_key: str | None = None


async def get_engram(db: AsyncSession, engram_id: int) -> Engram | None:
    """Fetch a single engram by ID."""
    return await db.get(Engram, engram_id)


async def list_engrams(db: AsyncSession, active_only: bool = True) -> list[Engram]:
    """List all engrams, optionally filtered to active only."""
    stmt = select(Engram)
    if active_only:
        stmt = stmt.where(Engram.is_active == True)  # noqa: E712
    result = await db.execute(stmt.order_by(Engram.id))
    return list(result.scalars().all())


async def get_engram_candidates(
    db: AsyncSession,
    keywords: list[str] | None = None,
) -> list[EngramCandidate]:
    """Pre-filter engram candidates, optionally by keyword match."""
    stmt = select(
        Engram.id, Engram.label, Engram.summary,
        Engram.avg_utility, Engram.invocations,
        Engram.created_at_query_count,
    ).where(Engram.is_active == True)  # noqa: E712

    rows = (await db.execute(stmt)).all()

    candidates = []
    for row in rows:
        hits = 0
        if keywords:
            search_text = f"{row.label} {row.summary or ''}".lower()
            for kw in keywords:
                if kw.lower() in search_text:
                    hits += 1

        candidates.append(EngramCandidate(
            id=row.id,
            label=row.label,
            summary=row.summary,
            avg_utility=row.avg_utility,
            invocations=row.invocations,
            created_at_query_count=row.created_at_query_count,
            keyword_hits=hits,
        ))
    return candidates


async def _fetch_engram_fire_stats(
    db: AsyncSession,
    ids: list[int],
    total_queries: int,
) -> tuple[dict[int, int], dict[int, tuple[int, int]]]:
    """Fetch burst counts and total firing stats for engram candidates."""
    burst_window_start = max(0, total_queries - settings.burst_window_queries)
    burst_stmt = text(
        "SELECT engram_id, COUNT(*) "
        "FROM engram_firings "
        "WHERE engram_id = ANY(:ids) AND global_query_offset >= :start "
        "GROUP BY engram_id"
    )
    burst_rows = (await db.execute(burst_stmt, {"ids": ids, "start": burst_window_start})).all()
    burst_map = {row[0]: row[1] for row in burst_rows}

    fire_stmt = text(
        "SELECT engram_id, COUNT(*), MAX(global_query_offset) "
        "FROM engram_firings "
        "WHERE engram_id = ANY(:ids) "
        "GROUP BY engram_id"
    )
    fire_rows = (await db.execute(fire_stmt, {"ids": ids})).all()
    fire_map = {row[0]: (row[1], row[2]) for row in fire_rows}

    return burst_map, fire_map


async def score_engram_candidates(
    db: AsyncSession,
    candidates: list[EngramCandidate],
    total_queries: int,
    keywords: list[str],
    precomputed_similarities: dict[int, float] | None = None,
) -> list[NeuronScoreBreakdown]:
    """Score engram candidates using the same 6-signal engine as neurons.

    Engrams get zero classification boost (no department/role match).
    Their ranking comes from relevance + modulatory signals only.
    """
    if not candidates:
        return []

    n = len(candidates)
    ids = [c.id for c in candidates]
    burst_map, fire_map = await _fetch_engram_fire_stats(db, ids, total_queries)

    # Build arrays for vectorized scoring
    burst_arr = np.array([burst_map.get(c.id, 0) for c in candidates], dtype=np.float64)
    impact_arr = np.array([c.avg_utility for c in candidates], dtype=np.float64)
    # Engrams have no dept stats — use zeros to trigger the <5 queries floor (0.3)
    precision_fires = np.zeros(n, dtype=np.float64)
    precision_totals = np.zeros(n, dtype=np.float64)
    age_arr = np.array([max(0, total_queries - c.created_at_query_count) for c in candidates], dtype=np.float64)
    last_fire_arr = np.array([
        max(0, total_queries - fire_map.get(c.id, (0, 0))[1])
        for c in candidates
    ], dtype=np.float64)

    # Compute signals
    bursts = calc_burst_batch(burst_arr)
    impacts = calc_impact_batch(impact_arr)
    precisions = calc_precision_batch(precision_fires, precision_totals)
    novelties = calc_novelty_batch(age_arr)
    recencies = calc_recency_batch(last_fire_arr)

    # Relevance from semantic similarity (precomputed) or keyword hits
    relevances = np.zeros(n, dtype=np.float64)
    for i, c in enumerate(candidates):
        if precomputed_similarities and c.id in precomputed_similarities:
            relevances[i] = precomputed_similarities[c.id]
        elif c.keyword_hits > 0:
            relevances[i] = min(1.0, c.keyword_hits * 0.2)

    # Gated combined score (same formula as neurons, no classification boost)
    w = settings
    stimulus = w.weight_relevance * relevances
    modulatory = (
        w.weight_burst * bursts
        + w.weight_impact * impacts
        + w.weight_precision * precisions
        + w.weight_novelty * novelties
        + w.weight_recency * recencies
    )
    gate = np.where(
        relevances >= w.relevance_gate_threshold,
        1.0,
        np.where(
            relevances > 0,
            w.relevance_gate_floor + (1.0 - w.relevance_gate_floor) * (relevances / w.relevance_gate_threshold),
            w.relevance_gate_floor,
        ),
    )
    combined = stimulus + modulatory * gate

    results = []
    for i, c in enumerate(candidates):
        results.append(NeuronScoreBreakdown(
            neuron_id=c.id,
            burst=float(bursts[i]),
            impact=float(impacts[i]),
            precision=float(precisions[i]),
            novelty=float(novelties[i]),
            recency=float(recencies[i]),
            relevance=float(relevances[i]),
            combined=float(combined[i]),
            entity_type="engram",
        ))

    results.sort(key=lambda s: s.combined, reverse=True)
    return results


async def record_engram_firings(
    db: AsyncSession,
    engram_ids: list[int],
    query_id: int | None,
    global_query_offset: int,
) -> None:
    """Record firing events for engrams activated in a query."""
    for eid in engram_ids:
        db.add(EngramFiring(
            engram_id=eid,
            query_id=query_id,
            global_query_offset=global_query_offset,
        ))
    # Update invocation counts
    if engram_ids:
        await db.execute(text(
            "UPDATE engrams SET invocations = invocations + 1 "
            "WHERE id = ANY(:ids)"
        ), {"ids": engram_ids})


async def record_engram_cofiring(
    db: AsyncSession,
    fired_engram_ids: list[int],
    fired_neuron_ids: list[int],
    query_offset: int,
) -> None:
    """Record engram<->neuron co-firing as a single batched upsert.

    New pair: weight 0.1, count 1. Repeat: count +1, weight +0.05 (cap 1.0).
    One round-trip regardless of pair count (was a per-pair SELECT+upsert loop)."""
    if not fired_engram_ids or not fired_neuron_ids:
        return
    pairs = [
        {"eid": int(e), "nid": int(n), "q": int(query_offset)}
        for e in fired_engram_ids for n in fired_neuron_ids
    ]
    await db.execute(text(
        "INSERT INTO engram_edges "
        "(engram_id, neuron_id, co_fire_count, weight, edge_type, source, last_updated_query) "
        "VALUES (:eid, :nid, 1, 0.1, 'regulatory', 'organic', :q) "
        "ON CONFLICT (engram_id, neuron_id) DO UPDATE SET "
        "co_fire_count = engram_edges.co_fire_count + 1, "
        "weight = LEAST(1.0, engram_edges.weight + 0.05), "
        "last_updated_query = EXCLUDED.last_updated_query"
    ), pairs)
