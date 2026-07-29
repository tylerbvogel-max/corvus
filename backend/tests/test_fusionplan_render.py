"""mind-fusionplan-preview-ui — the review-grade rendered_plan projection.

Golden-tested against the frozen NVM fixture (tests/fixtures/nvm_incident)
so the receipts Tyler countersigns can never silently drift: 419
UNION-distinct invocations (809 sum / 321 max rejected on the card),
utility 0.743329 replayed from the 0.5 birth prior, 12 internal
conducting edges deleted. Staleness renders as DEAD with the
fact-lives-elsewhere explanation; validator failures surface; the
projection's freshness verdict always agrees with preflight.
"""

import copy
import json
import os
from pathlib import Path

os.environ.setdefault("TENANT_ID", "corvus-mind")

import pytest

from tests.golden_frames import GOLDEN_NVM_CONTENT, TWO_MEMBER_NVM_CONTENT

from app.models import Neuron
from app.services.reconsolidation import review as rv
from app.services.reconsolidation.inheritance import (
    build_inheritance_preview, rewiring_preview,
)
from app.services.reconsolidation.lifecycle import reconsolidation_item_spec
from app.services.reconsolidation.apply import parse_reconsolidation_spec
from app.services.reconsolidation.render import render_fusion_plan
from app.services.reconsolidation.validators import preflight

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "nvm_incident"
CORE = [47, 51, 57, 177, 1151, 1161]
ALPHA, LOSS_PENALTY = 0.05, 0.3


def _load(name: str):
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


def _fixture_neurons() -> dict[int, Neuron]:
    out = {}
    for r in _load("neurons"):
        if r["id"] in set(CORE):
            out[r["id"]] = Neuron(
                id=r["id"], label=r["label"], content=r["content"],
                summary=r["summary"], department=r["department"],
                layer=r["layer"], node_type=r["node_type"],
                is_active=r["is_active"], invocations=r["invocations"],
                avg_utility=r["avg_utility"], superseded_by=r["superseded_by"],
                authority_level=r["authority_level"], embedding=r["embedding"],
                entities=r.get("entities"), created_at=None,
            )
    return out


def _golden_packet() -> dict:
    return {
        "facets": [
            {"kind": "invariant",
             "text": "nvm is loaded from ~/.config/nvm/nvm.sh",
             "evidence_member_ids": [47, 51, 57, 1161], "resolution": None},
            {"kind": "invariant",
             "text": "source ~/.config/nvm/nvm.sh && nvm use 22.22.0 "
                     "before Node tooling",
             "evidence_member_ids": [51, 57, 177, 1151, 1161],
             "resolution": None},
            {"kind": "example",
             "text": "applies to corvus frontend, master-corvus, "
                     "Market-Analytics-Suite",
             "evidence_member_ids": [47, 51, 177, 1151], "resolution": None},
            {"kind": "caveat",
             "text": "machine convention — repos may not enforce the pin "
                     "(no .nvmrc / engines field)",
             "evidence_member_ids": [47, 57], "resolution": None},
        ],
        "proposed_label": "NVM and Node 22 project runtime convention "
                          "on this machine",
        "proposed_summary": "Source ~/.config/nvm/nvm.sh and run `nvm use "
                            "22.22.0` before project Node tooling; machine "
                            "convention, not a repo-enforced pin.",
        "proposed_content": GOLDEN_NVM_CONTENT,
        "proposed_scope": "Environment",
    }


@pytest.fixture()
def neurons():
    return _fixture_neurons()


@pytest.fixture()
def nvm_plan(neurons):
    members = [neurons[i] for i in CORE]
    member_rows = [r for r in _load("neurons") if r["id"] in set(CORE)]
    packet = _golden_packet()
    facets = rv.parse_facets(packet)
    inheritance = build_inheritance_preview(
        member_rows, _load("neuron_firings"),
        _load("synaptic_learning_events"), facets,
        alpha=ALPHA, loss_penalty=LOSS_PENALTY)
    rewiring = rewiring_preview(CORE, _load("neuron_edges"))
    return rv.assemble_plan(members, packet, inheritance, rewiring)


def _render(plan, live, member_hash=None):
    return render_fusion_plan(
        plan, plan.plan_hash(),
        member_hash if member_hash is not None else plan.member_state_hash(),
        live)


def _field(rendered, name):
    return next(f for f in rendered["fields"] if f["field"] == name)


