"""FusionPlan schema + fail-closed validators (mind-reconsolidation-kernel
Phase 0B/0C). The golden plan is authored from the frozen NVM fixture; every
rejection path mirrors a receipt in test_reconsolidation_contract.py."""

import json
import os
from pathlib import Path

os.environ.setdefault("TENANT_ID", "corvus-mind")

import pytest

from app.models import Neuron
from app.services.reconsolidation import (
    Disposition,
    Facet,
    FacetKind,
    FusionPlan,
    InheritancePreview,
    MemberSnapshot,
    PlanValidationError,
    RewiringPreview,
    assert_preflight,
    check_postconditions,
    preflight,
    snapshot_of,
)
from app.services.reconsolidation.plan import EdgeRef

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "nvm_incident"
CORE = [47, 51, 57, 177, 1151, 1161]
SYNTHESIS = 0  # placeholder id for the to-be-created synthesis neuron


def _fixture_neurons() -> dict[int, Neuron]:
    rows = json.loads((FIXTURE_DIR / "neurons.json").read_text())
    out = {}
    for r in rows:
        if r["id"] in CORE:
            out[r["id"]] = Neuron(
                id=r["id"], label=r["label"], content=r["content"],
                summary=r["summary"], department=r["department"],
                layer=r["layer"], node_type=r["node_type"],
                is_active=r["is_active"], invocations=r["invocations"],
                avg_utility=r["avg_utility"], superseded_by=r["superseded_by"],
                authority_level=r["authority_level"], embedding=r["embedding"],
            )
    return out


def _union_distinct() -> int:
    firings = json.loads((FIXTURE_DIR / "neuron_firings.json").read_text())
    return len({f["query_id"] for f in firings if f["neuron_id"] in set(CORE)})


def _golden_plan(neurons: dict[int, Neuron]) -> FusionPlan:
    snaps = [snapshot_of(n) for n in neurons.values()]
    internal = [
        EdgeRef(source_id=e["source_id"], target_id=e["target_id"],
                edge_type=e["edge_type"])
        for e in json.loads((FIXTURE_DIR / "neuron_edges.json").read_text())
        if e["source_id"] in set(CORE) and e["target_id"] in set(CORE)
        and e["edge_type"] in ("pyramidal", "stellate")
    ]
    return FusionPlan(
        component_member_ids=CORE,
        member_snapshots=snaps,
        disposition=Disposition.SYNTHESIZE_NEW,
        coverage_delta=True,
        proposed_department="Environment",
        proposed_label="NVM and Node 22 project runtime convention on this machine",
        proposed_summary=(
            "Source ~/.config/nvm/nvm.sh and run `nvm use 22.22.0` before any "
            "project Node tooling; machine convention, not a repo-enforced pin."
        ),
        proposed_content=(
            "NVM lives at ~/.config/nvm/nvm.sh. Explicitly `source "
            "~/.config/nvm/nvm.sh && nvm use 22.22.0` before Node tooling for "
            "corvus frontend, master-corvus, and Market-Analytics-Suite. This "
            "is a machine convention; repos may lack .nvmrc/engines enforcement."
        ),
        facets=[
            Facet(kind=FacetKind.INVARIANT,
                  text="nvm is loaded from ~/.config/nvm/nvm.sh",
                  evidence_member_ids=[47, 51, 57, 1161]),
            Facet(kind=FacetKind.INVARIANT,
                  text="source nvm.sh && nvm use 22.22.0 before Node tooling",
                  evidence_member_ids=[51, 57, 177, 1151, 1161]),
            Facet(kind=FacetKind.EXAMPLE,
                  text="applies to corvus frontend, master-corvus, "
                       "Market-Analytics-Suite",
                  evidence_member_ids=[47, 51, 177, 1151]),
            Facet(kind=FacetKind.CAVEAT,
                  text="machine convention — repos may not enforce the pin",
                  evidence_member_ids=[57]),
        ],
        inheritance=InheritancePreview(
            invocations_union_distinct=_union_distinct(),
            utility_replayed=0.62,
            utility_events_replayed=14,
            utility_provenance_gaps=[
                "legacy +0.05 fusion boosts on #57 have no learning events",
            ],
            authority_level="guidance",
        ),
        rewiring=RewiringPreview(
            internal_activation_edges_to_retire=internal,
            provenance_links_to_create=[
                EdgeRef(source_id=m, target_id=SYNTHESIS,
                        edge_type="evidence-link") for m in CORE
            ],
        ),
    )


@pytest.fixture()
def neurons():
    return _fixture_neurons()


@pytest.fixture()
def plan(neurons):
    return _golden_plan(neurons)


