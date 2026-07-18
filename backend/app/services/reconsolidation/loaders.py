"""DB loaders + component plan builder (kernel Phases 1-3 wiring).

Everything the deterministic kernel needs from the live database, loaded
in one place: member firings/events for the inheritance engine, promoted
edges for the rewiring planner, and the peer co-fire loader that
reconstructs each external relationship from UNION query evidence in
neuron_firings — never from existing edge weights.

`build_plan_for_component` is the janitor's entry point: one Opus review
of the whole component -> validated packet -> field-specific previews ->
hashed FusionPlan. Fail-closed: any packet violation or an abstain
disposition raises; nothing half-built ever reaches a proposal.
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Neuron, NeuronEdge, NeuronFiring, SynapticLearningEvent

_CONDUCTING = ("pyramidal", "stellate")


async def load_member_firings(
    db: AsyncSession, member_ids: list[int],
) -> list[NeuronFiring]:
    rows = (await db.execute(
        select(NeuronFiring).where(NeuronFiring.neuron_id.in_(member_ids))
    )).scalars().all()
    return list(rows)


async def load_member_events(
    db: AsyncSession, member_ids: list[int],
) -> list[SynapticLearningEvent]:
    rows = (await db.execute(
        select(SynapticLearningEvent)
        .where(SynapticLearningEvent.neuron_id.in_(member_ids))
        .order_by(SynapticLearningEvent.created_at, SynapticLearningEvent.id)
    )).scalars().all()
    return list(rows)


async def load_component_edges(
    db: AsyncSession, ids: list[int],
) -> list[NeuronEdge]:
    """All promoted edges touching any of `ids` (both directions)."""
    rows = (await db.execute(
        select(NeuronEdge).where(
            or_(NeuronEdge.source_id.in_(ids), NeuronEdge.target_id.in_(ids))
        )
    )).scalars().all()
    return list(rows)


def conducting_peer_ids(edges, member_ids) -> set[int]:
    from app.services.reconsolidation.rewiring import _get

    ids = set(member_ids)
    peers: set[int] = set()
    for e in edges:
        if _get(e, "edge_type") not in _CONDUCTING:
            continue
        src, tgt = _get(e, "source_id"), _get(e, "target_id")
        src_in, tgt_in = src in ids, tgt in ids
        if src_in != tgt_in:
            peers.add(tgt if src_in else src)
    return peers


async def load_peer_cofire_queries(
    db: AsyncSession, member_ids: list[int], peer_ids: set[int],
) -> dict[int, set[int]]:
    """UNION co-fire evidence per external peer, straight from
    neuron_firings: the distinct queries where the peer fired alongside
    ANY member. Set semantics dedup same-query evidence across members by
    construction — old edge weights never enter."""
    if not peer_ids:
        return {}
    member_queries = {
        q for (q,) in (await db.execute(
            select(NeuronFiring.query_id).distinct().where(
                NeuronFiring.neuron_id.in_(member_ids),
                NeuronFiring.query_id.isnot(None),
            )
        )).all()
    }
    cofire: dict[int, set[int]] = {p: set() for p in peer_ids}
    if not member_queries:
        return cofire
    rows = (await db.execute(
        select(NeuronFiring.neuron_id, NeuronFiring.query_id).distinct().where(
            NeuronFiring.neuron_id.in_(peer_ids),
            NeuronFiring.query_id.in_(member_queries),
        )
    )).all()
    for neuron_id, query_id in rows:
        cofire[neuron_id].add(query_id)
    return cofire


async def load_peer_context(
    db: AsyncSession, peer_ids: set[int],
) -> tuple[dict[int, str | None], set[int]]:
    """(peer_scopes, active_peer_ids) for the rewiring preview. A peer is
    active when it is live and not superseded — absorbed corpses drop."""
    if not peer_ids:
        return {}, set()
    rows = (await db.execute(
        select(Neuron.id, Neuron.department, Neuron.is_active,
               Neuron.superseded_by).where(Neuron.id.in_(peer_ids))
    )).all()
    scopes = {r.id: r.department for r in rows}
    active = {r.id for r in rows if r.is_active and r.superseded_by is None}
    return scopes, active


async def build_plan_for_component(
    db: AsyncSession, members, pair_verdicts=None, source_pair_verdict_ids=(),
):
    """One reviewed, hashed FusionPlan for a judged duplicate component.

    Runs the single component review (opus, Phase 1B), validates the
    packet fail-closed, then computes every preview from live evidence:
    inheritance from member firings/learning events, rewiring from
    promoted edges + the neuron_firings peer co-fire loader. Raises
    PacketValidationError on any violation; the caller decides what an
    abstain disposition means for its surface.
    """
    from app.services.reconsolidation import review as rv
    from app.services.reconsolidation.inheritance import (
        build_inheritance_preview, rewiring_preview,
    )

    packet = await rv.review_component(members, pair_verdicts)
    violations = rv.validate_packet(packet, members)
    if violations:
        raise rv.PacketValidationError(violations)
    facets = rv.parse_facets(packet)

    member_ids = [m.id for m in members]
    firings = await load_member_firings(db, member_ids)
    events = await load_member_events(db, member_ids)
    edges = await load_component_edges(db, member_ids)
    peers = conducting_peer_ids(edges, member_ids)
    peer_scopes, active_peers = await load_peer_context(db, peers)
    cofire = await load_peer_cofire_queries(db, member_ids, peers)

    scopes = [m.department for m in members if m.department]
    final_scope = packet.get("proposed_scope") or (
        max(set(scopes), key=scopes.count) if scopes else None)
    inheritance = build_inheritance_preview(members, firings, events, facets)
    rewiring = rewiring_preview(
        member_ids, edges, synthesis_id=0, final_scope=final_scope,
        peer_scopes=peer_scopes, peer_cofire_queries=cofire,
        active_peer_ids=active_peers,
    )
    return rv.assemble_plan(
        members, packet, inheritance, rewiring,
        source_pair_verdict_ids=source_pair_verdict_ids,
    )