class TestGoldenNvmRender:
    """The projection over unchanged fixture members carries exactly the
    frozen receipts — union 419, utility 0.743329, 12 internal edges."""

    def test_fresh_verdict_and_headline(self, nvm_plan, neurons):
        r = _render(nvm_plan, neurons)
        assert r["kind"] == "reconsolidate"
        assert r["disposition"] == "synthesize-new"
        assert r["coverage_delta"] is True
        assert r["freshness"]["verdict"] == "fresh"
        assert r["freshness"]["violations"] == []
        assert r["freshness"]["dead_targets"] == []
        assert r["validators"]["preflight_passed"] is True
        assert r["plan_hash"] == nvm_plan.plan_hash()
        assert r["member_state_hash"] == nvm_plan.member_state_hash()

    def test_member_cards(self, nvm_plan, neurons):
        r = _render(nvm_plan, neurons)
        assert [m["neuron_id"] for m in r["members"]] == CORE
        for m in r["members"]:
            assert m["status"] == "fresh"
            assert m["outcome"] == "retire"          # synthesize-new
            assert m["facet_evidence_count"] > 0     # every member examined
            assert len(m["content_hash"]) == 16
        by_id = {m["neuron_id"]: m for m in r["members"]}
        # #57's four facet citations and legacy 0.92 utility are visible.
        assert by_id[57]["facet_evidence_count"] == 3
        assert by_id[57]["avg_utility"] == pytest.approx(0.92)

    def test_invocations_receipt_union_not_sum_or_max(self, nvm_plan, neurons):
        f = _field(_render(nvm_plan, neurons), "invocations")
        assert f["after"] == 419                     # frozen acceptance
        assert f["rule"] == "union-distinct-queries"
        assert f["rejected"] == {"member_sum": 809, "member_max": 321}
        assert "double-counts" in f["why"] and "discards" in f["why"]

    def test_utility_receipt_replayed_from_birth(self, nvm_plan, neurons):
        f = _field(_render(nvm_plan, neurons), "avg_utility")
        assert f["after"] == pytest.approx(0.743329, abs=1e-6)  # golden replay
        assert f["rule"] == "replay-from-birth"
        assert "13 unique events" in f["why"]
        assert "1 same-query deduped" in f["why"]
        # Provenance gaps flagged, never laundered: #47/#51/#57/#177.
        assert len(f["flags"]) == 4

    def test_authority_and_dates_receipts(self, nvm_plan, neurons):
        r = _render(nvm_plan, neurons)
        auth = _field(r, "authority_level")
        assert auth["after"] == "guidance"           # support-derived
        assert auth["rule"] == "support-derived"
        dates = _field(r, "effective_date / last_verified")
        assert dates["after"].startswith("2026-07-10 / 2026-07-16")
        actions = _field(r, "embedding · entities · centrality")
        assert "regenerate-from-final-text" in actions["after"]

    def test_identity_receipts_facet_bounded(self, nvm_plan, neurons):
        r = _render(nvm_plan, neurons)
        label = _field(r, "label")
        assert label["rule"] == "facet-bounded-synthesis"
        assert len(label["before"]) == len(CORE)
        assert "NVM and Node 22" in label["after"]
        scope = _field(r, "scope")
        assert scope["after"] == "Environment"

    def test_rewiring_summary(self, nvm_plan, neurons):
        rw = _render(nvm_plan, neurons)["rewiring"]
        assert rw["internal_conducting_deleted"] == 12   # frozen acceptance
        assert rw["provenance_links_created"] == len(CORE)
        for peer in rw["peers"]:
            assert "UNION of" in peer["weight_provenance"]

    def test_postconditions_announced(self, nvm_plan, neurons):
        posts = _render(nvm_plan, neurons)["validators"][
            "postconditions_asserted_at_apply"]
        assert any("419" in p for p in posts)
        assert any("0 conducting edges" in p for p in posts)
        assert any("winner bias" in p for p in posts)
        assert any("terminally superseded" in p for p in posts)

    def test_round_trip_through_item_spec(self, nvm_plan, neurons):
        """The exact router path: spec JSON -> parse -> render."""
        spec = reconsolidation_item_spec(nvm_plan)
        plan, plan_hash, member_hash = parse_reconsolidation_spec(spec)
        r = render_fusion_plan(plan, plan_hash, member_hash, neurons)
        assert r["freshness"]["verdict"] == "fresh"
        assert _field(r, "invocations")["after"] == 419


