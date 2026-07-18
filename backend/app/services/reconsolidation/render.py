"""Review-grade projection of a FusionPlan (mind-fusionplan-preview-ui).

Tyler approves a READABLE before/after, never a blob. This module decodes
one FusionPlan into the structure the Inbox renders: per-member cards
with a fresh/stale verdict, field-inheritance receipts that say WHICH
rule produced each inherited value, a rewiring summary with weight
provenance, and validator status. Presentation only — it reuses the same
primitives the apply path trusts (snapshot_of for drift, preflight for
the appliability verdict) and opens no new mutation path.

DB-agnostic like validators: callers fetch live member rows and pass
them in, so the same projection runs against the real session or fixture
neurons in golden tests.
"""

from __future__ import annotations

from app.services.reconsolidation.plan import (
    Disposition, FacetKind, FusionPlan, MemberSnapshot, snapshot_of,
)
from app.services.reconsolidation.validators import preflight

RENDER_VERSION = 1

# Member freshness statuses, worst-first. "superseded" is the DEAD-TARGET
# case: the fact this plan wanted to fuse now lives in another
# representation, so the plan can never apply (lifecycle receipt:
# #1069/#1071 aimed at #51/#177 after #1099 absorbed them).
_STATUS_RANK = ("missing", "superseded", "content-drifted",
                "state-drifted", "fresh")


def _member_status(snap: MemberSnapshot, live) -> tuple[str, list[str]]:
    """(status, notes) for one member card, mirroring exactly the checks
    preflight runs — a member this function calls fresh cannot fail
    preflight's drift checks, and vice versa."""
    if live is None:
        return "missing", [f"member #{snap.neuron_id} no longer exists"]
    live_snap = snapshot_of(live)
    if (live_snap.superseded_by is not None
            and live_snap.superseded_by != snap.superseded_by):
        return "superseded", [
            f"absorbed into #{live_snap.superseded_by} — the fact "
            "now lives elsewhere"]
    notes: list[str] = []
    content_drifted = live_snap.content_hash != snap.content_hash
    if content_drifted:
        notes.append(
            f"content drifted since plan "
            f"({snap.content_hash} -> {live_snap.content_hash})")
    if live_snap.is_active != snap.is_active:
        notes.append("active-state drifted since plan")
    if live_snap.superseded_by != snap.superseded_by:
        notes.append("supersession drifted since plan")
    if content_drifted:
        return "content-drifted", notes
    return ("state-drifted" if notes else "fresh"), notes


def _member_cards(plan: FusionPlan, live_neurons: dict) -> list[dict]:
    evidence_counts: dict[int, int] = {}
    for f in plan.facets:
        if f.kind is FacetKind.ADJACENT:
            continue
        for mid in f.evidence_member_ids:
            evidence_counts[mid] = evidence_counts.get(mid, 0) + 1

    retained = (plan.canonical_neuron_id
                if plan.disposition is Disposition.RETAIN_CANONICAL else None)
    cards = []
    for snap in plan.member_snapshots:
        live = live_neurons.get(snap.neuron_id)
        status, notes = _member_status(snap, live)
        cards.append({
            "neuron_id": snap.neuron_id,
            "label": snap.label,
            "summary": snap.summary,
            "content": snap.content,
            "node_type": snap.node_type,
            "scope": snap.department,
            "authority_level": snap.authority_level,
            "invocations": snap.invocations,
            "avg_utility": snap.avg_utility,
            "is_active": snap.is_active,
            "facet_evidence_count": evidence_counts.get(snap.neuron_id, 0),
            "content_hash": snap.content_hash,
            "status": status,
            "status_notes": notes,
            "outcome": ("retain" if snap.neuron_id == retained
                        else "unchanged"
                        if plan.disposition is Disposition.ABSTAIN
                        else "retire"),
        })
    return cards


def _identity_receipts(plan: FusionPlan) -> list[dict]:
    """label/summary/content/scope: LLM-proposed but facet-bounded — the
    packet validator rejects any path/version/entity the members never
    asserted. retain-canonical keeps the canonical member's identity."""
    if plan.disposition is Disposition.RETAIN_CANONICAL:
        canonical = next(s for s in plan.member_snapshots
                         if s.neuron_id == plan.canonical_neuron_id)
        after = {"label": canonical.label, "summary": canonical.summary,
                 "content": canonical.content,
                 "department": canonical.department}
        why = (f"retain-canonical: #{canonical.neuron_id}'s identity and ID "
               "survive a strict two-member restatement; only statistics "
               "rebuild")
        rule = "retain-canonical-identity"
    else:
        after = {"label": plan.proposed_label,
                 "summary": plan.proposed_summary,
                 "content": plan.proposed_content,
                 "department": plan.proposed_department}
        why = ("LLM-proposed synthesis, facet-bounded: every claim must be "
               "evidenced by a member; invented paths/versions fail closed "
               "at packet validation")
        rule = "facet-bounded-synthesis"

    receipts = []
    for field in ("label", "summary", "content", "department"):
        receipts.append({
            "field": field if field != "department" else "scope",
            "before": [
                {"neuron_id": s.neuron_id, "value": getattr(s, field)}
                for s in plan.member_snapshots],
            "after": after[field],
            "rule": rule,
            "why": why,
        })
    return receipts


