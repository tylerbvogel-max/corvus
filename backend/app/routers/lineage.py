"""Lineage endpoints — bidirectional provenance queries (AIP Pattern #2).

Forward chain: source document → neurons → firings → queries → answers.
Backward chain (for a query): answer → fired neurons → source documents → actions.
"""

from fastapi import APIRouter, Depends, Query as QueryParam
from pydantic import BaseModel
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    Neuron,
    NeuronFiring,
    NeuronScoreOverride,
    NeuronSourceLink,
    Query,
    SourceDocument,
)

router = APIRouter(prefix="/lineage", tags=["lineage"])


# --- Response schemas ---


class FiringDetail(BaseModel):
    firing_id: int
    query_id: int | None
    rank: int | None
    combined_score: float | None
    burst: float | None
    impact: float | None
    precision: float | None
    novelty: float | None
    recency: float | None
    relevance: float | None
    spread_boost: float | None
    was_included: bool | None
    created_at: str


class NeuronForwardResult(BaseModel):
    neuron_id: int
    neuron_label: str
    total_firings: int
    firings: list[FiringDetail]


class SourceImpactNeuron(BaseModel):
    neuron_id: int
    neuron_label: str
    derivation_type: str
    firing_count: int


class SourceImpactResult(BaseModel):
    source_id: int
    canonical_id: str
    linked_neurons: list[SourceImpactNeuron]
    total_query_citations: int


class TraceFiring(BaseModel):
    neuron_id: int
    neuron_label: str
    department: str | None
    layer: int
    rank: int | None
    combined_score: float | None
    was_included: bool | None
    source_documents: list[str]
    actions_count: int


class QueryTraceResult(BaseModel):
    query_id: int
    user_message: str
    created_at: str
    fired_neurons: list[TraceFiring]


class OverrideOut(BaseModel):
    id: int
    neuron_id: int
    signal: str
    floor: float | None
    ceiling: float | None
    multiplier: float | None
    reason: str | None
    created_by: str | None
    is_active: bool
    created_at: str


class OverrideIn(BaseModel):
    signal: str
    floor: float | None = None
    ceiling: float | None = None
    multiplier: float | None = None
    reason: str | None = None
    created_by: str | None = None


# --- Endpoints ---