class TestSchema:
    def test_golden_plan_validates_and_hashes_deterministically(self, neurons, plan):
        assert plan.inheritance.invocations_union_distinct == 419
        assert len(plan.rewiring.internal_activation_edges_to_retire) == 12
        again = _golden_plan(neurons)
        assert plan.plan_hash() == again.plan_hash()
        assert plan.member_state_hash() == again.member_state_hash()

    def test_member_state_hash_moves_with_member_content(self, neurons):
        before = _golden_plan(neurons).member_state_hash()
        neurons[57].content = "silently edited"
        assert _golden_plan(neurons).member_state_hash() != before

    def test_multi_member_retain_canonical_rejected(self, neurons):
        snaps = [snapshot_of(n) for n in neurons.values()]
        with pytest.raises(ValueError, match="strict two-member"):
            FusionPlan(component_member_ids=CORE, member_snapshots=snaps,
                       disposition=Disposition.RETAIN_CANONICAL,
                       canonical_neuron_id=57)

    def test_coverage_delta_forbids_retain_canonical(self, neurons):
        snaps = [snapshot_of(neurons[i]) for i in (51, 57)]
        with pytest.raises(ValueError, match="coverage delta"):
            FusionPlan(component_member_ids=[51, 57], member_snapshots=snaps,
                       disposition=Disposition.RETAIN_CANONICAL,
                       canonical_neuron_id=57, coverage_delta=True)

    def test_synthesize_new_forbids_winner_id(self, plan):
        with pytest.raises(ValueError, match="winner bias"):
            FusionPlan(**{**plan.model_dump(), "canonical_neuron_id": 57})

    def test_facet_citing_non_member_rejected(self, plan):
        dump = plan.model_dump()
        dump["facets"][0]["evidence_member_ids"] = [47, 999]
        with pytest.raises(ValueError, match="non-member evidence"):
            FusionPlan(**dump)

    def test_unresolved_conflict_requires_abstain(self, plan):
        dump = plan.model_dump()
        dump["facets"].append({
            "kind": "conflict", "text": "members disagree on version",
            "evidence_member_ids": [47, 51], "resolution": None,
        })
        with pytest.raises(ValueError, match="abstain"):
            FusionPlan(**dump)
        dump["disposition"] = "abstain"
        assert FusionPlan(**dump).disposition is Disposition.ABSTAIN

    def test_unrepresented_member_rejected(self, plan):
        dump = plan.model_dump()
        # Strip #177 from all facet evidence — absorbing unexamined content.
        for f in dump["facets"]:
            f["evidence_member_ids"] = [
                m for m in f["evidence_member_ids"] if m != 177
            ]
        with pytest.raises(ValueError, match=r"\[177\] evidence no facet"):
            FusionPlan(**dump)

    def test_snapshot_cover_mismatch_rejected(self, plan):
        dump = plan.model_dump()
        dump["member_snapshots"] = dump["member_snapshots"][:-1]
        with pytest.raises(ValueError, match="exactly cover"):
            FusionPlan(**dump)


class TestPreflight:
    def test_clean_preflight_passes(self, neurons, plan):
        assert preflight(
            plan, neurons,
            approved_member_state_hash=plan.member_state_hash(),
            proposal_state="approved",
        ) == []

    def test_member_drift_fails_closed(self, neurons, plan):
        approved = plan.member_state_hash()
        neurons[51].content = "edited after approval"
        violations = preflight(plan, neurons,
                               approved_member_state_hash=approved)
        assert any("content drifted" in v for v in violations)
        with pytest.raises(PlanValidationError):
            assert_preflight(plan, neurons, approved_member_state_hash=approved)

    def test_stale_approval_hash_fails_closed(self, neurons, plan):
        violations = preflight(plan, neurons,
                               approved_member_state_hash="0" * 64)
        assert any("approval is stale" in v for v in violations)

    def test_missing_member_fails_closed(self, neurons, plan):
        del neurons[1161]
        assert any("no longer exists" in v for v in preflight(plan, neurons))

    def test_unapproved_proposal_fails_closed(self, neurons, plan):
        violations = preflight(plan, neurons, proposal_state="proposed")
        assert any("not approved" in v for v in violations)

    def test_abstain_never_applies(self, neurons):
        snaps = [snapshot_of(neurons[i]) for i in (47, 51)]
        plan = FusionPlan(component_member_ids=[47, 51],
                          member_snapshots=snaps,
                          disposition=Disposition.ABSTAIN)
        assert any("never appliable" in v for v in preflight(plan, neurons))


class TestPostconditions:
    def _ok_kwargs(self, plan):
        return dict(
            active_representation_ids={SYNTHESIS},
            internal_conducting_edges=0,
            synthesis_invocations=419,
            synthesis_embedding_sha256="freshly-generated-hash",
            stale_proposal_states={1067: "superseded", 1069: "superseded",
                                   1071: "superseded"},
        )

    def test_clean_apply_passes(self, plan):
        assert check_postconditions(plan, **self._ok_kwargs(plan)) == []

    def test_sum_and_max_inheritance_both_rejected(self, plan):
        for wrong in (809, 321, 318):
            kwargs = self._ok_kwargs(plan) | {"synthesis_invocations": wrong}
            violations = check_postconditions(plan, **kwargs)
            assert any("union-distinct" in v for v in violations), wrong

    def test_surviving_member_after_synthesize_new_rejected(self, plan):
        kwargs = self._ok_kwargs(plan) | {
            "active_representation_ids": {57},
        }
        assert any("winner bias" in v
                   for v in check_postconditions(plan, **kwargs))

    def test_internal_edges_must_be_zero(self, plan):
        kwargs = self._ok_kwargs(plan) | {"internal_conducting_edges": 12}
        assert any("conducting activation" in v
                   for v in check_postconditions(plan, **kwargs))

    def test_inherited_embedding_rejected(self, plan):
        stale = plan.member_snapshots[0].embedding_sha256
        kwargs = self._ok_kwargs(plan) | {"synthesis_embedding_sha256": stale}
        assert any("regenerated" in v
                   for v in check_postconditions(plan, **kwargs))

    def test_obsolete_proposal_must_be_terminal(self, plan):
        kwargs = self._ok_kwargs(plan) | {
            "stale_proposal_states": {1067: "approved"},
        }
        assert any("terminally" in v
                   for v in check_postconditions(plan, **kwargs))
