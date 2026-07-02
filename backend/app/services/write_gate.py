"""Tiered, policy-driven write gate (plat-write-gate).

For enterprise controlled memory the gate IS the product; the problem was
one binary human gate over everything. Two write classes:

- Authoritative writes (policies, standards, sources of truth) stay
  human-gated — high scrutiny, the control surface the controller buys.
- Observational/working writes (session learnings, low-authority ingest)
  auto-commit WITH full audit + reversibility, and consolidation decay
  reclaims whatever never gets reinforced (forgetting as the soft gate).

The gate does not add a second write path: an auto-routed proposal is
approved by `write_gate:tiered-v1` and applied through the exact same
Action Bus proposal.apply tree as a human approval — same audit trail,
same NeuronRefinement reversibility.

Policy lives in tenant.yaml (`write_gate:` block); default is `manual`
(everything queues — pre-gate behavior). Per-region overrides arrive with
RegionPolicy (plat-region-config).
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutopilotProposal, Neuron, ProposalItem

logger = logging.getLogger(__name__)

GATE_ACTOR = "write_gate:tiered-v1"

# Authority ranking for the auto-commit ceiling. Absent/unknown authority
# ranks as informational: unattributed writes are observational by
# definition (session learnings), and decay reclaims them if wrong.
AUTHORITY_RANK = MappingProxyType({
    "informational": 1,
    "guidance": 2,
    "organizational": 3,
    "industry_practice": 4,
    "regulatory": 5,
    "binding_standard": 6,
})
_UNKNOWN_AUTHORITY_RANK = 1


@dataclass(frozen=True)
class WriteGatePolicy:
    """Controller-configured gate policy (tenant.yaml `write_gate:` block)."""
    mode: str = "manual"                              # "manual" | "tiered"
    auto_commit_max_authority: str = "informational"  # ceiling for auto route
    require_guardrails_pass: bool = True              # False guardrails always queue
    min_confidence: float = 0.6                       # normalized 0-1


@dataclass(frozen=True)
class WriteDecision:
    route: str    # "auto" | "queue"
    reason: str
    policy_mode: str


def _policy_from_dict(raw: dict, base: "WriteGatePolicy | None" = None) -> WriteGatePolicy:
    """Build a policy from a config dict, overlaying an optional base."""
    defaults = base or WriteGatePolicy()
    policy = WriteGatePolicy(
        mode=str(raw.get("mode", defaults.mode)),
        auto_commit_max_authority=str(
            raw.get("auto_commit_max_authority", defaults.auto_commit_max_authority)
        ),
        require_guardrails_pass=bool(
            raw.get("require_guardrails_pass", defaults.require_guardrails_pass)
        ),
        min_confidence=float(raw.get("min_confidence", defaults.min_confidence)),
    )
    assert policy.mode in ("manual", "tiered"), \
        f"write_gate.mode must be manual|tiered, got {policy.mode!r}"
    return policy


def load_policy() -> WriteGatePolicy:
    """Read the tenant's write-gate policy; absent config = manual mode."""
    from app.tenant import tenant
    return _policy_from_dict(tenant.write_gate_config)


async def policy_for_region(db: AsyncSession, region: str | None) -> WriteGatePolicy:
    """Tenant policy overlaid with the region's write_gate overrides, if any.

    The controller dials the threshold per tenant AND per region — e.g.
    Manufacturing auto-commits organizational notes while Legal queues
    everything.
    """
    base = load_policy()
    if not region:
        return base
    from app.services.region_policy import get_region_policies
    overrides = ((await get_region_policies(db)).get(region) or {}).get("write_gate") or {}
    if not overrides:
        return base
    return _policy_from_dict(overrides, base)


def authority_rank(authority_level: str | None) -> int:
    """Rank an authority level; unknown/absent = observational (lowest)."""
    return AUTHORITY_RANK.get(authority_level or "", _UNKNOWN_AUTHORITY_RANK)


