"""Deterministic apply-side rewiring (kernel Phase 3).

Pure planning: given the CURRENT promoted edges touching a component and
the approved RewiringPreview, compute the exact edge operations the
`edge.rewire` action must execute. No DB access here — the same planner
runs identically against ORM rows, fixture dicts, and unit-test literals,
and the executor stays a thin, auditable loop.

Rules (settled architecture):
- Every conducting edge INTERNAL to the component (members + synthesis)
  is retired — deleted from the promoted table. Member↔member semantic
  provenance is carried by the member→synthesis evidence-links, not by
  leftover activation topology; keeping retyped internal rows would keep
  inflating the degree centrality of absorbed corpses (frozen receipt:
  inactive #51 at 0.3898 outranking active #57 at 0.2373).
- Every member↔peer conducting edge is retired the same way; the ONLY
  external relationship that survives is synthesis↔peer, recomputed from
  the UNION of historical co-fire query sets (never max/mean of old
  weights). A peer with zero reconstructable co-fire evidence gets NO
  edge — recorded explicitly, never silently.
- Non-conducting rows (supersedes / scoped-by / evidence-link) are
  history and are never touched.
- Composite-PK collisions resolve deterministically: synthesis↔peer rows
  are written holder-order (min id first), and re-planning against
  already-rewired state yields empty delete sets — idempotent replay.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_CONDUCTING = frozenset({"pyramidal", "stellate"})


def _get(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


@dataclass(frozen=True)
class EdgeDelete:
    source_id: int
    target_id: int
    edge_type: str
    reason: str  # "internal" | "member-peer" | "inactive-peer" | "unplanned-peer"


@dataclass(frozen=True)
class EdgeUpsert:
    """Synthesis↔peer edge recomputed from union co-fire evidence."""
    source_id: int          # holder order: min(synthesis, peer)
    target_id: int
    co_fire_count: int      # len(union of co-fire query sets)
    weight: float           # min(1, union/20) — normal edge-learning rule
    edge_type: str          # stellate/pyramidal from FINAL scopes
    promoted: bool          # meets promotion thresholds -> neuron_edges table


@dataclass
class RewireOps:
    deletes: list[EdgeDelete] = field(default_factory=list)
    upserts: list[EdgeUpsert] = field(default_factory=list)
    # Peers with zero reconstructable co-fire evidence: no edge is
    # created; listed so nothing is silently dropped.
    no_evidence_peers: list[int] = field(default_factory=list)
    # Conducting peer edges whose peer appears in neither the external
    # nor the inactive preview list (edges that grew between plan and
    # apply). Removed like every other member-side conducting edge, but
    # called out loudly — the next organic co-fire rebuilds them.
    unplanned_peers: list[int] = field(default_factory=list)
    # Member pairs whose weak-tier (JSONB) entries must be cleared.
    weak_internal_pairs: list[tuple[int, int]] = field(default_factory=list)


def plan_rewire_ops(
    current_edges,
    member_ids,
    synthesis_id: int,
    external_peers,
    inactive_peer_ids,
    *,
    promote_min_weight: float,
    promote_min_cofires: int,
) -> RewireOps:
    """Compute the full edge-operation set for one reconsolidation.

    current_edges: promoted NeuronEdge rows (or dicts) touching any member
    or the synthesis. external_peers: the approved plan's
    ExternalPeerRewire entries (or dicts). Deterministic and idempotent:
    planning against post-apply state yields no deletes and identical
    upserts.
    """
    component = set(member_ids) | {synthesis_id}
    planned_peers = {_get(p, "peer_id"): p for p in external_peers}
    inactive = set(inactive_peer_ids)
    ops = RewireOps()

    for e in current_edges:
        src, tgt = _get(e, "source_id"), _get(e, "target_id")
        etype = _get(e, "edge_type")
        if etype not in _CONDUCTING:
            continue  # memory-semantics rows are history — never touched
        src_in, tgt_in = src in component, tgt in component
        if src_in and tgt_in:
            ops.deletes.append(EdgeDelete(src, tgt, etype, "internal"))
        elif src_in or tgt_in:
            peer = tgt if src_in else src
            if peer in planned_peers:
                reason = "member-peer"
            elif peer in inactive:
                reason = "inactive-peer"
            else:
                reason = "unplanned-peer"
                if peer not in ops.unplanned_peers:
                    ops.unplanned_peers.append(peer)
            ops.deletes.append(EdgeDelete(src, tgt, etype, reason))

    for peer_id in sorted(planned_peers):
        p = planned_peers[peer_id]
        union = int(_get(p, "union_cofire_queries") or 0)
        if union <= 0:
            ops.no_evidence_peers.append(peer_id)
            continue
        weight = float(_get(p, "recomputed_weight") or 0.0)
        a, b = sorted((synthesis_id, peer_id))
        ops.upserts.append(EdgeUpsert(
            source_id=a, target_id=b, co_fire_count=union, weight=weight,
            edge_type=str(_get(p, "edge_type") or "pyramidal"),
            promoted=(weight >= promote_min_weight
                      and union >= promote_min_cofires),
        ))

    ids = sorted(component)
    ops.weak_internal_pairs = [
        (a, b) for i, a in enumerate(ids) for b in ids[i + 1:]
    ]
    return ops
