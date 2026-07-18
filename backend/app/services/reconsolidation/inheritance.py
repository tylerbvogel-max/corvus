"""Field-specific signal inheritance (kernel Phase 2).

One deterministic rule per inherited signal — never a generic
max/mean/median. The frozen NVM incident is the proof: the six-member
component had 809 summed firing rows, 321 max-member distinct queries,
but 419 UNION-distinct queries — sum double-counts co-delivery, max
discards disjoint history, union is the only correct rule. Every value
computed here lands in an InheritancePreview that is visible at review
time and asserted as a postcondition after apply.

DB-agnostic core: functions take plain rows (ORM objects or fixture
dicts), so the same rules run identically against the live session, a
throwaway tenant, or the frozen fixture in unit tests.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from app.services.reconsolidation.plan import (
    EdgeRef, ExternalPeerRewire, Facet, FacetKind, InheritancePreview,
    RewiringPreview,
)

BIRTH_UTILITY = 0.5
# A member whose stored utility deviates from its own replayed event
# history by more than this is a provenance gap (legacy boosts, decay
# rows outside the event table, pre-event history) — reported, never
# laundered into the synthesis' confidence.
UTILITY_MATCH_EPS = 0.005
# Weight follows accumulated co-fire evidence, saturating at 20 co-fires
# — parity with synaptic_learning._apply_edge_learning's /20.0 rule.
EDGE_WEIGHT_DENOM = 20.0
_AUTHORITY_RANK = {"informational": 0, "guidance": 1, "organizational": 2}
_CONDUCTING = frozenset({"pyramidal", "stellate"})


def _get(row, key, default=None):
    """Field access that works for ORM rows and fixture dicts alike."""
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _dt(value) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


# ── invocations: union of distinct query IDs ────────────────────────────

def union_distinct_queries(member_ids, firings) -> set[int]:
    """The ONLY correct invocation rule: distinct queries that fired ANY
    member. Dedups co-delivery (sum's error) without discarding disjoint
    history (max's error). Frozen NVM expectation: 419."""
    ids = set(member_ids)
    return {
        q for row in firings
        if _get(row, "neuron_id") in ids
        and (q := _get(row, "query_id")) is not None
    }


# ── utility: replay unique independent evidence from the birth prior ────

def dedup_events(
    events, member_ids, discounted_query_ids=frozenset(),
) -> tuple[list, int, int]:
    """(kept, deduped, discounted) learning events for the component.

    Same-query events across members are ONE piece of evidence delivered
    to several phrasings of the same fact — the chronologically-first
    event is kept per query (deterministic tie-break: event id).
    Queries in discounted_query_ids (injected / self-reinforced evidence
    the caller identified) are dropped entirely and counted."""
    ids = set(member_ids)
    member_events = sorted(
        (e for e in events if _get(e, "neuron_id") in ids),
        key=lambda e: (_dt(_get(e, "created_at")) or datetime.min,
                       _get(e, "id") or 0),
    )
    kept: list = []
    seen: set[int] = set()
    deduped = discounted = 0
    for e in member_events:
        q = _get(e, "query_id")
        if q in discounted_query_ids:
            discounted += 1
            continue
        if q in seen:
            deduped += 1
            continue
        seen.add(q)
        kept.append(e)
    return kept, deduped, discounted


def replay_utility(kept_events, alpha: float, loss_penalty: float) -> float:
    """Replay unique evidence through the NORMAL learning function from
    the birth prior. Component membership itself adds zero: no events,
    no movement. Legacy flat-delta events are re-normalized by replaying
    their (outcome, attribution) through compute_utility_adjustment."""
    from app.services.synaptic_learning import compute_utility_adjustment

    utility = BIRTH_UTILITY
    for e in kept_events:
        weight = min(1.0, max(0.0, float(_get(e, "attribution_weight") or 0.0)))
        utility, _raw, _eff = compute_utility_adjustment(
            utility, weight, _get(e, "event_type") == "reward",
            alpha, loss_penalty,
        )
    return round(utility, 6)


def member_provenance_gaps(members, events, alpha: float,
                           loss_penalty: float) -> list[str]:
    """Members whose stored utility is not reconstructable from their own
    event history (replayed from birth through the normal function).
    Frozen NVM receipt: #57 sits at 0.92 with four reward events —
    legacy fusion boosts with no learning-event trail."""
    gaps: list[str] = []
    for m in members:
        own = [e for e in events if _get(e, "neuron_id") == _get(m, "id")]
        own.sort(key=lambda e: (_dt(_get(e, "created_at")) or datetime.min,
                                _get(e, "id") or 0))
        replayed = replay_utility(own, alpha, loss_penalty)
        stored = _get(m, "avg_utility")
        stored = BIRTH_UTILITY if stored is None else float(stored)
        if abs(replayed - stored) > UTILITY_MATCH_EPS:
            gaps.append(
                f"member #{_get(m, 'id')} utility {stored:.3f} is not "
                f"reconstructable from its {len(own)} learning event(s) "
                f"(replay from birth gives {replayed:.3f})"
            )
    return gaps


# ── authority / dates ───────────────────────────────────────────────────

def derive_authority(members, facets: list[Facet]) -> str:
    """Authority of the synthesized claim = highest authority among the
    members that actually SUPPORT it (evidence an invariant facet).
    Consolidation grants no promotion: the result can never exceed the
    strongest supporting member, and defaults to informational."""
    supporting = {
        mid for f in facets if f.kind is FacetKind.INVARIANT
        for mid in f.evidence_member_ids
    } or {_get(m, "id") for m in members}
    best = "informational"
    for m in members:
        if _get(m, "id") not in supporting:
            continue
        level = _get(m, "authority_level") or "informational"
        if _AUTHORITY_RANK.get(level, 0) > _AUTHORITY_RANK.get(best, 0):
            best = level
    return best


def derive_dates(members, kept_events) -> tuple[str | None, str | None]:
    """(effective_date, last_verified): earliest supporting evidence /
    latest independently-replayed verification — with the event as the
    receipt. No kept events means no verification claim."""
    created = [d for m in members if (d := _dt(_get(m, "created_at")))]
    effective = min(created).date().isoformat() if created else None
    verified = [d for e in kept_events if (d := _dt(_get(e, "created_at")))]
    last = max(verified).isoformat(timespec="seconds") if verified else None
    return effective, last


# ── embedding freshness ─────────────────────────────────────────────────

def embedding_input(label: str, summary: str | None,
                    content: str | None) -> str:
    """The canonical embed-text recipe — identical to creation-time
    embedding (lesson_store._embed_created) so refreshed vectors live in
    the same space as everything else."""
    return f"{label}. {summary or ''} {content or ''}"[:2000]


def embedding_sha256(embedding_json: str | None) -> str | None:
    """Freshness receipt over the stored vector (parity with
    plan.snapshot_of, so postconditions can prove regeneration)."""
    if not embedding_json:
        return None
    return hashlib.sha256(embedding_json.encode()).hexdigest()


# ── assembled previews ──────────────────────────────────────────────────

def build_inheritance_preview(
    members, firings, events, facets: list[Facet], *,
    alpha: float | None = None, loss_penalty: float | None = None,
    discounted_query_ids=frozenset(),
) -> InheritancePreview:
    """Compute every inherited signal by its field-specific rule."""
    from app.config import settings

    alpha = settings.outcome_learning_alpha if alpha is None else alpha
    loss_penalty = (settings.outcome_loss_penalty
                    if loss_penalty is None else loss_penalty)
    member_ids = [_get(m, "id") for m in members]
    per_member_queries = {
        mid: {
            _get(row, "query_id") for row in firings
            if _get(row, "neuron_id") == mid
            and _get(row, "query_id") is not None
        }
        for mid in member_ids
    }
    kept, deduped, discounted = dedup_events(
        events, member_ids, discounted_query_ids)
    gaps = member_provenance_gaps(members, events, alpha, loss_penalty)
    if discounted:
        gaps.append(f"{discounted} injected/self-reinforced event(s) "
                    "discounted from replay")
    effective, last_verified = derive_dates(members, kept)
    return InheritancePreview(
        invocations_union_distinct=len(
            union_distinct_queries(member_ids, firings)),
        invocations_member_sum=sum(len(qs) for qs in per_member_queries.values()),
        invocations_member_max=max(
            (len(qs) for qs in per_member_queries.values()), default=0),
        utility_replayed=replay_utility(kept, alpha, loss_penalty),
        utility_events_replayed=len(kept),
        utility_events_deduped=deduped,
        utility_provenance_gaps=gaps,
        authority_level=derive_authority(members, facets),
        effective_date=effective,
        last_verified=last_verified,
    )


def rewiring_preview(
    member_ids, edges, *, synthesis_id: int = 0,
    final_scope: str | None = None, peer_scopes: dict | None = None,
    peer_cofire_queries: dict | None = None, active_peer_ids=None,
) -> RewiringPreview:
    """Deterministic rewiring preview (the plan half of Phase 3).

    Internal conducting edges retire; every member gets a non-conducting
    provenance link to the synthesis; each external peer's relationship
    is recomputed from the UNION of historical co-firing query sets —
    existing edge weights are never max/mean'd, and a peer with no
    reconstructable co-fire evidence shows count 0 explicitly rather
    than being silently dropped. Inactive peers drop (kept only as
    provenance history)."""
    ids = set(member_ids)
    peer_scopes = peer_scopes or {}
    peer_cofire_queries = peer_cofire_queries or {}

    internal: list[EdgeRef] = []
    peers: set[int] = set()
    for e in edges:
        src, tgt = _get(e, "source_id"), _get(e, "target_id")
        etype = _get(e, "edge_type")
        if etype not in _CONDUCTING:
            continue
        if src in ids and tgt in ids:
            internal.append(EdgeRef(source_id=src, target_id=tgt,
                                    edge_type=etype))
        elif src in ids or tgt in ids:
            peers.add(tgt if src in ids else src)

    dropped = sorted(
        p for p in peers
        if active_peer_ids is not None and p not in active_peer_ids)
    externals = []
    for p in sorted(peers - set(dropped)):
        union = set(peer_cofire_queries.get(p, ()))
        externals.append(ExternalPeerRewire(
            peer_id=p,
            union_cofire_queries=len(union),
            recomputed_weight=min(1.0, len(union) / EDGE_WEIGHT_DENOM),
            edge_type=("stellate"
                       if peer_scopes.get(p) == final_scope else "pyramidal"),
        ))
    return RewiringPreview(
        internal_activation_edges_to_retire=internal,
        provenance_links_to_create=[
            EdgeRef(source_id=m, target_id=synthesis_id,
                    edge_type="evidence-link") for m in sorted(ids)
        ],
        external_peers=externals,
        inactive_peers_dropped=dropped,
    )
