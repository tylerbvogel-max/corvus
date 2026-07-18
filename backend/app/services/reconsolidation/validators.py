"""Fail-closed preflight and postcondition checks for FusionPlan application.

DB-agnostic on purpose: callers fetch state (live Neuron rows, edge lists,
proposal states) and pass it in, so the same checks run identically against
the real session, a throwaway tenant, or fixture JSON in unit tests. Any
violation aborts the apply — there is no best-effort mode.
"""

from __future__ import annotations

from app.services.reconsolidation.plan import Disposition, FusionPlan


class PlanValidationError(RuntimeError):
    """Raised when a FusionPlan may not be applied. Fail closed."""

    def __init__(self, violations: list[str]):
        self.violations = violations
        super().__init__(
            "FusionPlan rejected (fail closed): " + "; ".join(violations)
        )


def preflight(
    plan: FusionPlan,
    live_neurons: dict[int, object],
    *,
    approved_member_state_hash: str | None = None,
    proposal_state: str | None = None,
) -> list[str]:
    """Checks that must ALL pass immediately before apply, inside the apply
    transaction. Returns violations (empty == pass).

    live_neurons: {neuron_id: Neuron-like} fetched under the same transaction.
    approved_member_state_hash: the member_state_hash recorded at approval
    time — proves the reviewer saw exactly this member state.
    proposal_state: current state of the carrying proposal, if any.
    """
    from app.services.reconsolidation.plan import snapshot_of

    violations: list[str] = []

    if plan.disposition is Disposition.ABSTAIN:
        violations.append("abstain plans are reviewable but never appliable")

    for snap in plan.member_snapshots:
        live = live_neurons.get(snap.neuron_id)
        if live is None:
            violations.append(f"member #{snap.neuron_id} no longer exists")
            continue
        live_snap = snapshot_of(live)
        if live_snap.content_hash != snap.content_hash:
            violations.append(
                f"member #{snap.neuron_id} content drifted since plan "
                f"({snap.content_hash} -> {live_snap.content_hash})"
            )
        if live_snap.is_active != snap.is_active:
            violations.append(
                f"member #{snap.neuron_id} active-state drifted since plan"
            )
        if live_snap.superseded_by != snap.superseded_by:
            violations.append(
                f"member #{snap.neuron_id} supersession drifted since plan"
            )

    if approved_member_state_hash is not None:
        if plan.member_state_hash() != approved_member_state_hash:
            violations.append(
                "member_state_hash mismatch: the approved plan pinned a "
                "different member state — approval is stale"
            )

    if proposal_state is not None and proposal_state != "approved":
        violations.append(
            f"carrying proposal is {proposal_state!r}, not approved"
        )

    if plan.disposition is not Disposition.ABSTAIN and plan.inheritance is None:
        violations.append("appliable plan is missing its inheritance preview")
    if plan.disposition is not Disposition.ABSTAIN and plan.rewiring is None:
        violations.append("appliable plan is missing its rewiring preview")

    return violations


def assert_preflight(plan: FusionPlan, live_neurons: dict[int, object],
                     **kwargs) -> None:
    violations = preflight(plan, live_neurons, **kwargs)
    if violations:
        raise PlanValidationError(violations)


def check_postconditions(
    plan: FusionPlan,
    *,
    active_representation_ids: set[int],
    internal_conducting_edges: int,
    synthesis_invocations: int,
    synthesis_embedding_sha256: str | None,
    stale_proposal_states: dict[int, str],
) -> list[str]:
    """Checks that must ALL pass after apply, before commit. Any violation
    rolls back the entire synthesis. Returns violations (empty == pass).

    active_representation_ids: active neurons among members + synthesis.
    internal_conducting_edges: count of pyramidal/stellate edges internal to
    members + synthesis after rewiring.
    synthesis_invocations: invocations on the surviving representation.
    synthesis_embedding_sha256: embedding hash of the surviving
    representation after apply.
    stale_proposal_states: {proposal_id: state} for proposals the plan
    obsoletes — all must be terminal (superseded), never appliable again.
    """
    violations: list[str] = []

    if len(active_representation_ids) != 1:
        violations.append(
            f"expected exactly one active representation, found "
            f"{sorted(active_representation_ids)}"
        )
    if plan.disposition is Disposition.SYNTHESIZE_NEW:
        if active_representation_ids & plan.member_ids:
            violations.append(
                "a prior member is still active after synthesize-new — "
                "winner bias survived"
            )

    if internal_conducting_edges != 0:
        violations.append(
            f"{internal_conducting_edges} conducting activation edge(s) "
            "remain internal to the component"
        )

    if plan.inheritance is not None:
        expected = plan.inheritance.invocations_union_distinct
        if synthesis_invocations != expected:
            violations.append(
                f"invocations {synthesis_invocations} != union-distinct "
                f"preview {expected} (sum/max inheritance is forbidden)"
            )

    member_embeddings = {
        s.embedding_sha256 for s in plan.member_snapshots if s.embedding_sha256
    }
    if synthesis_embedding_sha256 is None:
        violations.append("surviving representation has no embedding")
    elif (plan.disposition is Disposition.SYNTHESIZE_NEW
          and synthesis_embedding_sha256 in member_embeddings):
        violations.append(
            "embedding was inherited from a member instead of regenerated "
            "from the final text"
        )

    for pid, state in stale_proposal_states.items():
        if state != "superseded":
            violations.append(
                f"obsoleted proposal #{pid} is {state!r}, not terminally "
                "superseded — it could still apply against dead state"
            )

    return violations
