"""Proposal apply — the single dispatch path from an approved proposal
into the graph, shared by human approval (proposals router) and the tiered
write gate's auto route.

Every per-item write passes through the Action Bus as a child action of a
single `proposal.apply` root action (AIP Pattern #1): identical audit tree
regardless of who (or what policy) approved. This module does NOT commit —
the caller owns the transaction.
"""

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.rbac import UserIdentity
from app.models import AutopilotProposal, ProposalItem
from app.services import action_bus

logger = logging.getLogger(__name__)


class ProposalApplyError(RuntimeError):
    """A child or root action failed while applying a proposal."""


def _require_applied(result, kind: str) -> None:
    if result.state != "applied":
        raise ProposalApplyError(
            f"{kind} child action failed: {result.state} ({result.error})"
        )


async def _submit_rescale_child(
    db: AsyncSession, item: ProposalItem, p: AutopilotProposal,
    identity: UserIdentity, actor_type: str, root_action_id: int,
) -> None:
    """Route a 'rescale' ProposalItem through the edge.rescale action."""
    assert item.neuron_spec_json is not None, "rescale item must have spec"
    spec = json.loads(item.neuron_spec_json)
    result = await action_bus.submit(
        db=db, kind="edge.rescale", actor=identity, actor_type=actor_type,
        source_proposal_id=p.id, parent_action_id=root_action_id,
        reason=item.reason,
        input_data={
            "source_id": spec["source_id"],
            "target_id": spec["target_id"],
            "new_weight": spec["new_weight"],
        },
    )
    _require_applied(result, "edge.rescale")


async def _submit_link_child(
    db: AsyncSession, item: ProposalItem, p: AutopilotProposal,
    identity: UserIdentity, actor_type: str, root_action_id: int,
) -> None:
    """Route a 'link' ProposalItem through the edge.link action."""
    assert item.neuron_spec_json is not None, "link item must have spec"
    spec = json.loads(item.neuron_spec_json)
    result = await action_bus.submit(
        db=db, kind="edge.link", actor=identity, actor_type=actor_type,
        source_proposal_id=p.id, parent_action_id=root_action_id,
        reason=item.reason,
        input_data={
            "source_id": spec["source_id"],
            "target_id": spec["target_id"],
            "weight": spec.get("initial_weight", 0.15),
            # Memory-semantics edges (graph-lint fusion provenance) pass
            # the promotion count in their spec so they land durable, not
            # in the reapable weak tier; topology links keep the default.
            "co_fire_count": spec.get("co_fire_count", 1),
            "edge_type": spec.get("edge_type", "pyramidal"),
            "source": spec.get("source", "integrity_completion"),
            "context": spec.get("context", ""),
        },
    )
    _require_applied(result, "edge.link")


async def _submit_create_child(
    db: AsyncSession, item: ProposalItem, p: AutopilotProposal,
    total_queries: int, identity: UserIdentity, actor_type: str,
    root_action_id: int,
) -> None:
    """Route a 'create' ProposalItem through the neuron.create action."""
    assert item.neuron_spec_json is not None
    spec = json.loads(item.neuron_spec_json)
    result = await action_bus.submit(
        db=db, kind="neuron.create", actor=identity, actor_type=actor_type,
        source_proposal_id=p.id, parent_action_id=root_action_id,
        reason=item.reason,
        input_data={
            "proposal_id": p.id, "item_id": item.id, "spec": spec,
            "query_id": p.query_id, "total_queries": total_queries,
            "reason": item.reason,
        },
    )
    _require_applied(result, "neuron.create")


async def _submit_refine_child(
    db: AsyncSession, item: ProposalItem, p: AutopilotProposal,
    identity: UserIdentity, actor_type: str, root_action_id: int,
) -> None:
    """Route an 'update' or 'merge' ProposalItem through the neuron.refine action."""
    result = await action_bus.submit(
        db=db, kind="neuron.refine", actor=identity, actor_type=actor_type,
        source_proposal_id=p.id, parent_action_id=root_action_id,
        reason=item.reason,
        input_data={
            "proposal_id": p.id, "item_id": item.id,
            "target_neuron_id": item.target_neuron_id,
            "field": item.field or "",
            "old_value": item.old_value or "",
            "new_value": item.new_value or "",
            "query_id": p.query_id,
            "reason": item.reason,
        },
    )
    _require_applied(result, "neuron.refine")


