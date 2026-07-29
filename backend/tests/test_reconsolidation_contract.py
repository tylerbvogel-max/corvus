"""mind-reconsolidation-kernel Phase 0 — frozen-incident contract tests.

Layer 1 (TestFrozenNvmIncident): assertions over the immutable NVM fixture in
tests/fixtures/nvm_incident/. These pass permanently — they are the receipts of
the failure modes the reconsolidation kernel exists to fix. If the fixture is
edited or re-frozen, the manifest test fails first.

Layer 2 (TestClosedGaps): inverted gap tests — the scenarios that once
demonstrated live defects (`test_gap_*`), now asserting the FIXED behavior
with the inversion date recorded. History of what was wrong lives in each
docstring. As of 2026-07-17 every Phase 0 gap test is inverted: embedding
regeneration on refine, zero-utility fusion membership, and FusionPlan-carried
component proposals (winner-take-all canonical selection retired).
"""

import hashlib
import json
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TENANT_ID", "corvus-mind")

import pytest

from tests.golden_frames import framed

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "nvm_incident"
CORE = {47, 51, 57, 177, 1151, 1161}
CONDUCTING = {"pyramidal", "stellate"}


def _load(name: str):
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


@pytest.fixture(scope="module")
def inv():
    return _load("invariants")


class TestFrozenNvmIncident:
    def test_manifest_integrity(self):
        manifest = (FIXTURE_DIR / "MANIFEST.sha256").read_text().strip().splitlines()
        assert len(manifest) == 14
        for line in manifest:
            digest, name = line.split()
            actual = hashlib.sha256((FIXTURE_DIR / name).read_bytes()).hexdigest()
            assert actual == digest, f"fixture file {name} drifted from freeze"

    def test_approved_is_not_applied(self, inv):
        props = {p["id"]: p for p in _load("autopilot_proposals")}
        for pid in (1067, 1069, 1071):
            assert props[pid]["state"] == "approved"
            assert props[pid]["applied_at"] is None, (
                f"proposal #{pid} looked resolved while recall served originals"
            )
        assert props[1099]["state"] == "applied"
        assert props[1099]["applied_at"] is not None
        assert inv["approved_unapplied_proposals"] == [1067, 1069, 1071]
        # No terminal superseded/stale disposition exists to retire them (Phase 4 gap).
        assert {p["state"] for p in props.values()} <= {"approved", "applied"}

    def test_invocation_inheritance_rules_all_disagree(self, inv):
        firings = [f for f in _load("neuron_firings") if f["neuron_id"] in CORE]
        summed = len(firings)
        per_member: dict[int, set] = {}
        for f in firings:
            per_member.setdefault(f["neuron_id"], set()).add(f["query_id"])
        max_member = max(len(q) for q in per_member.values())
        union = len(set().union(*per_member.values()))
        assert (summed, max_member, union) == (
            inv["summed_firing_rows"],
            inv["max_member_distinct_queries"],
            inv["union_distinct_queries"],
        )
        # sum double-counts co-delivery; max discards disjoint history.
        assert summed > union > max_member
        # Winner bias: canonical #57 kept only its own count, not the union.
        n57 = next(n for n in _load("neurons") if n["id"] == 57)
        assert n57["invocations"] == 318
        assert n57["invocations"] != union

    def test_internal_activation_edges_persist(self, inv):
        internal = [
            e for e in _load("neuron_edges")
            if e["source_id"] in CORE and e["target_id"] in CORE
            and e["edge_type"] in CONDUCTING
        ]
        assert len(internal) == inv["internal_activation_edges"] == 12
        active = {n["id"] for n in _load("neurons") if n["is_active"]}
        assert active & CORE == {57}
        # Every internal conducting edge touches an absorbed (inactive) member,
        # and the live canonical still conducts activation to its own absorbees.
        assert all(
            e["source_id"] not in active or e["target_id"] not in active
            for e in internal
        )
        pairs = {frozenset((e["source_id"], e["target_id"])) for e in internal}
        for absorbed in (177, 1151, 1161):
            assert frozenset((57, absorbed)) in pairs

    def test_pair_verdicts_do_not_compose(self):
        verdicts = {
            frozenset((v["neuron_a_id"], v["neuron_b_id"])): v["verdict"]
            for v in _load("mind_pair_verdicts")
        }
        # Transitivity violation: 51≡57 and 57≡177 duplicate, yet 51~177 not.
        assert verdicts[frozenset((51, 57))] == "duplicate-mis-scoped"
        assert verdicts[frozenset((57, 177))] == "duplicate-mis-scoped"
        assert verdicts[frozenset((51, 177))] == "complementary"
        # 47≡211 duplicate while 57/211 "genuinely-scoped" — although the
        # supersession chain records 211 -> 47 -> 57 as the same memory line.
        assert verdicts[frozenset((47, 211))] == "duplicate-mis-scoped"
        assert verdicts[frozenset((57, 211))] == "genuinely-scoped"
        by_id = {n["id"]: n for n in _load("neurons")}
        assert by_id[211]["superseded_by"] == 47
        assert by_id[47]["superseded_by"] == 57

    def test_stale_embedding_receipt(self, inv):
        # #1099 rewrote label+summary+content on 2026-07-17 …
        refined_fields = {
            r["field"] for r in _load("neuron_refinements")
            if r["neuron_id"] == 57 and r["created_at"].startswith("2026-07-17")
        }
        assert {"label", "summary", "content"} <= refined_fields
        # … but no embedding regeneration exists in any history table.
        assert "embedding" not in refined_fields
        assert not any(
            c["field"] == "embedding" for c in _load("memory_change_log")
        )
        # The frozen vector hash is the pre-refinement embedding riding new text.
        n57 = next(n for n in _load("neurons") if n["id"] == 57)
        actual = hashlib.sha256(n57["embedding"].encode()).hexdigest()
        assert actual == inv["neuron_embedding_sha256"]["57"]

    def test_stale_centrality_receipt(self):
        by_id = {n["id"]: n for n in _load("neurons")}
        # Derived topology still scores absorbed corpses above the living
        # canonical: inactive #51 outranks active #57.
        assert not by_id[51]["is_active"] and by_id[57]["is_active"]
        assert by_id[51]["centrality"] > by_id[57]["centrality"]
        for nid in (47, 51, 177, 211):
            assert not by_id[nid]["is_active"]
            assert by_id[nid]["centrality"] > 0.2


