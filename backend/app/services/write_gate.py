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
import math
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutopilotProposal, Neuron, ProposalItem

logger = logging.getLogger(__name__)

GATE_ACTOR = "write_gate:tiered-v1"

# None is a deliberate legacy default; unsupported values are never defaults.
AuthorityLevel = Literal[
    "informational", "guidance", "organizational", "industry_practice",
    "regulatory", "binding_standard",
]
AUTHORITY_RANK = MappingProxyType({
    "informational": 1,
    "guidance": 2,
    "organizational": 3,
    "industry_practice": 4,
    "regulatory": 5,
    "binding_standard": 6,
})


def authority_rank(authority_level: str | None) -> int:
    """Rank a supported authority. Only None defaults to informational.

    Malformed input raises ValueError without echoing untrusted input. This
    boundary is shared by HTTP saves, internal writes and proposal aggregation.
    """
    if authority_level is None:
        return AUTHORITY_RANK["informational"]
    if not isinstance(authority_level, str) or authority_level not in AUTHORITY_RANK:
        raise ValueError("unsupported authority_level")
    return AUTHORITY_RANK[authority_level]


def _validate_probability(value, name: str) -> None:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0 <= value <= 1):
        raise ValueError(f"{name} must be a finite number in [0,1]")


@dataclass(frozen=True)
class WriteGatePolicy:
    """Controller-configured gate policy (tenant.yaml `write_gate:` block)."""
    mode: str = "manual"                              # "manual" | "tiered"
    auto_commit_max_authority: str = "informational"  # ceiling for auto route
    require_guardrails_pass: bool = True              # False guardrails always queue
    min_confidence: float = 0.6                       # normalized 0-1

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not isinstance(self.mode, str) or self.mode not in ("manual", "tiered"):
            raise ValueError("write_gate.mode must be manual|tiered")
        if self.auto_commit_max_authority is None:
            raise ValueError("write_gate ceiling requires an explicit authority")
        authority_rank(self.auto_commit_max_authority)
        if type(self.require_guardrails_pass) is not bool:
            raise ValueError("require_guardrails_pass must be boolean")
        _validate_probability(self.min_confidence, "min_confidence")


@dataclass(frozen=True)
class WriteDecision:
    route: str    # "auto" | "queue"
    reason: str
    policy_mode: str


def _policy_from_dict(raw: dict, base: "WriteGatePolicy | None" = None) -> WriteGatePolicy:
    """Build a policy from a config dict, overlaying an optional base."""
    defaults = base or WriteGatePolicy()
    defaults.validate()
    fields = {"mode", "auto_commit_max_authority", "require_guardrails_pass", "min_confidence"}
    if not isinstance(raw, dict) or raw.keys() - fields:
        raise ValueError("write_gate policy must be an object with supported fields")
    return WriteGatePolicy(
        **{name: raw.get(name, getattr(defaults, name)) for name in fields},
    )


def load_policy() -> WriteGatePolicy:
    """Read the tenant's write-gate policy; absent config = manual mode."""
    from app.tenant import tenant
    return _policy_from_dict(tenant.write_gate_config)


async def policy_for_region(db: AsyncSession, region: str | None) -> WriteGatePolicy:
    """Tenant policy overlaid with the region's write_gate overrides, if any.

    Region thresholds may restrict low-authority auto-writes. They cannot
    remove the organizational/identity countersign requirement.
    """
    base = load_policy()
    if not region:
        return base
    from app.services.region_policy import get_region_policies
    region_settings = (await get_region_policies(db)).get(region)
    if region_settings is None:
        return base
    if not isinstance(region_settings, dict):
        raise ValueError("region policy must be an object")
    return _policy_from_dict(region_settings.get("write_gate", {}), base)


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
    if not isinstance(policy, WriteGatePolicy):
        raise ValueError("WriteGatePolicy required")
    policy.validate()
    rank = authority_rank(authority_level)
    ceiling = authority_rank(policy.auto_commit_max_authority)
    if guardrails_passed is not None and type(guardrails_passed) is not bool:
        raise ValueError("guardrails_passed must be boolean or None")
    if confidence is not None:
        _validate_probability(confidence, "confidence")

    if policy.mode == "manual":
        return WriteDecision("queue", "manual mode: all writes human-gated", policy.mode)

    if guardrails_passed is False and policy.require_guardrails_pass:
        return WriteDecision("queue", "guardrail layer tripped", policy.mode)

    if rank >= AUTHORITY_RANK["organizational"]:
        return WriteDecision("queue", "authoritative write requires human countersign", policy.mode)
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

    if not items:
        raise ValueError("cannot classify an empty proposal")

    best: str | None = None
    best_rank = 0

    def consider(level, department=None):
        nonlocal best, best_rank
        rank = authority_rank(level)
        if department is not None and not isinstance(department, str):
            raise ValueError("proposal department must be a string or None")
        if (department and department.strip().casefold() == "assistant"
                and rank < AUTHORITY_RANK["organizational"]):
            level, rank = "organizational", AUTHORITY_RANK["organizational"]
        if rank > best_rank:
            best, best_rank = level, rank

    target_ids = {i.target_neuron_id for i in items if i.target_neuron_id is not None}
    if any(type(target) is not int or target <= 0 for target in target_ids):
        raise ValueError("proposal target must be a positive neuron id")
    if target_ids:
        rows = (await db.execute(
            select(Neuron.id, Neuron.authority_level, Neuron.department)
            .where(Neuron.id.in_(target_ids))
        )).all()
        if {row[0] for row in rows} != target_ids:
            raise ValueError("proposal target authority is unresolved")
        for _, level, department in rows:
            consider(level, department)
    for item in items:
        if item.action not in ("create", "update", "merge"):
            raise ValueError("proposal action requires explicit human review")
        if item.action in ("update", "merge"):
            if item.target_neuron_id is None:
                raise ValueError("proposal update requires a target")
            if item.field == "authority_level":
                # The apply path turns None into an empty string; reject it.
                if item.new_value is None:
                    raise ValueError("authority update requires an explicit value")
                consider(item.new_value)
            elif item.field == "department":
                consider(None, item.new_value)
        if item.neuron_spec_json is not None or item.action == "create":
            try:
                spec = json.loads(item.neuron_spec_json)
            except (ValueError, TypeError, RecursionError) as exc:
                raise ValueError("proposal spec must be a valid JSON object") from exc
            if not isinstance(spec, dict):
                raise ValueError("proposal spec must be a JSON object")
            consider(spec.get("authority_level"), spec.get("department"))
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
    if proposal.state != "proposed":
        raise ValueError("route_proposal requires state='proposed'")

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
