"""Proposal lifecycle (kernel Phase 4): one-step review + terminal staleness.

Tyler directive 2026-07-17: the approved-vs-applied split was a process
relic — "approved but never applied" let #1067/#1069/#1071 look resolved
for days while recall kept serving the originals. Review now IS the
apply: `approve_and_apply` runs approval and full application in ONE
transaction; if any child action or postcondition fails, the rollback
takes the approval with it, so no intermediate approved-pending state can
ever exist for a reviewed proposal. The reject path is unchanged.

Staleness is terminal: a proposal whose recorded old-state no longer
matches the live graph transitions to `superseded` — historically
inspectable, impossible to apply, never re-nagged. Revalidation runs at
review/apply time (fail closed before any write) and via
`supersede_stale_approved` as a sweep for legacy approved rows.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.rbac import UserIdentity
from app.models import AutopilotProposal, Neuron, ProposalItem

logger = logging.getLogger(__name__)

# proposed -> approved+applied (one step) | rejected | superseded
# approved (legacy rows only) -> applied | superseded
TERMINAL_STATES = frozenset({"rejected", "applied", "superseded"})

# content old_values may have been recorded truncated (janitor compose
# stored [:2000]); comparison tolerates exactly that truncation.
_TRUNCATED_FIELDS = {"content"}


class ProposalStaleError(RuntimeError):
    """The proposal's recorded old-state drifted — it was terminally
    superseded instead of applied. Callers COMMIT the supersession."""

    def __init__(self, proposal_id: int, violations: list[str]):
        self.proposal_id = proposal_id
        self.violations = violations
        super().__init__(
            f"proposal #{proposal_id} superseded (stale): "
            + "; ".join(violations))


def _normalize(field: str, value) -> str:
    if field == "is_active":
        return str(bool(value)).lower()
    if value is None:
        return ""
    return str(value)


def _live_value(neuron: Neuron, field: str) -> str:
    return _normalize(field, getattr(neuron, field, None))


def _matches(field: str, old_value: str | None, live: str) -> bool:
    old = old_value or ""
    if live == old:
        return True
    return field in _TRUNCATED_FIELDS and len(old) == 2000 and \
        live[:2000] == old


async def revalidate_items(
    db: AsyncSession, p: AutopilotProposal,
) -> list[str]:
    """Old-state revalidation: every update item's recorded old_value must
    still match the live graph, and a reconsolidate item's member-state
    hash must still pin the live members. Returns violations (empty ==
    current). This is what makes 'approved is not applied' impossible to
    weaponize — approval against state that later drifted can never apply."""
    violations: list[str] = []
    for item in (p.items or []):
        if item.action == "update" and item.target_neuron_id:
            neuron = await db.get(Neuron, item.target_neuron_id)
            if neuron is None:
                violations.append(
                    f"item {item.id}: target #{item.target_neuron_id} "
                    "no longer exists")
                continue
            # A superseded target is an absorbed corpse: the fact this
            # item wanted to change now lives in another representation
            # (live receipt: scope proposals #1069/#1071 aimed at #51/#177
            # after #1099 absorbed them — 'current' by old-value match,
            # dead by identity). Re-pointing supersession itself is the
            # one legitimate mutation left.
            if neuron.superseded_by is not None and \
                    item.field != "superseded_by":
                violations.append(
                    f"item {item.id}: target #{item.target_neuron_id} was "
                    f"superseded by #{neuron.superseded_by} — the fact "
                    "now lives elsewhere")
                continue
            if item.field and hasattr(neuron, item.field):
                live = _live_value(neuron, item.field)
                if not _matches(item.field, item.old_value, live):
                    violations.append(
                        f"item {item.id}: #{item.target_neuron_id}."
                        f"{item.field} drifted since proposal "
                        f"({(item.old_value or '')[:60]!r} -> {live[:60]!r})")
        elif item.action == "reconsolidate" and item.neuron_spec_json:
            from app.services.reconsolidation.apply import (
                ReconsolidationApplyError, parse_reconsolidation_spec,
            )
            from app.services.reconsolidation.validators import preflight

            try:
                plan, _ph, member_hash = parse_reconsolidation_spec(
                    item.neuron_spec_json)
            except (ReconsolidationApplyError, ValueError, KeyError) as exc:
                violations.append(f"item {item.id}: unreadable fusion plan "
                                  f"({exc})")
                continue
            live = {}
            for mid in plan.member_ids:
                n = await db.get(Neuron, mid)
                if n is not None:
                    live[mid] = n
            violations.extend(
                f"item {item.id}: {v}"
                for v in preflight(plan, live,
                                   approved_member_state_hash=member_hash))
    return violations


def mark_superseded(
    db: AsyncSession, p: AutopilotProposal, reason: str, actor_id: str,
) -> None:
    """Terminal transition. The row stays fully inspectable (items,
    evidence, original reviewer); review_notes records why it died."""
    assert p.state not in TERMINAL_STATES, \
        f"proposal #{p.id} is already terminal ({p.state})"
    note = f"[superseded by {actor_id} @ " \
           f"{datetime.utcnow().isoformat(timespec='seconds')}] {reason}"
    p.state = "superseded"
    p.review_notes = f"{p.review_notes}\n{note}" if p.review_notes else note
    logger.info("proposal %s superseded: %s", p.id, reason)


async def approve_and_apply(
    db: AsyncSession, p: AutopilotProposal, identity: UserIdentity,
    notes: str | None = None,
) -> bool:
    """ONE-step review: approve + apply in the caller's single transaction.

    Stale proposals are terminally superseded instead (ProposalStaleError
    — caller commits that transition and surfaces 409). Any apply failure
    raises ProposalApplyError with NOTHING committed: the approval rolls
    back with the writes, so the proposal simply remains 'proposed'.
    Returns has_edge_changes. Does NOT commit.
    """
    from app.services.proposal_apply_service import apply_approved_proposal

    assert p.state == "proposed", \
        f"one-step review requires state='proposed', got {p.state!r}"
    stale = await revalidate_items(db, p)
    if stale:
        mark_superseded(
            db, p,
            reason="stale at review time: " + "; ".join(stale)[:800],
            actor_id=identity.user_id,
        )
        raise ProposalStaleError(p.id, stale)

    p.state = "approved"
    p.reviewed_by = identity.user_id
    p.reviewed_at = datetime.utcnow()
    p.review_notes = notes
    await db.flush()
    return await apply_approved_proposal(db, p, identity, actor_type="user")


async def supersede_stale_approved(
    db: AsyncSession, actor_id: str = "reconsolidation_lifecycle",
) -> list[dict]:
    """Sweep legacy approved-unapplied rows whose old-state drifted and
    retire them terminally (Phase 4A — the #1067/#1069/#1071 receipt:
    approved before #1099 rewrote their targets, appliable never again).
    Does NOT commit."""
    rows = (await db.execute(
        select(AutopilotProposal)
        .where(AutopilotProposal.state == "approved")
        .order_by(AutopilotProposal.id)
    )).scalars().all()
    retired: list[dict] = []
    for p in rows:
        stale = await revalidate_items(db, p)
        if not stale:
            continue
        mark_superseded(
            db, p,
            reason="approved-unapplied sweep: " + "; ".join(stale)[:800],
            actor_id=actor_id,
        )
        retired.append({"proposal_id": p.id, "violations": stale})
    return retired


def reconsolidation_item_spec(plan, plan_hash: str | None = None) -> str:
    """Serialize a FusionPlan into a ProposalItem.neuron_spec_json payload
    (single source of the format written by the janitor and read by
    parse_reconsolidation_spec)."""
    return json.dumps({
        "fusion_plan": plan.model_dump(mode="json"),
        "plan_hash": plan_hash or plan.plan_hash(),
        "member_state_hash": plan.member_state_hash(),
    })


def find_reconsolidation_item(p: AutopilotProposal) -> ProposalItem | None:
    return next(
        (i for i in (p.items or [])
         if i.action == "reconsolidate" and i.neuron_spec_json), None)
