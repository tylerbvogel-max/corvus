"""Action: edge.rewire — execute one component's deterministic rewiring.

The apply half of kernel Phase 3: retire every conducting edge internal
to the component or touching a member, and materialize the synthesis's
external edges exactly as the approved RewiringPreview recomputed them
from union co-fire evidence. All decisions were made by
reconsolidation.rewiring.plan_rewire_ops (pure, unit-tested); this
handler is a thin executor whose audit records every row it removed —
retired topology stays inspectable even though the rows are gone.

Idempotent: re-running against already-rewired state plans zero deletes
and re-asserts identical upserts.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.rbac import UserIdentity
from app.models import Action, NeuronEdge

MAX_AUDIT_ROWS = 60  # audit JSON stays bounded; counts are always exact


class PeerRewireSpec(BaseModel):
    peer_id: int
    union_cofire_queries: int = Field(..., ge=0)
    recomputed_weight: float = Field(..., ge=0.0, le=1.0)
    edge_type: str = "pyramidal"


class EdgeRewireInput(BaseModel):
    member_ids: list[int] = Field(..., min_length=1)
    synthesis_id: int
    external_peers: list[PeerRewireSpec] = Field(default_factory=list)
    inactive_peer_ids: list[int] = Field(default_factory=list)
    reason: str | None = None


async def handle_edge_rewire(
    payload: EdgeRewireInput,
    actor: UserIdentity,
    db: AsyncSession,
    action_row: Action,
) -> dict[str, Any]:
    from app.config import settings
    from app.services.edge_tier import delete_weak_edge, upsert_weak_edge
    from app.services.reconsolidation.loaders import load_component_edges
    from app.services.reconsolidation.rewiring import plan_rewire_ops

    component = sorted(set(payload.member_ids) | {payload.synthesis_id})
    current = await load_component_edges(db, component)
    by_pk = {(e.source_id, e.target_id): e for e in current}
    ops = plan_rewire_ops(
        current, payload.member_ids, payload.synthesis_id,
        payload.external_peers, payload.inactive_peer_ids,
        promote_min_weight=settings.edge_promote_min_weight,
        promote_min_cofires=settings.edge_promote_min_cofires,
    )

    removed: list[dict] = []
    for d in ops.deletes:
        edge = by_pk.get((d.source_id, d.target_id))
        if edge is None:
            continue  # already gone — replay no-op
        removed.append({
            "source_id": edge.source_id, "target_id": edge.target_id,
            "edge_type": edge.edge_type, "weight": edge.weight,
            "co_fire_count": edge.co_fire_count, "reason": d.reason,
        })
        await db.delete(edge)
    await db.flush()

    upserted: list[dict] = []
    for u in ops.upserts:
        if u.promoted:
            existing = await db.get(NeuronEdge, (u.source_id, u.target_id))
            if existing is None:
                db.add(NeuronEdge(
                    source_id=u.source_id, target_id=u.target_id,
                    co_fire_count=u.co_fire_count, weight=u.weight,
                    edge_type=u.edge_type, source="reconsolidation",
                    context=(payload.reason or "reconsolidation: recomputed "
                             "from union co-fire evidence")[:300],
                ))
            else:
                # Deterministic overwrite — the union recomputation is the
                # value; old weights are never blended in.
                existing.co_fire_count = u.co_fire_count
                existing.weight = u.weight
                existing.edge_type = u.edge_type
                existing.source = "reconsolidation"
            await delete_weak_edge(db, u.source_id, u.target_id)
        else:
            await upsert_weak_edge(db, u.source_id, u.target_id, {
                "w": u.weight, "t": u.edge_type, "c": u.co_fire_count,
                "s": "reconsolidation", "q": 0,
            })
        upserted.append({
            "source_id": u.source_id, "target_id": u.target_id,
            "co_fire_count": u.co_fire_count, "weight": u.weight,
            "edge_type": u.edge_type, "promoted": u.promoted,
        })

    # Weak-tier internal entries are reapable statistics between absorbed
    # phrasings of one fact — clear them so nothing internal survives in
    # either tier.
    for a, b in ops.weak_internal_pairs:
        await delete_weak_edge(db, a, b)
    await db.flush()

    audit = {
        "member_ids": payload.member_ids,
        "synthesis_id": payload.synthesis_id,
        "edges_removed": len(removed),
        "edges_upserted": len(upserted),
        "removed": removed[:MAX_AUDIT_ROWS],
        "upserted": upserted[:MAX_AUDIT_ROWS],
        "no_evidence_peers": ops.no_evidence_peers,
        "unplanned_peers_removed": ops.unplanned_peers,
        "inactive_peers_dropped": payload.inactive_peer_ids,
    }
    return {"audit": audit, "payload": audit}