class TestStaleness:
    """A drifted plan renders prominently as dead, in agreement with the
    revalidate/preflight verdict that will terminally supersede it."""

    def test_content_drift_renders_stale(self, nvm_plan, neurons):
        neurons[51].content = "the content someone edited after review"
        r = _render(nvm_plan, neurons)
        assert r["freshness"]["verdict"] == "stale"
        assert any("content drifted" in v for v in r["freshness"]["violations"])
        by_id = {m["neuron_id"]: m for m in r["members"]}
        assert by_id[51]["status"] == "content-drifted"
        for mid in set(CORE) - {51}:
            assert by_id[mid]["status"] == "fresh"

    def test_superseded_member_explains_fact_lives_elsewhere(
            self, nvm_plan, neurons):
        neurons[57].superseded_by = 1200
        neurons[57].is_active = False
        r = _render(nvm_plan, neurons)
        assert r["freshness"]["verdict"] == "stale"
        dead = r["freshness"]["dead_targets"]
        assert dead and dead[0]["neuron_id"] == 57
        assert dead[0]["superseded_by"] == 1200
        assert "lives elsewhere" in dead[0]["note"]
        by_id = {m["neuron_id"]: m for m in r["members"]}
        assert by_id[57]["status"] == "superseded"

    def test_missing_member_renders_stale(self, nvm_plan, neurons):
        del neurons[177]
        r = _render(nvm_plan, neurons)
        assert r["freshness"]["verdict"] == "stale"
        by_id = {m["neuron_id"]: m for m in r["members"]}
        assert by_id[177]["status"] == "missing"
        assert any("no longer exists" in v
                   for v in r["freshness"]["violations"])

    def test_tampered_member_hash_pin_surfaces(self, nvm_plan, neurons):
        r = _render(nvm_plan, neurons, member_hash="0" * 64)
        assert r["freshness"]["verdict"] == "stale"
        assert any("member_state_hash mismatch" in v
                   for v in r["validators"]["preflight_violations"])

    @pytest.mark.parametrize("mutate", [
        lambda ns: setattr(ns[51], "content", "drifted"),
        lambda ns: setattr(ns[57], "superseded_by", 1200),
        lambda ns: ns.pop(177),
        lambda ns: setattr(ns[47], "is_active", False),
        lambda ns: None,
    ])
    def test_verdict_agrees_with_preflight(self, nvm_plan, neurons, mutate):
        """The card's fresh/stale verdict and the fail-closed apply gate can
        never disagree — what renders fresh must preflight clean."""
        mutate(neurons)
        r = _render(nvm_plan, neurons)
        gate = preflight(
            nvm_plan, dict(neurons),
            approved_member_state_hash=nvm_plan.member_state_hash())
        assert (r["freshness"]["verdict"] == "stale") == bool(gate)


class TestRetainCanonical:
    def test_identity_receipt_shows_canonical(self):
        a = Neuron(id=1, layer=3, node_type="lesson", label="nvm activation",
                   content="source ~/.config/nvm/nvm.sh && nvm use 22.22.0",
                   department="Environment", invocations=10, avg_utility=0.6,
                   is_active=True)
        b = Neuron(id=2, layer=3, node_type="lesson",
                   label="nvm activation again",
                   content="source ~/.config/nvm/nvm.sh && nvm use 22.22.0",
                   department="Environment", invocations=3, avg_utility=0.5,
                   is_active=True)
        packet = {
            "facets": [{"kind": "invariant",
                        "text": "source ~/.config/nvm/nvm.sh && nvm use "
                                "22.22.0",
                        "evidence_member_ids": [1, 2]}],
            "proposed_label": "nvm activation",
            "proposed_summary": "nvm use 22.22.0",
            "proposed_content": TWO_MEMBER_NVM_CONTENT,
            "proposed_scope": "Environment",
        }
        plan = rv.assemble_plan([a, b], packet, None, None)
        r = _render(plan, {1: a, 2: b})
        assert r["disposition"] == "retain-canonical"
        assert r["canonical_neuron_id"] == 1
        by_id = {m["neuron_id"]: m for m in r["members"]}
        assert by_id[1]["outcome"] == "retain"
        assert by_id[2]["outcome"] == "retire"
        label = _field(r, "label")
        assert label["rule"] == "retain-canonical-identity"
        assert label["after"] == "nvm activation"
        # No inheritance preview on this minimal plan -> missing-preview
        # violations surface in validators without breaking the render.
        assert r["validators"]["preflight_passed"] is False
        assert any("missing its inheritance preview" in v
                   for v in r["validators"]["preflight_violations"])
        # ...but that is appliability, not drift: freshness stays fresh.
        assert r["freshness"]["verdict"] == "fresh"


class TestUnreadableSpec:
    def test_tampered_spec_fails_at_parse_not_render(self, nvm_plan):
        spec = json.loads(reconsolidation_item_spec(nvm_plan))
        spec["fusion_plan"]["proposed_label"] = "tampered after hashing"
        from app.services.reconsolidation.apply import (
            ReconsolidationApplyError,
        )
        with pytest.raises(ReconsolidationApplyError):
            parse_reconsolidation_spec(json.dumps(spec))