@router.get("/neuron/{neuron_id}/forward", response_model=NeuronForwardResult)
async def neuron_forward_lineage(
    neuron_id: int,
    limit: int = QueryParam(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """What queries used this neuron? Returns recent firings with scores."""
    neuron = await db.get(Neuron, neuron_id)
    if not neuron:
        from fastapi import HTTPException
        raise HTTPException(404, f"Neuron {neuron_id} not found")

    total = (await db.execute(
        select(func.count(NeuronFiring.id)).where(
            NeuronFiring.neuron_id == neuron_id,
        )
    )).scalar() or 0

    result = await db.execute(
        select(NeuronFiring)
        .where(NeuronFiring.neuron_id == neuron_id)
        .order_by(desc(NeuronFiring.created_at))
        .limit(limit)
    )
    firings = [
        FiringDetail(
            firing_id=f.id,
            query_id=f.query_id,
            rank=f.rank,
            combined_score=f.combined_score,
            burst=f.burst,
            impact=f.impact,
            precision=f.precision,
            novelty=f.novelty,
            recency=f.recency,
            relevance=f.relevance,
            spread_boost=f.spread_boost,
            was_included=f.was_included,
            created_at=f.created_at.isoformat() if f.created_at else "",
        )
        for f in result.scalars().all()
    ]
    return NeuronForwardResult(
        neuron_id=neuron_id,
        neuron_label=neuron.label,
        total_firings=total,
        firings=firings,
    )


@router.get("/source/{source_id}/impact", response_model=SourceImpactResult)
async def source_impact(
    source_id: int,
    db: AsyncSession = Depends(get_db),
):
    """What queries were influenced by this source document?

    Joins: SourceDocument → NeuronSourceLink → Neuron → NeuronFiring → Query.
    """
    doc = await db.get(SourceDocument, source_id)
    if not doc:
        from fastapi import HTTPException
        raise HTTPException(404, f"Source document {source_id} not found")

    # Get linked neurons with their firing counts
    result = await db.execute(
        select(
            NeuronSourceLink.neuron_id,
            Neuron.label,
            NeuronSourceLink.derivation_type,
            func.count(func.distinct(NeuronFiring.query_id)).label("fire_count"),
        )
        .join(Neuron, Neuron.id == NeuronSourceLink.neuron_id)
        .outerjoin(NeuronFiring, NeuronFiring.neuron_id == NeuronSourceLink.neuron_id)
        .where(NeuronSourceLink.source_document_id == source_id)
        .group_by(
            NeuronSourceLink.neuron_id,
            Neuron.label,
            NeuronSourceLink.derivation_type,
        )
    )
    rows = result.all()
    neurons = [
        SourceImpactNeuron(
            neuron_id=r.neuron_id,
            neuron_label=r.label,
            derivation_type=r.derivation_type,
            firing_count=r.fire_count,
        )
        for r in rows
    ]
    total_citations = sum(n.firing_count for n in neurons)

    return SourceImpactResult(
        source_id=source_id,
        canonical_id=doc.canonical_id,
        linked_neurons=neurons,
        total_query_citations=total_citations,
    )


async def _load_trace_context(
    db: AsyncSession, neuron_ids: list[int],
) -> tuple[dict[int, Neuron], dict[int, list[str]], dict[int, int]]:
    """Batch-load neurons, source doc links, and action counts for a trace."""
    neurons_map: dict[int, Neuron] = {}
    source_links: dict[int, list[str]] = {}
    action_counts: dict[int, int] = {}
    if not neuron_ids:
        return neurons_map, source_links, action_counts

    neurons_result = await db.execute(
        select(Neuron).where(Neuron.id.in_(neuron_ids))
    )
    neurons_map = {n.id: n for n in neurons_result.scalars().all()}

    links_result = await db.execute(
        select(NeuronSourceLink.neuron_id, SourceDocument.canonical_id)
        .join(SourceDocument, SourceDocument.id == NeuronSourceLink.source_document_id)
        .where(NeuronSourceLink.neuron_id.in_(neuron_ids))
    )
    for nid, canonical in links_result.all():
        source_links.setdefault(nid, []).append(canonical)

    from sqlalchemy import text
    nid_list = ",".join(str(n) for n in neuron_ids)
    actions_result = await db.execute(text(
        f"SELECT nid, count(*) FROM ("
        f"  SELECT (input_json->>'target_neuron_id')::int AS nid FROM actions"
        f"  WHERE input_json->>'target_neuron_id' IS NOT NULL"
        f"  AND (input_json->>'target_neuron_id')::int IN ({nid_list})"
        f"  UNION ALL"
        f"  SELECT (result_json->>'created_neuron_id')::int AS nid FROM actions"
        f"  WHERE result_json->>'created_neuron_id' IS NOT NULL"
        f"  AND (result_json->>'created_neuron_id')::int IN ({nid_list})"
        f") sub GROUP BY nid"
    ))
    action_counts = dict(actions_result.all())
    return neurons_map, source_links, action_counts


@router.get("/query/{query_id}/trace", response_model=QueryTraceResult)
async def query_trace(
    query_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Full provenance trace for a query answer."""
    query = await db.get(Query, query_id)
    if not query:
        from fastapi import HTTPException
        raise HTTPException(404, f"Query {query_id} not found")

    firings_result = await db.execute(
        select(NeuronFiring)
        .where(NeuronFiring.query_id == query_id)
        .order_by(NeuronFiring.rank.nulls_last())
    )
    firings = firings_result.scalars().all()
    neuron_ids = [f.neuron_id for f in firings]
    neurons_map, source_links, action_counts = await _load_trace_context(db, neuron_ids)

    trace_firings = []
    for f in firings:
        neuron = neurons_map.get(f.neuron_id)
        trace_firings.append(TraceFiring(
            neuron_id=f.neuron_id,
            neuron_label=neuron.label if neuron else f"[deleted:{f.neuron_id}]",
            department=neuron.department if neuron else None,
            layer=neuron.layer if neuron else 0,
            rank=f.rank,
            combined_score=f.combined_score,
            was_included=f.was_included,
            source_documents=source_links.get(f.neuron_id, []),
            actions_count=action_counts.get(f.neuron_id, 0),
        ))

    return QueryTraceResult(
        query_id=query_id,
        user_message=query.user_message,
        created_at=query.created_at.isoformat() if query.created_at else "",
        fired_neurons=trace_firings,
    )


# --- Score override CRUD ---


@router.get(
    "/neuron/{neuron_id}/overrides",
    response_model=list[OverrideOut],
)
async def list_overrides(
    neuron_id: int,
    db: AsyncSession = Depends(get_db),
):
    """List all score overrides for a neuron."""
    result = await db.execute(
        select(NeuronScoreOverride)
        .where(NeuronScoreOverride.neuron_id == neuron_id)
        .order_by(NeuronScoreOverride.signal)
    )
    return [
        OverrideOut(
            id=o.id,
            neuron_id=o.neuron_id,
            signal=o.signal,
            floor=o.floor,
            ceiling=o.ceiling,
            multiplier=o.multiplier,
            reason=o.reason,
            created_by=o.created_by,
            is_active=o.is_active,
            created_at=o.created_at.isoformat() if o.created_at else "",
        )
        for o in result.scalars().all()
    ]


_VALID_SIGNALS = frozenset({
    "burst", "impact", "precision", "novelty",
    "recency", "relevance", "combined",
})


def _override_to_out(ov: NeuronScoreOverride) -> OverrideOut:
    return OverrideOut(
        id=ov.id, neuron_id=ov.neuron_id, signal=ov.signal,
        floor=ov.floor, ceiling=ov.ceiling, multiplier=ov.multiplier,
        reason=ov.reason, created_by=ov.created_by, is_active=ov.is_active,
        created_at=ov.created_at.isoformat() if ov.created_at else "",
    )


@router.post(
    "/neuron/{neuron_id}/overrides",
    response_model=OverrideOut,
    status_code=201,
)
async def create_override(
    neuron_id: int,
    body: OverrideIn,
    db: AsyncSession = Depends(get_db),
):
    """Create or update a score override for a neuron signal."""
    if body.signal not in _VALID_SIGNALS:
        from fastapi import HTTPException
        raise HTTPException(400, f"Invalid signal: {body.signal}")

    neuron = await db.get(Neuron, neuron_id)
    if not neuron:
        from fastapi import HTTPException
        raise HTTPException(404, f"Neuron {neuron_id} not found")

    existing = (await db.execute(
        select(NeuronScoreOverride).where(
            NeuronScoreOverride.neuron_id == neuron_id,
            NeuronScoreOverride.signal == body.signal,
        )
    )).scalar_one_or_none()

    if existing:
        existing.floor = body.floor
        existing.ceiling = body.ceiling
        existing.multiplier = body.multiplier
        existing.reason = body.reason
        existing.created_by = body.created_by
        existing.is_active = True
        await db.commit()
        await db.refresh(existing)
        return _override_to_out(existing)

    ov = NeuronScoreOverride(
        neuron_id=neuron_id, signal=body.signal,
        floor=body.floor, ceiling=body.ceiling, multiplier=body.multiplier,
        reason=body.reason, created_by=body.created_by,
    )
    db.add(ov)
    await db.commit()
    await db.refresh(ov)
    return _override_to_out(ov)


@router.delete("/overrides/{override_id}", status_code=204)
async def delete_override(
    override_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Deactivate a score override (soft delete)."""
    ov = await db.get(NeuronScoreOverride, override_id)
    if not ov:
        from fastapi import HTTPException
        raise HTTPException(404, f"Override {override_id} not found")
    ov.is_active = False
    await db.commit()
