"""Action: edge.link — assert a NeuronEdge with tiered storage.

Wraps the logic from `backend/app/routers/proposals.py:_apply_link_item` and
also covers the admin ingest edge-creation path. Edges above the promotion
threshold go into the neuron_edges table; below it they get stored as JSONB
in the holder neuron's weak_edges column.
"""

from __future__ import annotations

from typing import Any

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
    from app.services.edge_tier import (
        is_promoted, upsert_weak_edge, delete_weak_edge,
    )

    promoted = is_promoted(payload.weight, payload.co_fire_count)

    existing = None
    retyped = False
    if promoted:
        existing = await db.get(
            NeuronEdge, (payload.source_id, payload.target_id),
        )
        if existing is None:
            edge = NeuronEdge(
                source_id=payload.source_id,
                target_id=payload.target_id,
                weight=payload.weight,
                co_fire_count=payload.co_fire_count,
                edge_type=payload.edge_type,
                source=payload.source,
                context=payload.context,
                last_updated_query=payload.last_updated_query,
            )
            db.add(edge)
        elif payload.edge_type in {"supersedes", "scoped-by", "evidence-link"}:
            retyped = existing.edge_type != payload.edge_type
            existing.weight = payload.weight
            existing.co_fire_count = max(
                existing.co_fire_count or 0, payload.co_fire_count,
            )
            existing.edge_type = payload.edge_type
            existing.source = payload.source
            existing.context = payload.context
            existing.last_updated_query = payload.last_updated_query
        elif existing.edge_type == payload.edge_type:
            # Exact repeated topology assertions are harmless and should not
            # fail a larger proposal transaction.
            existing.weight = max(existing.weight or 0.0, payload.weight)
            existing.co_fire_count = max(
                existing.co_fire_count or 0, payload.co_fire_count,
            )
            existing.context = payload.context or existing.context
        await delete_weak_edge(db, payload.source_id, payload.target_id)
    else:
        data = {
            "w": payload.weight,
            "t": payload.edge_type,
            "c": payload.co_fire_count,
            "s": payload.source,
            "q": payload.last_updated_query,
        }
        await upsert_weak_edge(
            db, payload.source_id, payload.target_id, data,
        )

    return {
        "audit": {
            "source_id": payload.source_id,
            "target_id": payload.target_id,
            "weight": payload.weight,
            "promoted": promoted,
            "existing": existing is not None,
            "retyped": retyped,
        },
        "payload": {
            "promoted": promoted,
            "existing": existing is not None,
            "retyped": retyped,
        },
    }