async def _submit_reconsolidate_child(
    db: AsyncSession, item: ProposalItem, p: AutopilotProposal,
    total_queries: int, identity: UserIdentity, actor_type: str,
    root_action_id: int,
) -> None:
    """Route a 'reconsolidate' ProposalItem (a full FusionPlan) through the
    proposal.reconsolidate action. idempotency_key = the plan hash, so
    replaying an already-applied plan returns its recorded receipt instead
    of re-executing (kernel Phase 4C)."""
    from app.services.reconsolidation.apply import parse_reconsolidation_spec

    assert item.neuron_spec_json is not None, "reconsolidate item must have spec"
    _plan, plan_hash, member_hash = parse_reconsolidation_spec(
        item.neuron_spec_json)
    spec = json.loads(item.neuron_spec_json)
    result = await action_bus.submit(
        db=db, kind="proposal.reconsolidate", actor=identity,
        actor_type=actor_type, source_proposal_id=p.id,
        parent_action_id=root_action_id, reason=item.reason,
        idempotency_key=f"fusionplan:{plan_hash}",
        input_data={
            "proposal_id": p.id, "item_id": item.id,
            "fusion_plan": spec["fusion_plan"],
            "member_state_hash": member_hash,
            "total_queries": total_queries,
            "actor_type": actor_type,
        },
    )
    _require_applied(result, "proposal.reconsolidate")


async def _dispatch_proposal_items(
    db: AsyncSession, items: list[ProposalItem], p: AutopilotProposal,
    total_queries: int, identity: UserIdentity, actor_type: str,
    root_action_id: int,
) -> bool:
    """Run every ProposalItem through the right write path. Returns has_edge_changes."""
    has_edge_changes = False
    for item in items:
        if item.action == "reconsolidate" and item.neuron_spec_json:
            await _submit_reconsolidate_child(
                db, item, p, total_queries, identity, actor_type,
                root_action_id)
            has_edge_changes = True
        elif item.action == "create" and item.neuron_spec_json:
            await _submit_create_child(
                db, item, p, total_queries, identity, actor_type, root_action_id,
            )
        elif item.action in ("update", "merge") and item.target_neuron_id:
            await _submit_refine_child(db, item, p, identity, actor_type, root_action_id)
        elif item.action == "rescale" and item.neuron_spec_json:
            await _submit_rescale_child(db, item, p, identity, actor_type, root_action_id)
            has_edge_changes = True
        elif item.action == "link" and item.neuron_spec_json:
            await _submit_link_child(db, item, p, identity, actor_type, root_action_id)
            has_edge_changes = True
    return has_edge_changes


async def resolve_integrity_findings(
    db: AsyncSession, proposal_id: int, applied_by: str,
) -> None:
    """When an integrity proposal is applied, mark linked findings as resolved."""
    from datetime import datetime
    from app.models import IntegrityFinding
    stmt = select(IntegrityFinding).where(IntegrityFinding.proposal_id == proposal_id)
    result = await db.execute(stmt)
    for finding in result.scalars().all():
        finding.status = "resolved"
        finding.resolved_by = applied_by
        finding.resolved_at = datetime.utcnow()


async def apply_approved_proposal(
    db: AsyncSession,
    p: AutopilotProposal,
    identity: UserIdentity,
    actor_type: str = "user",
) -> bool:
    """Apply an approved proposal through the Action Bus.

    Root `proposal.apply` action + one child action per item. Marks the
    proposal applied and syncs integrity findings. Returns has_edge_changes
    (caller invalidates the adjacency cache after commit). Raises
    ProposalApplyError on any action failure. Does NOT commit.
    """
    assert p.state == "approved", \
        f"apply requires state='approved', got {p.state!r}"

    from datetime import datetime
    from app.services.neuron_service import get_system_state

    state = await get_system_state(db)
    items = list(p.items or [])

    root_result = await action_bus.submit(
        db=db, kind="proposal.apply", actor=identity, actor_type=actor_type,
        source_proposal_id=p.id,
        input_data={
            "proposal_id": p.id, "item_count": len(items),
            "applied_by": identity.user_id,
        },
    )
    if root_result.state != "applied":
        raise ProposalApplyError(
            f"proposal.apply root action failed: {root_result.state} ({root_result.error})"
        )

    has_edge_changes = await _dispatch_proposal_items(
        db, items, p, state.total_queries, identity, actor_type,
        root_result.action_id,
    )

    p.state = "applied"
    p.applied_at = datetime.utcnow()
    p.applied_by = identity.user_id

    if p.gap_source and p.gap_source.startswith("integrity_"):
        await resolve_integrity_findings(db, p.id, identity.user_id)

    return has_edge_changes