def evaluate_write(
    policy: WriteGatePolicy,
    authority_level: str | None,
    guardrails_passed: bool | None,
    confidence: float | None,
) -> WriteDecision:
    """Route a write: auto-commit (with audit) or human queue.

    guardrails_passed: True = all guardrail layers verified; False = at
    least one tripped (always queues); None = not applicable to this path.
    confidence: normalized 0-1 (e.g. eval_overall/5); None = not applicable.
    """
    assert confidence is None or 0.0 <= confidence <= 1.0, \
        f"confidence must be in [0,1], got {confidence}"

    if policy.mode == "manual":
        return WriteDecision("queue", "manual mode: all writes human-gated", policy.mode)

    if guardrails_passed is False and policy.require_guardrails_pass:
        return WriteDecision("queue", "guardrail layer tripped", policy.mode)

    rank = authority_rank(authority_level)
    ceiling = authority_rank(policy.auto_commit_max_authority)
    if rank > ceiling:
        return WriteDecision(
            "queue",
            f"authoritative write ({authority_level or 'unknown'} > "
            f"{policy.auto_commit_max_authority} ceiling)",
            policy.mode,
        )

    if confidence is not None and confidence < policy.min_confidence:
        return WriteDecision(
            "queue",
            f"confidence {confidence:.2f} below {policy.min_confidence:.2f}",
            policy.mode,
        )

    return WriteDecision(
        "auto",
        f"observational write ({authority_level or 'unattributed'}), "
        "auto-commit with audit; decay reclaims if unreinforced",
        policy.mode,
    )


async def proposal_max_authority(
    db: AsyncSession, proposal: AutopilotProposal,
) -> str | None:
    """The write class of a proposal = its most authoritative touched item.

    Updates inherit the TARGET neuron's authority (editing a regulatory
    neuron is an authoritative write); creates carry their spec's authority.
    """
    items = (await db.execute(
        select(ProposalItem).where(ProposalItem.proposal_id == proposal.id)
    )).scalars().all()

    best: str | None = None
    best_rank = 0
    target_ids = [i.target_neuron_id for i in items if i.target_neuron_id]
    if target_ids:
        rows = (await db.execute(
            select(Neuron.authority_level).where(Neuron.id.in_(target_ids))
        )).all()
        for (level,) in rows:
            if authority_rank(level) > best_rank:
                best, best_rank = level, authority_rank(level)
    for item in items:
        if not item.neuron_spec_json:
            continue
        try:
            spec_level = json.loads(item.neuron_spec_json).get("authority_level")
        except (json.JSONDecodeError, TypeError):
            continue
        if authority_rank(spec_level) > best_rank:
            best, best_rank = spec_level, authority_rank(spec_level)
    return best


async def route_proposal(
    db: AsyncSession,
    proposal: AutopilotProposal,
    *,
    guardrails_passed: bool | None,
    confidence: float | None,
    region: str | None = None,
) -> WriteDecision:
    """Evaluate a staged proposal and, on auto, approve + apply it in place.

    The auto path reuses the identical Action Bus apply tree a human
    approval would produce; the gate's decision is recorded in
    review_notes and the actor is GATE_ACTOR. Caller owns the commit.
    region selects per-region policy overrides (plat-region-config).
    """
    assert proposal.state == "proposed", \
        f"route_proposal requires state='proposed', got {proposal.state!r}"

    policy = await policy_for_region(db, region)
    authority = await proposal_max_authority(db, proposal)
    decision = evaluate_write(policy, authority, guardrails_passed, confidence)

    if decision.route != "auto":
        logger.info(
            "write_gate: proposal %s queued (%s)", proposal.id, decision.reason,
        )
        return decision

    from app.middleware.rbac import UserIdentity
    from app.services.proposal_apply_service import apply_approved_proposal

    proposal.state = "approved"
    proposal.reviewed_by = GATE_ACTOR
    proposal.reviewed_at = datetime.utcnow()
    proposal.review_notes = f"auto-approved by write gate: {decision.reason}"
    await db.flush()

    identity = UserIdentity(user_id=GATE_ACTOR, role="admin", source="system")
    await apply_approved_proposal(db, proposal, identity, actor_type="system")
    logger.info(
        "write_gate: proposal %s auto-applied (%s)", proposal.id, decision.reason,
    )
    return decision
