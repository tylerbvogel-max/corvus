"""mind-reconsolidation-kernel Phase 3 + 4 — apply-side rewiring and the
one-step proposal lifecycle.

Same discipline as the Phase 1/2 suite: every decision under test here is
deterministic code; the DB and the action bus appear only as recorded
fakes, and the frozen NVM fixture pins the numbers (419 union-distinct,
12 internal conducting edges, proposals 1067/1069/1071 approved-unapplied).
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TENANT_ID", "corvus-mind")

import pytest

from app.models import AutopilotProposal, Neuron, ProposalItem
from app.services.reconsolidation import review as rv
from app.services.reconsolidation.apply import (
    ReconsolidationApplyError, parse_reconsolidation_spec, run_reconsolidation,
    synthesis_entities,
)
from app.services.reconsolidation.inheritance import (
    build_inheritance_preview, rewiring_preview,
)
from app.services.reconsolidation.lifecycle import (
    ProposalStaleError, approve_and_apply, mark_superseded,
    reconsolidation_item_spec, revalidate_items, supersede_stale_approved,
)
from app.services.reconsolidation.plan import Disposition
from app.services.reconsolidation.rewiring import plan_rewire_ops

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "nvm_incident"
CORE = [47, 51, 57, 177, 1151, 1161]
ALPHA, LOSS_PENALTY = 0.05, 0.3
PROMOTE = dict(promote_min_weight=0.10, promote_min_cofires=2)


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
                entities=r.get("entities"),
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
        "proposed_content": "NVM lives at ~/.config/nvm/nvm.sh. Explicitly "
                            "`source ~/.config/nvm/nvm.sh && nvm use "
                            "22.22.0` before Node tooling for the corvus "
                            "frontend, master-corvus, and "
                            "Market-Analytics-Suite. This is a machine "
                            "convention; repos may lack .nvmrc or engines "
                            "enforcement.",
        "proposed_scope": "Environment",
    }


def _golden_plan():
    members = list(_fixture_neurons().values())
    packet = _golden_packet()
    facets = rv.parse_facets(packet)
    member_rows = [r for r in _load("neurons") if r["id"] in set(CORE)]
    inheritance = build_inheritance_preview(
        member_rows, _load("neuron_firings"),
        _load("synaptic_learning_events"), facets,
        alpha=ALPHA, loss_penalty=LOSS_PENALTY)
    rewiring = rewiring_preview(CORE, _load("neuron_edges"))
    return rv.assemble_plan(members, packet, inheritance, rewiring)


# ── Phase 3: the pure rewiring op planner ───────────────────────────────

class TestRewireOps:
    def test_frozen_fixture_all_internal_conducting_edges_retire(self):
        ops = plan_rewire_ops(
            _load("neuron_edges"), CORE, synthesis_id=9999,
            external_peers=[], inactive_peer_ids=[], **PROMOTE)
        internal = [d for d in ops.deletes if d.reason == "internal"]
        assert len(internal) == 12
        # Memory-semantics rows are history: none appear in the delete set.
        deleted = {(d.source_id, d.target_id) for d in ops.deletes}
        for e in _load("neuron_edges"):
            if e["edge_type"] not in ("pyramidal", "stellate"):
                assert (e["source_id"], e["target_id"]) not in deleted

    def test_external_upserts_holder_order_and_tiering(self):
        edges = [
            {"source_id": 1, "target_id": 9, "edge_type": "pyramidal",
             "weight": 0.9},   # old weight must be irrelevant
            {"source_id": 2, "target_id": 8, "edge_type": "stellate"},
            {"source_id": 2, "target_id": 7, "edge_type": "pyramidal"},
        ]
        peers = [
            {"peer_id": 9, "union_cofire_queries": 3,
             "recomputed_weight": 0.15, "edge_type": "stellate"},
            {"peer_id": 8, "union_cofire_queries": 1,
             "recomputed_weight": 0.05, "edge_type": "pyramidal"},
            {"peer_id": 7, "union_cofire_queries": 0,
             "recomputed_weight": 0.0, "edge_type": "pyramidal"},
        ]
        ops = plan_rewire_ops(edges, [1, 2], synthesis_id=500,
                              external_peers=peers, inactive_peer_ids=[],
                              **PROMOTE)
        # member->peer conducting edges all retire; recreation is
        # synthesis-side only.
        assert {(d.source_id, d.target_id) for d in ops.deletes} == \
            {(1, 9), (2, 8), (2, 7)}
        by_peer = {u.target_id if u.source_id == 500 else u.source_id: u
                   for u in ops.upserts}
        # holder order: min id first — deterministic PK, no collisions.
        assert (by_peer[9].source_id, by_peer[9].target_id) == (9, 500)
        assert by_peer[9].promoted and by_peer[9].co_fire_count == 3
        assert by_peer[9].weight == pytest.approx(0.15)
        # union of 1 misses the co-fire threshold -> weak tier, not table.
        assert not by_peer[8].promoted
        # zero evidence -> NO edge, listed explicitly.
        assert 7 not in by_peer and ops.no_evidence_peers == [7]

    def test_replay_against_rewired_state_is_idempotent(self):
        post_state = [
            {"source_id": 47, "target_id": 9999, "edge_type": "evidence-link"},
            {"source_id": 9, "target_id": 9999, "edge_type": "stellate",
             "weight": 0.15, "co_fire_count": 3},
        ]
        peers = [{"peer_id": 9, "union_cofire_queries": 3,
                  "recomputed_weight": 0.15, "edge_type": "stellate"}]
        ops = plan_rewire_ops(post_state, CORE, synthesis_id=9999,
                              external_peers=peers, inactive_peer_ids=[],
                              **PROMOTE)
        # synthesis<->peer is external to the members but internal to the
        # component set {members, synthesis}? No: peer 9 is not a member,
        # so the recreated edge is member-side-only for peer 9 — it
        # touches the synthesis, which IS in the component. It must NOT
        # be deleted as internal (only edges with BOTH ends inside are).
        assert [d for d in ops.deletes if d.reason == "internal"] == []
        # The peer edge touches the component on the synthesis side only —
        # planner re-asserts the identical upsert (overwrite-idempotent).
        assert len(ops.upserts) == 1
        u = ops.upserts[0]
        assert (u.source_id, u.target_id, u.co_fire_count) == (9, 9999, 3)

    def test_unplanned_peer_edges_removed_loudly(self):
        edges = [{"source_id": 1, "target_id": 42, "edge_type": "pyramidal"}]
        ops = plan_rewire_ops(edges, [1], synthesis_id=500,
                              external_peers=[], inactive_peer_ids=[],
                              **PROMOTE)
        assert ops.unplanned_peers == [42]
        assert ops.deletes[0].reason == "unplanned-peer"


# ── Phase 4: staleness + terminal supersession ──────────────────────────

def _db_with_neurons(neurons: dict[int, Neuron]):
    db = MagicMock()

    async def _get(model, key):
        if model is Neuron:
            return neurons.get(key)
        return None

    db.get = AsyncMock(side_effect=_get)
    db.flush = AsyncMock()
    return db


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_revalidate_current_item_passes(self):
        n = Neuron(id=51, layer=3, is_active=True, superseded_by=None)
        p = AutopilotProposal(id=1, state="approved")
        p.items = [ProposalItem(id=10, proposal_id=1, action="update",
                                target_neuron_id=51, field="is_active",
                                old_value="true", new_value="false")]
        assert await revalidate_items(_db_with_neurons({51: n}), p) == []

    @pytest.mark.asyncio
    async def test_revalidate_flags_drift(self):
        # Pure field drift (no supersession): approved while the neuron
        # was active; something later deactivated it without absorbing
        # it. old_value 'true' no longer matches.
        n = Neuron(id=51, layer=3, is_active=False, superseded_by=None)
        p = AutopilotProposal(id=1067, state="approved")
        p.items = [ProposalItem(id=10, proposal_id=1067, action="update",
                                target_neuron_id=51, field="is_active",
                                old_value="true", new_value="false")]
        violations = await revalidate_items(_db_with_neurons({51: n}), p)
        assert len(violations) == 1 and "drifted" in violations[0]

    @pytest.mark.asyncio
    async def test_revalidate_flags_superseded_target_as_dead(self):
        # The 1069/1071 live receipt: a scope rescope whose old_value
        # still matches ('Projects') but whose target was absorbed by
        # #1099 — dead by identity, not by field drift.
        n = Neuron(id=51, layer=3, department="Projects", is_active=False,
                   superseded_by=57)
        p = AutopilotProposal(id=1069, state="approved")
        p.items = [ProposalItem(id=10, proposal_id=1069, action="update",
                                target_neuron_id=51, field="department",
                                old_value="Projects", new_value="Environment")]
        violations = await revalidate_items(_db_with_neurons({51: n}), p)
        assert len(violations) == 1 and "lives elsewhere" in violations[0]

    @pytest.mark.asyncio
    async def test_revalidate_allows_completing_absorption(self):
        # Auditor receipt #1181 (2026-07-18): #101 sat half-absorbed
        # (active + superseded_by set — the exact inconsistency the
        # auditor flagged) and the corpse rule killed the deactivation
        # that would complete it. is_active -> false on a superseded
        # target finishes absorption; it does not mutate the fact.
        n = Neuron(id=101, layer=3, is_active=True, superseded_by=174)
        p = AutopilotProposal(id=1181, state="approved")
        p.items = [ProposalItem(id=10, proposal_id=1181, action="update",
                                target_neuron_id=101, field="is_active",
                                old_value="true", new_value="false")]
        assert await revalidate_items(_db_with_neurons({101: n}), p) == []

    @pytest.mark.asyncio
    async def test_revalidate_still_blocks_corpse_reactivation(self):
        n = Neuron(id=101, layer=3, is_active=False, superseded_by=174)
        p = AutopilotProposal(id=1182, state="approved")
        p.items = [ProposalItem(id=10, proposal_id=1182, action="update",
                                target_neuron_id=101, field="is_active",
                                old_value="false", new_value="true")]
        violations = await revalidate_items(_db_with_neurons({101: n}), p)
        assert len(violations) == 1 and "lives elsewhere" in violations[0]

    @pytest.mark.asyncio
    async def test_revalidate_reconsolidate_item_uses_member_hashes(self):
        plan = _golden_plan()
        item = ProposalItem(id=11, proposal_id=2, action="reconsolidate",
                            neuron_spec_json=reconsolidation_item_spec(plan))
        p = AutopilotProposal(id=2, state="proposed")
        p.items = [item]
        neurons = _fixture_neurons()
        assert await revalidate_items(_db_with_neurons(neurons), p) == []
        # Drift one member's content -> the member hash breaks the plan.
        neurons[57].content = "rewritten after the plan was authored"
        violations = await revalidate_items(_db_with_neurons(neurons), p)
        assert violations and "drifted since plan" in violations[0]

    def test_mark_superseded_is_terminal(self):
        p = AutopilotProposal(id=1067, state="approved", review_notes=None)
        mark_superseded(MagicMock(), p, "member state died", "kernel-test")
        assert p.state == "superseded"
        assert "member state died" in p.review_notes
        with pytest.raises(AssertionError):
            mark_superseded(MagicMock(), p, "twice", "kernel-test")

    @pytest.mark.asyncio
    async def test_one_step_review_supersedes_stale_instead_of_applying(self):
        n = Neuron(id=51, layer=3, is_active=False, superseded_by=57)
        p = AutopilotProposal(id=5, state="proposed")
        p.items = [ProposalItem(id=10, proposal_id=5, action="update",
                                target_neuron_id=51, field="is_active",
                                old_value="true", new_value="false")]
        apply_mock = AsyncMock()
        with patch("app.services.proposal_apply_service."
                   "apply_approved_proposal", new=apply_mock):
            with pytest.raises(ProposalStaleError):
                await approve_and_apply(
                    _db_with_neurons({51: n}), p,
                    SimpleNamespace(user_id="tyler"))
        assert p.state == "superseded"
        apply_mock.assert_not_awaited()  # never applied against dead state

    @pytest.mark.asyncio
    async def test_one_step_review_approves_and_applies_in_one_call(self):
        n = Neuron(id=51, layer=3, is_active=True, superseded_by=None)
        p = AutopilotProposal(id=6, state="proposed")
        p.items = [ProposalItem(id=10, proposal_id=6, action="update",
                                target_neuron_id=51, field="is_active",
                                old_value="true", new_value="false")]
        apply_mock = AsyncMock(return_value=True)
        with patch("app.services.proposal_apply_service."
                   "apply_approved_proposal", new=apply_mock):
            has_edges = await approve_and_apply(
                _db_with_neurons({51: n}), p, SimpleNamespace(user_id="tyler"))
        assert has_edges is True
        apply_mock.assert_awaited_once()
        assert p.reviewed_by == "tyler" and p.reviewed_at is not None

    @pytest.mark.asyncio
    async def test_sweep_retires_stale_approved_rows(self):
        # 1067-shaped: approved, target drifted. Must land terminal.
        n = Neuron(id=51, layer=3, is_active=False, superseded_by=57)
        stale_p = AutopilotProposal(id=1067, state="approved")
        stale_p.items = [ProposalItem(id=1, proposal_id=1067, action="update",
                                      target_neuron_id=51, field="is_active",
                                      old_value="true", new_value="false")]
        current_p = AutopilotProposal(id=1200, state="approved")
        current_p.items = [ProposalItem(id=2, proposal_id=1200,
                                        action="update", target_neuron_id=51,
                                        field="superseded_by",
                                        old_value="57", new_value="58")]
        db = _db_with_neurons({51: n})
        rows = MagicMock()
        rows.scalars.return_value.all.return_value = [stale_p, current_p]
        db.execute = AsyncMock(return_value=rows)
        retired = await supersede_stale_approved(db, actor_id="kernel-test")
        assert [r["proposal_id"] for r in retired] == [1067]
        assert stale_p.state == "superseded"
        assert current_p.state == "approved"  # still valid -> untouched

    @pytest.mark.asyncio
    async def test_janitor_pass_commits_only_when_rows_retired(self):
        from app.services.mind_janitors import run_stale_approved_sweep

        db = MagicMock()
        db.commit = AsyncMock()
        with patch("app.services.reconsolidation.lifecycle."
                   "supersede_stale_approved",
                   new=AsyncMock(return_value=[])):
            report = await run_stale_approved_sweep(db)
        assert report == {"retired": []}
        db.commit.assert_not_awaited()  # empty sweep leaves no txn behind

        retired = [{"proposal_id": 1068, "violations": ["drifted"]}]
        with patch("app.services.reconsolidation.lifecycle."
                   "supersede_stale_approved",
                   new=AsyncMock(return_value=retired)):
            report = await run_stale_approved_sweep(db)
        assert report == {"retired": retired}
        db.commit.assert_awaited_once()


# ── the FusionPlan proposal payload ─────────────────────────────────────

class TestReconsolidationSpec:
    def test_spec_round_trip_and_tamper_detection(self):
        plan = _golden_plan()
        spec = reconsolidation_item_spec(plan)
        parsed, plan_hash, member_hash = parse_reconsolidation_spec(spec)
        assert parsed.plan_hash() == plan.plan_hash() == plan_hash
        assert member_hash == plan.member_state_hash()
        tampered = json.loads(spec)
        tampered["fusion_plan"]["proposed_content"] = "evil content"
        with pytest.raises(ReconsolidationApplyError):
            parse_reconsolidation_spec(json.dumps(tampered))

    def test_synthesis_entities_are_evidence_bounded(self):
        members = [SimpleNamespace(entities=["nvm", "corvus frontend"]),
                   SimpleNamespace(entities=["market-analytics-suite"]),
                   SimpleNamespace(entities=None)]
        final = "Use NVM for the corvus frontend on this machine."
        # Only entities still present in the final text survive; nothing
        # is invented.
        assert synthesis_entities(members, final) == \
            ["corvus frontend", "nvm"]


# ── orchestration: an approved plan applied end to end (recorded bus) ──

class _FakeBus:
    """Interprets child actions against an in-memory neuron store so the
    postcondition gather sees the mutations the real handlers would make."""

    def __init__(self, neurons: dict[int, Neuron], synthesis_id: int = 9999,
                 stats_sha: str = "fresh-sha-from-final-text"):
        self.neurons = neurons
        self.synthesis_id = synthesis_id
        self.stats_sha = stats_sha
        self.calls: list[tuple[str, dict]] = []

    async def submit(self, db, kind, actor, input_data, **kwargs):
        self.calls.append((kind, input_data))
        audit, payload = {}, {}
        if kind == "neuron.create":
            spec = input_data["spec"]
            self.neurons[self.synthesis_id] = Neuron(
                id=self.synthesis_id, layer=3, is_active=True,
                node_type=spec["node_type"], label=spec["label"],
                content=spec["content"], summary=spec["summary"],
                department=spec["department"],
                authority_level=spec["authority_level"], invocations=0)
            payload = {"neuron_id": self.synthesis_id}
        elif kind == "neuron.stats.rebuild":
            n = self.neurons[input_data["neuron_id"]]
            n.invocations = input_data["invocations"]
            n.avg_utility = input_data["avg_utility"]
            audit = {"embedding_sha256": self.stats_sha,
                     "embedding_regenerated": True}
        elif kind == "neuron.refine":
            n = self.neurons[input_data["target_neuron_id"]]
            if input_data["field"] == "is_active":
                n.is_active = input_data["new_value"] == "true"
            elif input_data["field"] == "superseded_by":
                n.superseded_by = int(input_data["new_value"])
        elif kind == "edge.rewire":
            audit = {"edges_removed": 12, "edges_upserted": 0}
        return SimpleNamespace(action_id=len(self.calls), state="applied",
                               payload=payload, audit=audit, error=None)


def _orchestration_db(neurons, proposal, stale_proposals):
    db = MagicMock()

    async def _get(model, key):
        if model is Neuron:
            return neurons.get(key)
        if model is AutopilotProposal:
            return proposal if key == proposal.id else None
        return None

    db.get = AsyncMock(side_effect=_get)
    db.flush = AsyncMock()
    stale_rows = MagicMock()
    stale_rows.scalars.return_value.all.return_value = stale_proposals
    count_row = MagicMock()
    count_row.scalar_one.return_value = 0  # rewire retired everything
    db.execute = AsyncMock(side_effect=[stale_rows, count_row])
    return db


class TestRunReconsolidation:
    def _setup(self):
        plan = _golden_plan()
        neurons = _fixture_neurons()
        proposal = AutopilotProposal(id=1200, state="approved")
        stale = [AutopilotProposal(id=pid, state="approved")
                 for pid in (1067, 1069, 1071)]
        return plan, neurons, proposal, stale

    async def _run(self, plan, neurons, proposal, stale, bus=None):
        bus = bus or _FakeBus(neurons)
        db = _orchestration_db(neurons, proposal, stale)
        with patch("app.services.action_bus.submit", new=bus.submit), \
             patch("app.services.consolidation.refresh_centrality",
                   new=AsyncMock(return_value=42)), \
             patch("app.services.adjacency_cache.invalidate_adjacency_cache"):
            receipt = await run_reconsolidation(
                db, plan, plan.member_state_hash(),
                proposal_id=1200, item_id=77,
                identity=SimpleNamespace(user_id="tyler"),
                actor_type="user", parent_action_id=1,
            )
        return receipt, bus

    @pytest.mark.asyncio
    async def test_golden_plan_applies_and_passes_postconditions(self):
        plan, neurons, proposal, stale = self._setup()
        receipt, bus = await self._run(plan, neurons, proposal, stale)

        kinds = [k for k, _ in bus.calls]
        assert kinds[0] == "neuron.create"
        assert kinds[1] == "neuron.stats.rebuild"
        assert kinds[-1] == "edge.rewire"
        # Union-distinct inheritance, never sum(809)/max(321).
        stats = next(d for k, d in bus.calls if k == "neuron.stats.rebuild")
        assert stats["invocations"] == 419
        # Every member — the prominent #57 included — deactivates and
        # points at the synthesis (no winner bias survives).
        assert all(not neurons[m].is_active for m in CORE)
        assert all(neurons[m].superseded_by == 9999 for m in CORE)
        # One provenance link per member.
        links = [d for k, d in bus.calls if k == "edge.link"]
        assert {d["source_id"] for d in links} == set(CORE)
        assert all(d["edge_type"] == "evidence-link" and
                   d["target_id"] == 9999 for d in links)
        # The approved-unapplied receipts retire terminally.
        assert all(p.state == "superseded" for p in stale)
        assert receipt["superseded_proposals"] == [1067, 1069, 1071]
        assert receipt["survivor_id"] == 9999
        assert receipt["invocations"] == 419

    @pytest.mark.asyncio
    async def test_member_inherited_embedding_fails_postconditions(self):
        plan, neurons, proposal, stale = self._setup()
        # An embedding sha equal to a member snapshot's = the vector was
        # inherited, not regenerated. Must fail closed.
        member_sha = next(s.embedding_sha256 for s in plan.member_snapshots
                          if s.embedding_sha256)
        bus = _FakeBus(neurons, stats_sha=member_sha)
        with pytest.raises(ReconsolidationApplyError) as exc:
            await self._run(plan, neurons, proposal, stale, bus=bus)
        assert any("inherited from a member" in v for v in exc.value.violations)

    @pytest.mark.asyncio
    async def test_drifted_member_fails_preflight_before_any_write(self):
        plan, neurons, proposal, stale = self._setup()
        neurons[57].content = "rewritten since the plan was approved"
        bus = _FakeBus(neurons)
        from app.services.reconsolidation.validators import PlanValidationError
        with pytest.raises(PlanValidationError):
            await self._run(plan, neurons, proposal, stale, bus=bus)
        assert bus.calls == []  # fail closed: nothing was submitted