class TestClosedGaps:
    """Inverted gap tests — same scenarios, fixed behavior."""

    @pytest.mark.asyncio
    async def test_closed_gap_neuron_refine_regenerates_embedding(self):
        """INVERTED 2026-07-17 (was test_gap_neuron_refine_does_not_
        regenerate_embedding): content refinement now regenerates the
        embedding from the final text in the same apply, and refreshes
        the semantic cache. The old behavior left #57's improved text
        riding its pre-#1099 vector."""
        from app.models import Neuron
        from app.services.actions.neuron_refine import (
            NeuronRefineInput, handle_neuron_refine,
        )

        stale = json.dumps([0.1, 0.2, 0.3])
        fresh = [0.5, 0.6, 0.7]
        neuron = Neuron(
            id=57, label="old", content="old fact", summary="old",
            department="Environment", layer=3, node_type="lesson",
            embedding=stale,
        )
        db = MagicMock()
        db.get = AsyncMock(return_value=neuron)
        db.flush = AsyncMock()
        db.add = MagicMock(side_effect=lambda obj: setattr(obj, "id", 999))
        # The rewritten body must be a frame: neuron.refine enforces the
        # touched-neuron rule (mind-neuron-evidence-frame), so a content
        # rewrite that leaves the neuron unframed is refused outright.
        payload = NeuronRefineInput(
            target_neuron_id=57, field="content",
            old_value="old fact",
            new_value=framed(
                "entirely rewritten canonical fact",
                context="corrected — replaces the earlier imprecise statement.",
                evidence="session:test-refine; canonical rewrite under #1099.",
                future_use="A future agent needs the corrected fact, not the stale one.",
                likely_queries="What is the canonical fact here?",
            ),
        )
        cache = AsyncMock()
        embedding_module = ModuleType("app.services.embedding_service")
        embedding_module.embed_text = MagicMock(return_value=fresh)
        prefilter_module = ModuleType("app.services.semantic_prefilter")
        prefilter_module.update_cache_incremental = cache
        async def run_inline(fn, *args):
            return fn(*args)
        inline_loop = SimpleNamespace(
            run_in_executor=lambda _executor, fn, *args: run_inline(fn, *args),
        )
        with patch("app.services.reference_hooks.populate_external_references"), \
             patch("app.services.neuron_index.invalidate_index"), \
             patch(
                 "app.services.actions.neuron_refine.asyncio.get_running_loop",
                 return_value=inline_loop,
             ), \
             patch.dict(sys.modules, {
                 "app.services.embedding_service": embedding_module,
                 "app.services.semantic_prefilter": prefilter_module,
             }):
            result = await handle_neuron_refine(
                payload, SimpleNamespace(user_id="contract-test"), db, MagicMock(),
            )
        assert result["audit"]["refinement_id"] == 999
        assert result["audit"]["embedding_regenerated"] is True
        assert "Claim: entirely rewritten canonical fact" in neuron.content
        assert neuron.embedding == json.dumps(fresh)  # new text, NEW vector
        # Regenerated from the FINAL text (label. summary content recipe).
        embedded_text = embedding_module.embed_text.call_args[0][0]
        assert "entirely rewritten canonical fact" in embedded_text
        cache.assert_awaited_once_with(db, [57], "neuron")

    @pytest.mark.asyncio
    async def test_closed_gap_no_text_change_keeps_embedding(self):
        """Lifecycle-only refinements (is_active etc.) have no new text and
        must NOT re-embed."""
        from app.models import Neuron
        from app.services.actions.neuron_refine import (
            NeuronRefineInput, handle_neuron_refine,
        )

        stale = json.dumps([0.1, 0.2, 0.3])
        neuron = Neuron(id=51, label="l", content="c", layer=3,
                        node_type="lesson", embedding=stale, is_active=True)
        db = MagicMock()
        db.get = AsyncMock(return_value=neuron)
        db.flush = AsyncMock()
        db.add = MagicMock(side_effect=lambda obj: setattr(obj, "id", 1000))
        payload = NeuronRefineInput(
            target_neuron_id=51, field="is_active",
            old_value="true", new_value="false",
        )
        with patch("app.services.reference_hooks.populate_external_references"), \
             patch("app.services.neuron_index.invalidate_index"):
            result = await handle_neuron_refine(
                payload, SimpleNamespace(user_id="contract-test"), db, MagicMock(),
            )
        assert result["audit"]["embedding_regenerated"] is False
        assert neuron.embedding == stale

    @pytest.mark.asyncio
    async def test_query_age_provenance_can_be_repaired_through_refinement(self):
        """A synthesis created before total_queries was threaded through the
        action bus can be repaired without an unaudited direct DB write."""
        from app.models import Neuron
        from app.services.actions.neuron_refine import (
            NeuronRefineInput, handle_neuron_refine,
        )

        neuron = Neuron(
            id=1303, label="canonical", content="c", layer=3,
            node_type="lesson", created_at_query_count=0,
        )
        db = MagicMock()
        db.get = AsyncMock(return_value=neuron)
        db.flush = AsyncMock()
        db.add = MagicMock(side_effect=lambda obj: setattr(obj, "id", 1001))
        payload = NeuronRefineInput(
            target_neuron_id=1303, field="created_at_query_count",
            old_value="0", new_value="713",
        )
        with patch("app.services.reference_hooks.populate_external_references"), \
             patch("app.services.neuron_index.invalidate_index"):
            result = await handle_neuron_refine(
                payload, SimpleNamespace(user_id="contract-test"), db, MagicMock(),
            )
        assert neuron.created_at_query_count == 713
        assert result["audit"]["refinement_id"] == 1001
        assert result["audit"]["embedding_regenerated"] is False

    @pytest.mark.asyncio
    async def test_closed_gap_fuse_pair_membership_adds_zero_utility(self):
        """INVERTED 2026-07-17 (was test_gap_fuse_pair_grants_unearned_
        utility_boost): direct fusion no longer adds the +0.05 boost —
        consolidation is not evidence; membership grants zero utility.
        The confirmation flag survives as recorded provenance."""
        from app.models import Neuron
        from app.services import mind_janitors

        canonical = Neuron(id=57, label="canon", content="c", layer=3,
                           node_type="lesson", avg_utility=0.60, is_active=True)
        dup = Neuron(id=51, label="dup", content="d", layer=3,
                     node_type="lesson", avg_utility=0.50, is_active=True)
        db = MagicMock()
        db.add = MagicMock()
        with patch.object(mind_janitors, "_injected_in_session", return_value=False), \
             patch.object(mind_janitors, "_session_of", return_value=None), \
             patch.object(mind_janitors, "_add_memory_edge", new=AsyncMock()), \
             patch.object(mind_janitors, "_log_action"):
            detail = await mind_janitors._fuse_pair(db, canonical, dup)
        assert detail["confirmation"] is True  # provenance, not a boost
        assert canonical.avg_utility == pytest.approx(0.60)  # zero movement
        assert dup.is_active is False and dup.superseded_by == 57


    @pytest.mark.asyncio
    async def test_closed_gap_component_proposals_carry_fusion_plans(self):
        """INVERTED 2026-07-17 (was test_gap_canonical_selection_is_winner_
        biased): janitor component proposals now route through the kernel —
        ONE reviewed FusionPlan whose disposition comes from
        decide_disposition, not max(invocations, utility, id). For the NVM
        component the most prominent member (#57: 318 invocations, 0.92
        utility) does NOT keep identity: a multi-member component
        synthesizes NEW with canonical_neuron_id=None and union-distinct
        stats. The census max() survives only as the entry point that
        names the component; it decides nothing."""
        from app.services import mind_janitors
        from app.services.reconsolidation.apply import (
            parse_reconsolidation_spec,
        )
        from app.services.reconsolidation.plan import Disposition
        from tests.test_reconsolidation_apply import (
            CORE, _fixture_neurons, _golden_packet, _load,
        )

        neurons = _fixture_neurons()
        members = [neurons[i] for i in CORE]
        canonical = max(members, key=lambda n: ((n.invocations or 0),
                                                (n.avg_utility or 0.5), n.id))
        assert canonical.id == 57  # prominence still nominates ...
        dups = [m for m in members if m.id != 57]

        added: list = []
        db = MagicMock()
        db.add = MagicMock(side_effect=lambda obj: (
            added.append(obj), setattr(obj, "id", getattr(obj, "id", None) or 1300)))
        db.flush = AsyncMock()
        with patch("app.services.reconsolidation.review.review_component",
                   new=AsyncMock(return_value=_golden_packet())), \
             patch("app.services.reconsolidation.loaders.load_member_firings",
                   new=AsyncMock(return_value=_load("neuron_firings"))), \
             patch("app.services.reconsolidation.loaders.load_member_events",
                   new=AsyncMock(return_value=_load("synaptic_learning_events"))), \
             patch("app.services.reconsolidation.loaders.load_component_edges",
                   new=AsyncMock(return_value=_load("neuron_edges"))), \
             patch("app.services.reconsolidation.loaders.load_peer_context",
                   new=AsyncMock(return_value=({}, set()))), \
             patch("app.services.reconsolidation.loaders.load_peer_cofire_queries",
                   new=AsyncMock(return_value={})), \
             patch.object(mind_janitors, "_log_action"):
            detail = await mind_janitors._queue_component_proposal(
                db, canonical, dups, rescope=None, dup_info={})

        assert detail["outcome"] == "proposed"
        assert detail["disposition"] == "synthesize-new"
        from app.models import ProposalItem
        item = next(o for o in added if isinstance(o, ProposalItem))
        assert item.action == "reconsolidate"
        plan, _ph, _mh = parse_reconsolidation_spec(item.neuron_spec_json)
        # ... but decides nothing: no winner bias, union stats.
        assert plan.disposition is Disposition.SYNTHESIZE_NEW
        assert plan.canonical_neuron_id is None
        assert plan.inheritance.invocations_union_distinct == 419
        assert len(plan.rewiring.internal_activation_edges_to_retire) == 12