def _stat_receipts(plan: FusionPlan) -> list[dict]:
    inh = plan.inheritance
    if inh is None:
        return []
    snaps = plan.member_snapshots
    receipts = [
        {
            "field": "invocations",
            "before": [{"neuron_id": s.neuron_id, "value": s.invocations}
                       for s in snaps],
            "after": inh.invocations_union_distinct,
            "rule": "union-distinct-queries",
            "why": (f"distinct queries that fired ANY member — sum "
                    f"({inh.invocations_member_sum}) double-counts "
                    f"co-delivery, max ({inh.invocations_member_max}) "
                    "discards disjoint history"),
            "rejected": {"member_sum": inh.invocations_member_sum,
                         "member_max": inh.invocations_member_max},
        },
        {
            "field": "avg_utility",
            "before": [{"neuron_id": s.neuron_id,
                        "value": round(s.avg_utility, 6)} for s in snaps],
            "after": inh.utility_replayed,
            "rule": "replay-from-birth",
            "why": (f"replayed from the 0.5 birth prior through the normal "
                    f"learning rule over {inh.utility_events_replayed} "
                    f"unique events ({inh.utility_events_deduped} same-query "
                    "deduped) — membership adds zero, never averaged"),
            "flags": list(inh.utility_provenance_gaps),
        },
        {
            "field": "authority_level",
            "before": [{"neuron_id": s.neuron_id, "value": s.authority_level}
                       for s in snaps],
            "after": inh.authority_level,
            "rule": "support-derived",
            "why": ("highest authority among members that evidence an "
                    "invariant facet — consolidation never promotes"),
        },
        {
            "field": "effective_date / last_verified",
            "before": [],
            "after": (f"{inh.effective_date or 'unknown'} / "
                      f"{inh.last_verified or 'no verification claim'}"),
            "rule": "evidence-dates",
            "why": ("earliest supporting member evidence / latest "
                    "independently replayed verification event"),
        },
        {
            "field": "embedding · entities · centrality",
            "before": [],
            "after": (f"{inh.embedding_action} · {inh.entities_action} · "
                      f"{inh.centrality_action}"),
            "rule": "action-at-apply",
            "why": ("derived signals are never inherited — regenerated "
                    "inside the apply transaction and asserted by "
                    "postconditions"),
        },
    ]
    return receipts


def _rewiring_summary(plan: FusionPlan) -> dict | None:
    rw = plan.rewiring
    if rw is None:
        return None
    return {
        "internal_conducting_deleted":
            len(rw.internal_activation_edges_to_retire),
        "member_peer_retired":
            len(rw.external_peers) + len(rw.inactive_peers_dropped),
        "synthesis_peer_created": len(rw.external_peers),
        "provenance_links_created": len(rw.provenance_links_to_create),
        "why": ("internal conducting edges DELETED (retyped rows inflated "
                "corpse centrality); member↔peer relationships retire; only "
                "synthesis↔peer survives, weighted from UNION-of-cofire "
                "evidence — old edge weights never max/mean'd"),
        "peers": [
            {"peer_id": p.peer_id,
             "union_cofire_queries": p.union_cofire_queries,
             "recomputed_weight": p.recomputed_weight,
             "edge_type": p.edge_type,
             "weight_provenance": (
                 f"UNION of {p.union_cofire_queries} distinct co-fire "
                 "queries / 20 (saturating)")}
            for p in rw.external_peers],
        "inactive_peers_dropped": list(rw.inactive_peers_dropped),
    }


def _postconditions(plan: FusionPlan) -> list[str]:
    """What check_postconditions will assert inside the apply transaction —
    shown at review time so the approval is informed, enforced at apply."""
    out = ["exactly one active representation survives the component"]
    if plan.disposition is Disposition.SYNTHESIZE_NEW:
        out.append("no prior member stays active (winner bias forbidden)")
    out.append("0 conducting edges remain internal to the component")
    if plan.inheritance is not None:
        out.append(
            f"synthesis invocations == {plan.inheritance.invocations_union_distinct} "
            "(union-distinct; sum/max inheritance forbidden)")
    out.append("embedding regenerated from final text, not inherited")
    out.append("every proposal aimed at a member is terminally superseded")
    return out


def render_fusion_plan(
    plan: FusionPlan,
    plan_hash: str,
    member_state_hash: str,
    live_neurons: dict,
) -> dict:
    """Decode one FusionPlan into the Inbox's review-grade projection.
    Read-only: computes drift/validator verdicts against the live rows the
    caller fetched, writes nothing."""
    members = _member_cards(plan, live_neurons)
    violations = preflight(
        plan, {k: v for k, v in live_neurons.items() if v is not None},
        approved_member_state_hash=member_state_hash,
    )
    # Appliability facts preflight reports that are not drift: abstain
    # plans and missing previews are permanent properties of the plan,
    # not staleness — keep the freshness verdict about drift only.
    drift = [v for v in violations
             if "drifted" in v or "no longer exists" in v
             or "member_state_hash mismatch" in v]
    dead_targets = [
        {"neuron_id": m["neuron_id"],
         "superseded_by": live_neurons[m["neuron_id"]].superseded_by,
         "note": m["status_notes"][0]}
        for m in members if m["status"] == "superseded"
    ]

    return {
        "kind": "reconsolidate",
        "render_version": RENDER_VERSION,
        "plan_hash": plan_hash,
        "member_state_hash": member_state_hash,
        "disposition": plan.disposition.value,
        "coverage_delta": plan.coverage_delta,
        "canonical_neuron_id": plan.canonical_neuron_id,
        "proposed_node_type": plan.proposed_node_type,
        "freshness": {
            "verdict": "stale" if drift else "fresh",
            "violations": drift,
            "dead_targets": dead_targets,
        },
        "members": members,
        "fields": _identity_receipts(plan) + _stat_receipts(plan),
        "facets": [
            {"kind": f.kind.value, "text": f.text,
             "evidence_member_ids": list(f.evidence_member_ids),
             "resolution": f.resolution}
            for f in plan.facets],
        "rewiring": _rewiring_summary(plan),
        "validators": {
            "preflight_passed": not violations,
            "preflight_violations": violations,
            "postconditions_asserted_at_apply": _postconditions(plan),
        },
    }
