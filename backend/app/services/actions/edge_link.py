"""Action: edge.link — assert a NeuronEdge with tiered storage.

Wraps the logic from `backend/app/routers/proposals.py:_apply_link_item` and
also covers the admin ingest edge-creation path. Edges above the promotion
threshold go into the neuron_edges table; below it they get stored as JSONB
in the holder neuron's weak_edges column.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.rbac import UserIdentity
from app.models import Action, NeuronEdge


class EdgeLinkInput(BaseModel):
    source_id: int
    target_id: int
    weight: float = Field(0.15, ge=0.0, le=1.0)
    co_fire_count: int = Field(1, ge=0)
    edge_type: str = Field("pyramidal", max_length=50)
    source: str = Field("integrity_completion", max_length=80)
    context: str = ""
    last_updated_query: int = 0
    # Authoritative topology assertions (for example concept instantiation)
    # must remain in the durable edge table even before organic co-firing has
    # met the normal promotion threshold. Derived learning keeps "auto".
    storage_tier: Literal["auto", "promoted"] = "auto"


_AUTHORITATIVE_RELATIONSHIP_TYPES = frozenset({
    "supersedes", "scoped-by", "evidence-link", "instantiates",
})


def _apply_authoritative_relationship(existing, payload: EdgeLinkInput) -> bool:
    """Retype one durable relationship while preserving concept strength."""
    retyped = existing.edge_type != payload.edge_type
    existing.weight = (
        max(existing.weight or 0.0, payload.weight)
        if payload.edge_type == "instantiates"
        else payload.weight
    )
    existing.co_fire_count = max(
        existing.co_fire_count or 0, payload.co_fire_count,
    )
    existing.edge_type = payload.edge_type
    existing.source = payload.source
    existing.context = payload.context
    existing.last_updated_query = payload.last_updated_query
    return retyped


async def _assert_promoted(
    payload: EdgeLinkInput,
    db: AsyncSession,
) -> tuple[NeuronEdge | None, bool]:
    """Insert or reconcile one promoted edge and remove any weak duplicate."""
    from app.services.edge_tier import delete_weak_edge

    existing = await db.get(
        NeuronEdge, (payload.source_id, payload.target_id),
    )
    retyped = False
    if existing is None:
        db.add(NeuronEdge(
            source_id=payload.source_id,
            target_id=payload.target_id,
            weight=payload.weight,
            co_fire_count=payload.co_fire_count,
            edge_type=payload.edge_type,
            source=payload.source,
            context=payload.context,
            last_updated_query=payload.last_updated_query,
        ))
    elif payload.edge_type in _AUTHORITATIVE_RELATIONSHIP_TYPES:
        retyped = _apply_authoritative_relationship(existing, payload)
    elif existing.edge_type == payload.edge_type:
        existing.weight = max(existing.weight or 0.0, payload.weight)
        existing.co_fire_count = max(
            existing.co_fire_count or 0, payload.co_fire_count,
        )
        existing.context = payload.context or existing.context
    await delete_weak_edge(db, payload.source_id, payload.target_id)
    return existing, retyped


async def _assert_weak(payload: EdgeLinkInput, db: AsyncSession) -> None:
    """Upsert one derived edge into the weak JSONB tier."""
    from app.services.edge_tier import upsert_weak_edge

    await upsert_weak_edge(
        db,
        payload.source_id,
        payload.target_id,
        {
            "w": payload.weight,
            "t": payload.edge_type,
            "c": payload.co_fire_count,
            "s": payload.source,
            "q": payload.last_updated_query,
        },
    )


async def handle_edge_link(
    payload: EdgeLinkInput,
    actor: UserIdentity,
    db: AsyncSession,
    action_row: Action,
) -> dict[str, Any]:
    """Assert an edge, routing to promoted table or weak_edges JSONB.

    Promoted edges use ``(source_id, target_id)`` as their primary key, so
    repeated assertions must be idempotent.  A memory-semantics assertion
    (supersedes/scoped-by/evidence-link) is stronger than a pre-existing
    activation edge: retype that row in place so a fused-memory provenance
    link cannot continue conducting spread activation.
    """
    from app.services.edge_tier import is_promoted

    promoted = (
        payload.storage_tier == "promoted"
        or is_promoted(payload.weight, payload.co_fire_count)
    )

    existing = None
    retyped = False
    if promoted:
        existing, retyped = await _assert_promoted(payload, db)
    else:
        await _assert_weak(payload, db)

    return {
        "audit": {
            "source_id": payload.source_id,
            "target_id": payload.target_id,
            "weight": payload.weight,
            "promoted": promoted,
            "storage_tier": payload.storage_tier,
            "existing": existing is not None,
            "retyped": retyped,
        },
        "payload": {
            "promoted": promoted,
            "storage_tier": payload.storage_tier,
            "existing": existing is not None,
            "retyped": retyped,
        },
    }
