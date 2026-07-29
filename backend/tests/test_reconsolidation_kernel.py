"""mind-reconsolidation-kernel Phase 1 + 2 — component understanding,
coverage-delta synthesis, and field-specific inheritance.

Everything asserts against the frozen NVM fixture (tests/fixtures/
nvm_incident) so later traffic cannot move the numbers: 809 summed firing
rows / 321 max-member distinct / 419 UNION-distinct, 12 internal
conducting edges, 14 learning events over 13 distinct queries (query
2682 rewarded two members at once — the same evidence twice).

The LLM never appears in these tests except as a mocked proposer: every
decision — facet validity, coverage delta, disposition, every inherited
signal — is deterministic code under test here.
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("TENANT_ID", "corvus-mind")

import pytest

from app.models import Neuron
from app.services.reconsolidation import fingerprints as fp
from app.services.reconsolidation import review as rv
from app.services.reconsolidation.inheritance import (
    BIRTH_UTILITY, build_inheritance_preview, dedup_events, derive_authority,
    derive_dates, embedding_input, member_provenance_gaps, replay_utility,
    rewiring_preview, union_distinct_queries,
)
from app.services.reconsolidation.plan import Disposition, Facet, FacetKind
from app.services.reconsolidation.validators import (
    check_postconditions, preflight,
)
from tests.golden_frames import GOLDEN_NVM_CONTENT, TWO_MEMBER_NVM_CONTENT

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "nvm_incident"
CORE = [47, 51, 57, 177, 1151, 1161]
ALPHA, LOSS_PENALTY = 0.05, 0.3  # settings defaults, pinned for replay math


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
    # created_at as datetime-parseable strings via the row dicts below.
    return out


def _golden_packet() -> dict:
    """The packet an honest reviewer would produce for the NVM component —
    facet structure mirrors the Phase 0 golden plan."""
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
        # Framed per mind-neuron-evidence-frame: an honest reviewer now
        # produces the nine-slot frame. Same facts, same facet coverage,
        # same no-invention property — only the construction syntax moved.
        "proposed_content": GOLDEN_NVM_CONTENT,
        "proposed_scope": "Environment",
    }


@pytest.fixture()
def neurons():
    return _fixture_neurons()


@pytest.fixture()
def members(neurons):
    return [neurons[i] for i in CORE]


@pytest.fixture()
def member_rows():
    """Raw fixture dicts (carry created_at strings for date rules)."""
    return [r for r in _load("neurons") if r["id"] in set(CORE)]


# ── Phase 1A: fact fingerprints ─────────────────────────────────────────

class TestFingerprints:
    def test_nvm_member_signals_extracted(self, neurons):
        signals = fp.fingerprint(neurons[57])
        assert "path:~/.config/nvm/nvm.sh" in signals
        assert "version:22.22.0" in signals
        assert "cmd:nvm use" in signals
        assert "tool:nvm" in signals

    def test_prose_after_tool_name_is_not_a_command(self):
        # "node processes" is narrative; "nvm use" and "npm run" are commands.
        signals = fp.signals_of_text(
            "restart node processes, then nvm use 22.22.0 and npm run build")
        assert not any(s.startswith("cmd:node") for s in signals)
        assert "cmd:nvm use" in signals
        assert "cmd:npm run" in signals

    def test_all_nvm_pairs_nominate(self, neurons):
        pairs = [(51, 57), (47, 177), (1151, 1161), (47, 57), (51, 1161)]
        for a, b in pairs:
            assert fp.nominates(neurons[a], neurons[b]), (a, b)

    def test_unrelated_lesson_does_not_nominate(self, neurons):
        other = Neuron(
            id=999, layer=3, node_type="lesson",
            label="Meal planner serves the family dinner rotation",
            content="The meal-planner FastAPI app uses a diversity-aware "
                    "picker over a two-week window on port 8030.",
        )
        assert not fp.nominates(other, neurons[57])

    def test_lone_shared_version_does_not_nominate(self):
        a = Neuron(id=1, layer=3, node_type="lesson", label="tool a",
                   content="pinned at 1.2.3 for stability")
        b = Neuron(id=2, layer=3, node_type="lesson", label="tool b",
                   content="broke after upgrading past 1.2.3")
        assert not fp.nominates(a, b)


# ── Phase 1B: packet validation (fail closed) ───────────────────────────

class TestPacketValidation:
    def test_golden_packet_passes(self, members):
        assert rv.validate_packet(_golden_packet(), members) == []

    def test_invented_path_fails_closed(self, members):
        packet = _golden_packet()
        packet["proposed_content"] += " Also configured in /usr/local/nvm/nvm.sh."
        violations = rv.validate_packet(packet, members)
        assert any("invents" in v and "/usr/local/nvm/nvm.sh" in v
                   for v in violations)

    def test_invented_version_fails_closed(self, members):
        packet = _golden_packet()
        packet["proposed_content"] = packet["proposed_content"].replace(
            "22.22.0", "24.0.1")
        assert any("invents" in v
                   for v in rv.validate_packet(packet, members))

    def test_adjacent_fact_leakage_fails_closed(self):
        a = Neuron(id=1, layer=3, node_type="lesson", label="nvm fact",
                   content="source ~/.config/nvm/nvm.sh && nvm use 22.22.0; "
                           "the dev server runs on port 8004")
        b = Neuron(id=2, layer=3, node_type="lesson", label="nvm fact again",
                   content="source ~/.config/nvm/nvm.sh && nvm use 22.22.0")
        packet = {
            "facets": [
                {"kind": "invariant",
                 "text": "source ~/.config/nvm/nvm.sh && nvm use 22.22.0",
                 "evidence_member_ids": [1, 2]},
                {"kind": "adjacent",
                 "text": "dev server runs on port 8004",
                 "evidence_member_ids": [1]},
            ],
            "proposed_label": "nvm activation",
            "proposed_summary": "nvm use 22.22.0",
            # Leaks the adjacent port fact into the synthesis:
            "proposed_content": "source ~/.config/nvm/nvm.sh && nvm use "
                                "22.22.0 (dev server on port 8004)",
        }
        violations = rv.validate_packet(packet, [a, b])
        assert any("leaked" in v and "port:8004" in v for v in violations)

    def test_non_member_evidence_fails_closed(self, members):
        packet = _golden_packet()
        packet["facets"][0]["evidence_member_ids"] = [47, 999]
        assert any("non-member evidence" in v
                   for v in rv.validate_packet(packet, members))

    def test_unrepresented_member_fails_closed(self, members):
        packet = _golden_packet()
        for f in packet["facets"]:
            f["evidence_member_ids"] = [
                m for m in f["evidence_member_ids"] if m != 1151]
        assert any("[1151] evidence no facet" in v
                   for v in rv.validate_packet(packet, members))

    def test_empty_evidence_fails_closed(self, members):
        packet = _golden_packet()
        packet["facets"][0]["evidence_member_ids"] = []
        assert any("invented evidence" in v
                   for v in rv.validate_packet(packet, members))

    def test_invalid_kind_and_scope_fail_closed(self, members):
        packet = _golden_packet()
        packet["facets"][0]["kind"] = "vibes"
        packet["proposed_scope"] = "Cosmos"
        violations = rv.validate_packet(packet, members)
        assert any("invalid kind" in v for v in violations)
        assert any("not a known scope" in v for v in violations)

    def test_missing_content_fails_closed(self, members):
        packet = _golden_packet()
        packet["proposed_content"] = ""
        assert any("proposes no content" in v
                   for v in rv.validate_packet(packet, members))

    @pytest.mark.asyncio
    async def test_review_component_parses_mocked_reply(self, members):
        reply = {"text": "Here you go:\n" + json.dumps(_golden_packet())}
        with patch("app.services.llm_provider.llm_chat",
                   new=AsyncMock(return_value=reply)) as chat:
            packet = await rv.review_component(members)
        assert rv.validate_packet(packet, members) == []
        kwargs = chat.await_args.kwargs
        assert kwargs["model"] == rv.REVIEW_MODEL
        assert kwargs["effort"] == rv.REVIEW_EFFORT

    @pytest.mark.asyncio
    async def test_review_component_garbage_reply_fails_closed(self, members):
        with patch("app.services.llm_provider.llm_chat",
                   new=AsyncMock(return_value={"text": "no json here"})):
            with pytest.raises(rv.PacketValidationError):
                await rv.review_component(members)


# ── Phase 1C: coverage delta + disposition ──────────────────────────────

def _facet(kind, text, evidence, resolution=None):
    return Facet(kind=kind, text=text, evidence_member_ids=evidence,
                 resolution=resolution)


def _member(id, dept="Environment", invocations=0, utility=0.5):
    return SimpleNamespace(id=id, department=dept, invocations=invocations,
                           avg_utility=utility, node_type="lesson")


class TestDisposition:
    def test_multi_member_always_synthesizes_new(self):
        members = [_member(51, invocations=137), _member(57, invocations=318),
                   _member(177, invocations=285)]
        facets = [_facet(FacetKind.INVARIANT, "the fact", [51, 57, 177])]
        disposition, canonical = rv.decide_disposition(
            members, facets, coverage_delta=False)
        assert disposition is Disposition.SYNTHESIZE_NEW
        assert canonical is None  # winner-take-all forbidden

    def test_strict_two_member_restatement_retains_strongest(self):
        members = [_member(51, invocations=137), _member(57, invocations=318)]
        facets = [_facet(FacetKind.INVARIANT, "the fact", [51, 57])]
        disposition, canonical = rv.decide_disposition(
            members, facets, coverage_delta=False)
        assert disposition is Disposition.RETAIN_CANONICAL
        assert canonical == 57

    def test_cross_scope_two_member_synthesizes_new(self):
        members = [_member(51, dept="Projects"), _member(57)]
        facets = [_facet(FacetKind.INVARIANT, "the fact", [51, 57])]
        assert rv.decide_disposition(members, facets, False)[0] \
            is Disposition.SYNTHESIZE_NEW

    def test_scope_correction_synthesizes_new(self):
        members = [_member(51, dept="Projects"), _member(57, dept="Projects")]
        facets = [_facet(FacetKind.INVARIANT, "the fact", [51, 57])]
        assert rv.decide_disposition(
            members, facets, False, proposed_scope="Environment")[0] \
            is Disposition.SYNTHESIZE_NEW

    def test_coverage_delta_synthesizes_new_even_for_two(self):
        members = [_member(51), _member(57)]
        facets = [_facet(FacetKind.INVARIANT, "the fact", [51, 57])]
        assert rv.decide_disposition(members, facets, True)[0] \
            is Disposition.SYNTHESIZE_NEW

    def test_unresolved_conflict_abstains(self):
        members = [_member(51), _member(57)]
        facets = [_facet(FacetKind.CONFLICT, "disagree on version", [51, 57])]
        assert rv.decide_disposition(members, facets, False)[0] \
            is Disposition.ABSTAIN

    def test_resolved_conflict_synthesizes_new(self):
        members = [_member(51), _member(57)]
        facets = [
            _facet(FacetKind.INVARIANT, "the fact", [51, 57]),
            _facet(FacetKind.CONFLICT, "disagree on version", [51, 57],
                   resolution="member texts show 22.22.0 superseded 20.x"),
        ]
        assert rv.decide_disposition(members, facets, False)[0] \
            is Disposition.SYNTHESIZE_NEW

    def test_coverage_delta_rule(self):
        covered = [
            _facet(FacetKind.INVARIANT, "a", [51, 57]),
            _facet(FacetKind.CAVEAT, "b", [57]),
        ]
        # 57 evidences every substantive facet — synthesis adds nothing.
        assert rv.compute_coverage_delta(covered) is False
        spread = covered + [_facet(FacetKind.EXAMPLE, "c", [51])]
        assert rv.compute_coverage_delta(spread) is True
        # Adjacent facts never create coverage.
        adjacent_only = [_facet(FacetKind.ADJACENT, "port", [51])]
        assert rv.compute_coverage_delta(adjacent_only) is False


# ── Phase 2: field-specific inheritance ─────────────────────────────────

class TestInheritance:
    def test_union_distinct_is_the_only_correct_rule(self):
        firings = _load("neuron_firings")
        core = [f for f in firings if f["neuron_id"] in set(CORE)]
        union = union_distinct_queries(CORE, firings)
        assert len(union) == 419                       # frozen acceptance
        assert len(core) == 809                        # sum double-counts
        per_member: dict[int, set] = {}
        for f in core:
            per_member.setdefault(f["neuron_id"], set()).add(f["query_id"])
        assert max(len(q) for q in per_member.values()) == 321  # max discards

    def test_same_query_evidence_counts_once(self):
        kept, deduped, discounted = dedup_events(
            _load("synaptic_learning_events"), CORE)
        assert len(kept) == 13 and deduped == 1 and discounted == 0
        # Query 2682 rewarded #57 and #177 in the same eval — one keeps.
        assert sum(1 for e in kept if e["query_id"] == 2682) == 1

    def test_replay_from_birth_prior(self):
        kept, _, _ = dedup_events(_load("synaptic_learning_events"), CORE)
        replayed = replay_utility(kept, ALPHA, LOSS_PENALTY)
        # 13 unit-weight wins through the normal diminishing-returns rule.
        expected = BIRTH_UTILITY
        for _ in range(13):
            expected += ALPHA * (1.0 - expected)
        assert replayed == pytest.approx(expected, abs=1e-6)

    def test_membership_adds_zero(self):
        assert replay_utility([], ALPHA, LOSS_PENALTY) == BIRTH_UTILITY

    def test_provenance_gaps_reported_not_laundered(self, member_rows):
        gaps = member_provenance_gaps(
            member_rows, _load("synaptic_learning_events"),
            ALPHA, LOSS_PENALTY)
        flagged = {int(g.split("#")[1].split()[0]) for g in gaps}
        # #57's 0.92 (legacy boosts), #47/#51/#177 (pre-event history /
        # flat-delta legacy events) are gaps; #1151/#1161 sit clean at birth.
        assert flagged == {47, 51, 57, 177}
        assert any("0.920" in g for g in gaps if "#57" in g)

    def test_authority_derived_from_support_never_promoted(self, members):
        facets = rv.parse_facets(_golden_packet())
        # #57 (guidance) supports an invariant -> guidance carries over.
        assert derive_authority(members, facets) == "guidance"
        # Without #57 in any invariant, informational members can't be
        # promoted by consolidation itself.
        stripped = [
            Facet(kind=f.kind, text=f.text, resolution=f.resolution,
                  evidence_member_ids=[m for m in f.evidence_member_ids
                                       if m != 57] or [51])
            for f in facets
        ]
        assert derive_authority(members, stripped) == "informational"

    def test_dates_from_evidence(self, member_rows):
        kept, _, _ = dedup_events(_load("synaptic_learning_events"), CORE)
        effective, last_verified = derive_dates(member_rows, kept)
        assert effective == "2026-07-10"          # earliest member evidence
        assert last_verified.startswith("2026-07-16T16:38:33")
        # No replayable events -> no verification claim.
        assert derive_dates(member_rows, [])[1] is None

    def test_embedding_input_parity_with_creation(self):
        assert embedding_input("L", "S", "C") == "L. S C"
        assert embedding_input("L", None, None) == "L.  "
        assert len(embedding_input("L", "x" * 3000, None)) == 2000

    def test_full_preview_from_frozen_fixture(self, member_rows):
        preview = build_inheritance_preview(
            member_rows, _load("neuron_firings"),
            _load("synaptic_learning_events"),
            rv.parse_facets(_golden_packet()),
            alpha=ALPHA, loss_penalty=LOSS_PENALTY)
        assert preview.invocations_union_distinct == 419
        assert preview.utility_events_replayed == 13
        assert preview.utility_events_deduped == 1
        assert len(preview.utility_provenance_gaps) == 4
        assert preview.authority_level == "guidance"
        assert preview.effective_date == "2026-07-10"
        assert preview.embedding_action == "regenerate-from-final-text"
        assert preview.centrality_action == "recompute-after-rewiring"

    def test_discounted_evidence_dropped_and_reported(self, member_rows):
        preview = build_inheritance_preview(
            member_rows, _load("neuron_firings"),
            _load("synaptic_learning_events"),
            rv.parse_facets(_golden_packet()),
            alpha=ALPHA, loss_penalty=LOSS_PENALTY,
            discounted_query_ids={2682})
        assert preview.utility_events_replayed == 12
        assert any("2 injected/self-reinforced" in g
                   for g in preview.utility_provenance_gaps)


# ── Phase 3 planning half: rewiring preview ─────────────────────────────

class TestRewiringPreview:
    def test_frozen_fixture_internal_edges_and_provenance(self):
        preview = rewiring_preview(CORE, _load("neuron_edges"))
        assert len(preview.internal_activation_edges_to_retire) == 12
        links = preview.provenance_links_to_create
        assert [(l.source_id, l.target_id, l.edge_type) for l in links] == \
            [(m, 0, "evidence-link") for m in sorted(CORE)]
        # No peer evidence supplied -> unions are explicitly zero, never
        # silently dropped or inherited from old weights.
        assert all(p.union_cofire_queries == 0 for p in preview.external_peers)

    def test_external_peer_weight_from_union_not_from_old_weight(self):
        edges = [
            {"source_id": 1, "target_id": 2, "edge_type": "pyramidal"},
            {"source_id": 2, "target_id": 1, "edge_type": "stellate"},
            # Old weight 0.9 must NOT survive into the recomputation.
            {"source_id": 1, "target_id": 9, "edge_type": "pyramidal",
             "weight": 0.9},
            {"source_id": 2, "target_id": 9, "edge_type": "pyramidal",
             "weight": 0.7},
            {"source_id": 2, "target_id": 77, "edge_type": "stellate"},
            {"source_id": 1, "target_id": 3, "edge_type": "evidence-link"},
        ]
        preview = rewiring_preview(
            [1, 2], edges, synthesis_id=500, final_scope="Environment",
            peer_scopes={9: "Environment"},
            # Overlapping co-fire histories: {10,11} ∪ {11,12} -> 3 unique.
            peer_cofire_queries={9: {10, 11, 12}},
            active_peer_ids={9})
        assert len(preview.internal_activation_edges_to_retire) == 2
        assert preview.inactive_peers_dropped == [77]
        (peer,) = preview.external_peers
        assert peer.peer_id == 9
        assert peer.union_cofire_queries == 3
        assert peer.recomputed_weight == pytest.approx(3 / 20.0)
        assert peer.edge_type == "stellate"  # from FINAL scopes
        links = {(l.source_id, l.target_id)
                 for l in preview.provenance_links_to_create}
        assert links == {(1, 500), (2, 500)}


# ── Phase 1A write gate: lexical lane ───────────────────────────────────

class TestWriteGateLexicalLane:
    def _member_57(self):
        rows = _load("neurons")
        r = next(n for n in rows if n["id"] == 57)
        return Neuron(id=57, label=r["label"], content=r["content"],
                      summary=r["summary"], department=r["department"],
                      layer=r["layer"], node_type=r["node_type"],
                      embedding=json.dumps([1.0, 0.0]), is_active=True)

    async def _gate(self, spec, candidate_vec):
        from app.services.lesson_store import _nearest_active_lesson
        with patch("app.services.embedding_service.embed_text",
                   return_value=candidate_vec), \
             patch("app.services.mind_janitors._load_lessons",
                   new=AsyncMock(return_value=[self._member_57()])):
            return await _nearest_active_lesson(MagicMock(), spec)

    @pytest.mark.asyncio
    async def test_sub_fuse_sim_paraphrase_queues_via_lexical_lane(self):
        # cosine 0.7: judged-duplicate territory (NVM pairs sat at
        # 0.757-0.822) that the old 0.88-only gate silently inserted.
        spec = {"label": "Node runtime activation on this Chromebook",
                "content": "Run `source ~/.config/nvm/nvm.sh && nvm use "
                           "22.22.0` before npm commands.",
                "summary": None}
        near = await self._gate(spec, [0.7, (1 - 0.49) ** 0.5])
        assert near is not None
        assert near["lane"] == "lexical" and near["id"] == 57
        assert 0.60 <= near["sim"] < 0.88

    @pytest.mark.asyncio
    async def test_related_topic_without_shared_facts_still_inserts(self):
        # Same 0.7 cosine but no shared concrete signals: not a duplicate,
        # must NOT queue — the cosine bar is not being lowered by stealth.
        spec = {"label": "Vite dev server needs a manual restart",
                "content": "When frontend changes don't appear, restart "
                           "the dev server.",
                "summary": None}
        assert await self._gate(spec, [0.7, (1 - 0.49) ** 0.5]) is None

    @pytest.mark.asyncio
    async def test_cosine_lane_still_wins_at_fuse_sim(self):
        spec = {"label": "NVM and Node 22 runtime convention",
                "content": "source ~/.config/nvm/nvm.sh && nvm use 22.22.0",
                "summary": None}
        near = await self._gate(spec, [0.95, (1 - 0.9025) ** 0.5])
        assert near is not None and near["lane"] == "cosine"


# ── end to end: packet -> plan -> acceptance bar ────────────────────────

class TestPlanAssembly:
    def test_nvm_plan_meets_the_acceptance_bar(self, members, member_rows):
        packet = _golden_packet()
        facets = rv.parse_facets(packet)
        inheritance = build_inheritance_preview(
            member_rows, _load("neuron_firings"),
            _load("synaptic_learning_events"), facets,
            alpha=ALPHA, loss_penalty=LOSS_PENALTY)
        rewiring = rewiring_preview(CORE, _load("neuron_edges"))
        plan = rv.assemble_plan(members, packet, inheritance, rewiring)

        assert plan.disposition is Disposition.SYNTHESIZE_NEW
        assert plan.canonical_neuron_id is None
        assert plan.coverage_delta is True
        assert plan.proposed_department == "Environment"
        assert plan.inheritance.invocations_union_distinct == 419
        assert len(plan.rewiring.internal_activation_edges_to_retire) == 12

        # Preflight: the plan applies cleanly against unchanged members.
        assert preflight(
            plan, {m.id: m for m in members},
            approved_member_state_hash=plan.member_state_hash(),
            proposal_state="approved") == []

        # check_postconditions is the acceptance bar: a correct apply of
        # THIS plan (one new active synthesis, zero internal conducting
        # edges, union-distinct stats, fresh embedding, terminally
        # superseded stale proposals) passes clean.
        assert check_postconditions(
            plan,
            active_representation_ids={0},
            internal_conducting_edges=0,
            synthesis_invocations=419,
            synthesis_embedding_sha256="regenerated-from-final-text",
            stale_proposal_states={1067: "superseded", 1069: "superseded",
                                   1071: "superseded"}) == []

    def test_invalid_packet_never_becomes_a_plan(self, members):
        packet = _golden_packet()
        packet["proposed_content"] += " See also /etc/invented/path.conf."
        with pytest.raises(rv.PacketValidationError):
            rv.assemble_plan(members, packet, None, None)

    def test_two_member_restatement_keeps_canonical_identity(self):
        a = Neuron(id=1, layer=3, node_type="lesson", label="nvm activation",
                   content="source ~/.config/nvm/nvm.sh && nvm use 22.22.0",
                   department="Environment", invocations=10, avg_utility=0.6,
                   is_active=True)
        b = Neuron(id=2, layer=3, node_type="lesson", label="nvm activation again",
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
        assert plan.disposition is Disposition.RETAIN_CANONICAL
        assert plan.canonical_neuron_id == 1
        # Identity is retained: no new proposed identity fields.
        assert plan.proposed_label is None
