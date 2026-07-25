"""Reconsolidation apply engine (kernel Phases 3+4).

Executes one approved FusionPlan atomically inside the carrying
proposal's transaction. Deterministic after approval: every value written
here comes from the plan's previews; the only decisions left are
fail-closed checks. The audit tree is one `proposal.reconsolidate` child
under the `proposal.apply` root, with its own creation / stat-rebuild /
refinement / link / rewire children.

Fail-closed at both ends: preflight revalidates member hashes and
proposal state before any write; check_postconditions runs after the last
write and ANY violation raises — the action bus savepoint plus the
uncommitted outer transaction roll back the entire synthesis, statistics,
edges, proposal states, and caches together. Idempotent replay: the
`proposal.reconsolidate` action carries idempotency_key
`fusionplan:<plan_hash>`, so a second apply of the same plan returns the
recorded receipt without re-executing.
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.rbac import UserIdentity
from app.models import AutopilotProposal, Neuron, NeuronEdge, ProposalItem
from app.services.reconsolidation.plan import Disposition, FusionPlan
from app.services.reconsolidation.validators import (
    assert_preflight, check_postconditions,
)

logger = logging.getLogger(__name__)

_CONDUCTING = ("pyramidal", "stellate")
RECONSOLIDATE_ITEM_ACTION = "reconsolidate"


class ReconsolidationApplyError(RuntimeError):
    """Postconditions (or a child action) failed — the apply rolls back."""

    def __init__(self, violations: list[str]):
        self.violations = violations
        super().__init__(
            "reconsolidation apply failed closed: " + "; ".join(violations))


def parse_reconsolidation_spec(spec_json: str) -> tuple[FusionPlan, str, str]:
    """(plan, plan_hash, member_state_hash) from a ProposalItem's spec.
    Hash fields recorded at proposal time are revalidated against the
    embedded plan so a tampered spec fails before preflight."""
    spec = json.loads(spec_json)
    plan = FusionPlan.model_validate(spec["fusion_plan"])
    plan_hash = spec.get("plan_hash") or plan.plan_hash()
    member_hash = spec.get("member_state_hash") or plan.member_state_hash()
    if plan_hash != plan.plan_hash():
        raise ReconsolidationApplyError(
            ["stored plan_hash does not match the embedded plan"])
    if member_hash != plan.member_state_hash():
        raise ReconsolidationApplyError(
            ["stored member_state_hash does not match the embedded plan"])
    return plan, plan_hash, member_hash


def synthesis_entities(members, final_text: str) -> list[str] | None:
    """Deterministic entity re-extraction for the synthesis: the union of
    member entities filtered to those still present in the final text.
    Evidence-bounded — nothing an LLM never attributed can appear."""
    lowered = final_text.casefold()
    kept: list[str] = []
    for m in members:
        for ent in (getattr(m, "entities", None) or []):
            e = str(ent)
            if e.casefold() in lowered and e not in kept:
                kept.append(e)
    return sorted(kept) or None


def _synthesis_create_spec(plan: FusionPlan, live_members: list) -> dict:
    inh = plan.inheritance
    final_text = " ".join(filter(None, (
        plan.proposed_label, plan.proposed_summary, plan.proposed_content)))
    entities = synthesis_entities(live_members, final_text)
    return {
        "layer": 3,
        "node_type": plan.proposed_node_type,
        "label": plan.proposed_label,
        "summary": plan.proposed_summary,
        "content": plan.proposed_content,
        "department": plan.proposed_department,
        "source_origin": "reconsolidation",
        "source_type": "operational",
        "authority_level": inh.authority_level if inh else "informational",
        "citation": (f"reconsolidation synthesis of members "
                     f"{sorted(plan.member_ids)} (plan "
                     f"{plan.plan_hash()[:12]})")[:500],
        **({"entities": entities} if entities else {}),
    }


async def _submit_child(db, kind, identity, actor_type, proposal_id,
                        parent_action_id, input_data, reason=None,
                        idempotency_key=None):
    from app.services import action_bus

    result = await action_bus.submit(
        db=db, kind=kind, actor=identity, actor_type=actor_type,
        source_proposal_id=proposal_id, parent_action_id=parent_action_id,
        reason=reason, input_data=input_data, idempotency_key=idempotency_key,
    )
    if result.state != "applied":
        raise ReconsolidationApplyError(
            [f"{kind} child action failed: {result.state} ({result.error})"])
    return result


async def _internal_conducting_count(db: AsyncSession, ids: list[int]) -> int:
    return (await db.execute(
        select(func.count()).select_from(NeuronEdge).where(
            NeuronEdge.source_id.in_(ids),
            NeuronEdge.target_id.in_(ids),
            NeuronEdge.edge_type.in_(_CONDUCTING),
        )
    )).scalar_one()


async def _supersede_obsoleted_proposals(
    db: AsyncSession, member_ids: set[int], carrying_proposal_id: int,
    plan_hash: str, actor_id: str,
) -> dict[int, str]:
    """Terminally retire every open/approved proposal still aimed at a
    member: after the synthesis, their targets' state hash is dead — they
    stay historically inspectable but can never apply (Phase 4A)."""
    from app.services.reconsolidation.lifecycle import mark_superseded

    rows = (await db.execute(
        select(AutopilotProposal).join(
            ProposalItem, ProposalItem.proposal_id == AutopilotProposal.id,
        ).where(
            ProposalItem.target_neuron_id.in_(member_ids),
            AutopilotProposal.state.in_(("proposed", "approved")),
            AutopilotProposal.id != carrying_proposal_id,
        ).distinct()
    )).scalars().all()
    states: dict[int, str] = {}
    for p in rows:
        mark_superseded(
            db, p,
            reason=(f"superseded by reconsolidation plan {plan_hash[:12]} "
                    f"(proposal #{carrying_proposal_id}): member state it "
                    "was approved against no longer exists"),
            actor_id=actor_id,
        )
        states[p.id] = p.state
    return states


async def run_reconsolidation(
    db: AsyncSession,
    plan: FusionPlan,
    member_state_hash: str,
    proposal_id: int,
    item_id: int | None,
    identity: UserIdentity,
    actor_type: str,
    total_queries: int,
    parent_action_id: int,
) -> dict:
    """Execute an approved FusionPlan. Called from the
    proposal.reconsolidate action handler; does NOT commit."""
    from app.config import settings
    from app.services.adjacency_cache import invalidate_adjacency_cache
    from app.services.consolidation import refresh_centrality

    proposal = await db.get(AutopilotProposal, proposal_id)
    assert proposal is not None, f"proposal {proposal_id} not found"
    member_ids = sorted(plan.member_ids)
    live = {mid: await db.get(Neuron, mid) for mid in member_ids}
    assert_preflight(
        plan, {k: v for k, v in live.items() if v is not None},
        approved_member_state_hash=member_state_hash,
        proposal_state=proposal.state,
    )

    plan_hash = plan.plan_hash()
    submit = lambda kind, data, reason=None: _submit_child(  # noqa: E731
        db, kind, identity, actor_type, proposal_id, parent_action_id,
        data, reason)

    # 1. Surviving representation.
    if plan.disposition is Disposition.SYNTHESIZE_NEW:
        create = await submit(
            "neuron.create",
            {"spec": _synthesis_create_spec(plan, list(live.values())),
             "proposal_id": proposal_id, "item_id": item_id,
             "total_queries": total_queries,
             "reason": "reconsolidation synthesis (no winner bias): new "
                       "canonical representation for the component"},
        )
        survivor_id = (create.payload or {}).get("neuron_id")
        assert survivor_id, "neuron.create must return the synthesis id"
    else:
        survivor_id = plan.canonical_neuron_id
        assert survivor_id in live, "canonical must be a live member"

    # 2. Field-specific inherited statistics + fresh embedding (Phase 2).
    inh = plan.inheritance
    stats = await submit(
        "neuron.stats.rebuild",
        {"neuron_id": survivor_id,
         "invocations": inh.invocations_union_distinct,
         "avg_utility": inh.utility_replayed,
         "authority_level": inh.authority_level,
         "effective_date": inh.effective_date,
         "last_verified": inh.last_verified,
         "utility_provenance_gaps": inh.utility_provenance_gaps,
         "reason": f"reconsolidation plan {plan_hash[:12]}: union-distinct "
                   f"invocations={inh.invocations_union_distinct}, replayed "
                   f"utility={inh.utility_replayed}"},
    )

    # 3. Member lifecycle: every absorbed member deactivates and points at
    # the synthesis. History rows persist; MemoryChangeEvents land inside
    # neuron.refine.
    for mid in member_ids:
        if mid == survivor_id:
            continue
        m = live[mid]
        if m.is_active:
            await submit("neuron.refine", {
                "target_neuron_id": mid, "field": "is_active",
                "old_value": "true", "new_value": "false",
                "proposal_id": proposal_id, "item_id": item_id,
                "reason": f"reconsolidation: absorbed into #{survivor_id}",
            })
        if m.superseded_by != survivor_id:
            await submit("neuron.refine", {
                "target_neuron_id": mid, "field": "superseded_by",
                "old_value": ("" if m.superseded_by is None
                              else str(m.superseded_by)),
                "new_value": str(survivor_id),
                "proposal_id": proposal_id, "item_id": item_id,
                "reason": f"reconsolidation: superseded by #{survivor_id}",
            })

    # 4. member -> synthesis provenance: durable weight-1 evidence-links
    # that never conduct spread (Phase 3A).
    for mid in member_ids:
        if mid == survivor_id:
            continue
        await submit("edge.link", {
            "source_id": mid, "target_id": survivor_id, "weight": 1.0,
            "co_fire_count": settings.edge_promote_min_cofires,
            "edge_type": "evidence-link", "source": "reconsolidation",
            "context": (f"reconsolidation: member #{mid} absorbed into "
                        f"#{survivor_id} (plan {plan_hash[:12]})")[:300],
        }, reason="member->synthesis provenance link")

    # 5. Deterministic rewiring: retire internal/stale conducting edges,
    # materialize union-evidence external edges (Phase 3B-D).
    rw = plan.rewiring
    rewire = await submit("edge.rewire", {
        "member_ids": member_ids, "synthesis_id": survivor_id,
        "external_peers": [
            {"peer_id": p.peer_id,
             "union_cofire_queries": p.union_cofire_queries,
             "recomputed_weight": p.recomputed_weight,
             "edge_type": p.edge_type}
            for p in rw.external_peers],
        "inactive_peer_ids": rw.inactive_peers_dropped,
        "reason": f"reconsolidation plan {plan_hash[:12]}",
    })

    # 6. Terminal supersession of proposals aimed at now-dead member state.
    stale_states = await _supersede_obsoleted_proposals(
        db, set(member_ids), proposal_id, plan_hash, identity.user_id)

    # 7. Derived topology is never inherited — recompute after rewiring
    # (Phase 3E); process-local caches invalidate with it.
    centrality_updates = await refresh_centrality(db)
    invalidate_adjacency_cache()

    # 8. Acceptance bar: any violation rolls the whole synthesis back.
    survivor = await db.get(Neuron, survivor_id)
    active_ids = {
        nid for nid in {*member_ids, survivor_id}
        if (n := await db.get(Neuron, nid)) is not None and n.is_active
    }
    violations = check_postconditions(
        plan,
        active_representation_ids=active_ids,
        internal_conducting_edges=await _internal_conducting_count(
            db, [*member_ids, survivor_id]),
        synthesis_invocations=survivor.invocations or 0,
        synthesis_embedding_sha256=stats.audit.get("embedding_sha256"),
        stale_proposal_states=stale_states,
    )
    if violations:
        raise ReconsolidationApplyError(violations)

    return {
        "survivor_id": survivor_id,
        "disposition": plan.disposition.value,
        "plan_hash": plan_hash,
        "member_ids": member_ids,
        "invocations": inh.invocations_union_distinct,
        "avg_utility": inh.utility_replayed,
        "embedding_sha256": stats.audit.get("embedding_sha256"),
        "edges_removed": rewire.audit.get("edges_removed"),
        "edges_upserted": rewire.audit.get("edges_upserted"),
        "superseded_proposals": sorted(stale_states),
        "centrality_updates": centrality_updates,
    }
