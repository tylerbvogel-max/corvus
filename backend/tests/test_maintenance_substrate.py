"""Relocation gates for the maintenance substrate (record 04b).

Record 04b broke the last import cycle by moving shared vocabulary DOWN out
of three modules: the corpus primitives out of ``mind_janitors`` into
``mind_corpus``, ``CHARTER_TIERS`` into ``delivery_mode``, and the FusionPlan
wire format into ``reconsolidation.plan``.

That record's failure mode was never a broken import — it was a SILENTLY
WIDENED GATE. Two things could have gone wrong quietly:

  1. a relocated gate stops biting (a second definition drifts from the
     first, so the lint's verdict store and a FusionPlan's drift check no
     longer agree about what "unchanged" means);
  2. the new shared module, imported by nearly everything, becomes a
     convenient place for an ungoverned write to hide.

These tests plant both. They are cheap and hermetic on purpose: the guard
that only runs when someone remembers to run a replay script is not a guard.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1]


# ── 1. one definition, not two ─────────────────────────────────────────────

@pytest.mark.hermetic
def test_content_hash_is_a_single_object_everywhere_it_is_used():
    """The lint's verdict store and plan.snapshot_of MUST agree bit-for-bit.

    Before 04b the parity was a comment ("parity with mind_lint.content_hash")
    and plan reached UP through mind_lint to honour it — which was that
    module's only tie to the cycle. Now it lives below both. If someone ever
    re-defines it locally, drift detection and verdict staleness silently
    disagree and a stale verdict can nominate a plan the preflight thinks is
    current. Identity, not equality: two functions that agree today are not
    the same guarantee.
    """
    from app.services import mind_corpus, mind_lint
    from app.services.reconsolidation import plan as plan_mod

    assert mind_lint.content_hash is mind_corpus.content_hash
    src = (BACKEND / "app/services/reconsolidation/plan.py").read_text()
    assert "from app.services.mind_corpus import content_hash" in src, \
        "plan.snapshot_of must take the hash from the shared primitive"
    assert "hashlib.sha256(text.encode" not in src, \
        "plan.py re-implemented content_hash instead of importing it"
    assert plan_mod.snapshot_of  # the consumer still exists


@pytest.mark.hermetic
def test_fusionplan_wire_format_is_one_object_at_every_name():
    """Serializer and deserializer were in different modules importing each
    other back. They are now one pair beside the hashes they verify, and the
    old names are re-exports — not copies."""
    from app.services.reconsolidation import apply as apply_mod
    from app.services.reconsolidation import lifecycle as life_mod
    from app.services.reconsolidation import plan as plan_mod

    assert apply_mod.parse_reconsolidation_spec is plan_mod.parse_reconsolidation_spec
    assert life_mod.parse_reconsolidation_spec is plan_mod.parse_reconsolidation_spec
    assert life_mod.reconsolidation_item_spec is plan_mod.reconsolidation_item_spec
    assert apply_mod.ReconsolidationApplyError is plan_mod.ReconsolidationApplyError


@pytest.mark.hermetic
def test_janitor_re_exports_are_the_same_objects_not_copies():
    """Eight modules outside the cycle, and several test seams that
    monkeypatch these names on mind_janitors, still resolve them there."""
    from app.services import mind_corpus, mind_janitors as mj

    for name in ("EPISODE_DIR", "ACTIONS_LOG", "LESSON_TYPES", "FUSE_SIM",
                 "BORDERLINE_SIM"):
        assert getattr(mj, name) == getattr(mind_corpus, name), name
    for name in ("_log_action", "_load_lessons", "_similar_pairs",
                 "_add_memory_edge"):
        assert getattr(mj, name) is getattr(mind_corpus, name), name


# ── 2. the substrate must never become a hiding place for a write ─────────

@pytest.mark.hermetic
def test_the_shared_substrate_never_assigns_a_protected_column():
    """mind_corpus is imported by nearly everything in the memory tenant.

    A module that widely shared must stay read/compute plus governed Action
    Bus calls only. The classification pass put the direct ORM writes to
    protected columns in mind_janitors and they must stay there: a write
    that moved down here would inherit the substrate's reach and would look
    like a primitive.
    """
    register = json.loads(
        (BACKEND.parent / "architecture" / "graph_writers.json").read_text())
    protected = set(register["protected_columns"])
    tree = ast.parse((BACKEND / "app/services/mind_corpus.py").read_text())

    offenders = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AugAssign):
            targets = [node.target]
        for t in targets:
            if isinstance(t, ast.Attribute) and t.attr in protected:
                offenders.append((getattr(node, "lineno", "?"), t.attr))
    assert not offenders, (
        f"mind_corpus.py assigns protected column(s) {offenders}. Authored "
        "content and the supersession lifecycle change through the action bus, "
        "not through a shared primitive.")


@pytest.mark.hermetic
def test_the_shared_substrate_constructs_no_graph_rows():
    """Companion to the sole-writer register: the substrate creates no
    Neuron/NeuronEdge. Its one write helper submits edge.link to the bus."""
    tree = ast.parse((BACKEND / "app/services/mind_corpus.py").read_text())
    built = [n.func.id for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id in {"Neuron", "NeuronEdge"}]
    assert not built, f"mind_corpus constructs graph rows: {built}"
    src = (BACKEND / "app/services/mind_corpus.py").read_text()
    assert "action_bus.submit" in src, \
        "the memory-edge helper must still route through the action bus"


# ── 3. planted adversarial cases: the relocated gates still bite ──────────

def _framed(claim: str, context: str) -> str:
    return "\n".join([
        f"Claim: {claim}",
        "Entities: Node",
        "Time scope: stable-preference (2026-08-01)",
        f"Context: {context}",
        "Evidence: [session:04b-guard] planted by a test",
        "Future-use: when a relocated gate is checked",
        "Likely queries: does the relocated gate still refuse?",
        "Confidence: high",
        "Volatility: stable",
    ])


def _two_member_plan():
    """A minimal appliable FusionPlan built through the real assembler."""
    from types import SimpleNamespace

    from app.services.reconsolidation import review as rv
    from app.services.reconsolidation.inheritance import (
        build_inheritance_preview, rewiring_preview)

    members = [
        SimpleNamespace(id=9001, label="Probe runtime", node_type="lesson",
                        content=_framed("Probe uses Node 22.", "baseline"),
                        summary="Probe uses Node 22", department="Environment",
                        is_active=True, superseded_by=None, invocations=5,
                        avg_utility=0.6, authority_level="informational",
                        embedding=None, entities=[], created_at=None),
        SimpleNamespace(id=9002, label="Probe runtime restated",
                        node_type="lesson",
                        content=_framed("Probe uses Node 22.", "restatement"),
                        summary="Probe pins Node 22", department="Environment",
                        is_active=True, superseded_by=None, invocations=2,
                        avg_utility=0.5, authority_level="informational",
                        embedding=None, entities=[], created_at=None),
    ]
    packet = {
        "facets": [{"kind": "invariant", "text": "Probe uses Node 22.",
                    "evidence_member_ids": [9001, 9002], "resolution": None}],
        "proposed_label": "Probe runtime",
        "proposed_summary": "Probe uses Node 22",
        "proposed_content": _framed(
            "Probe uses Node 22.", "reinforced: absorbs two restatements"),
        "proposed_scope": "Environment",
    }
    facets = rv.parse_facets(packet)
    inheritance = build_inheritance_preview(members, [], [], facets)
    rewiring = rewiring_preview([9001, 9002], [])
    return members, rv.assemble_plan(members, packet, inheritance, rewiring)


@pytest.mark.hermetic
def test_planted_tampered_plan_is_refused_by_the_relocated_parser():
    """Smuggle different content into a serialized plan. The hash gate that
    moved into plan.py must refuse it before anything reaches preflight."""
    from app.services.reconsolidation.plan import (
        ReconsolidationApplyError, parse_reconsolidation_spec,
        reconsolidation_item_spec)

    _members, plan = _two_member_plan()
    spec = json.loads(reconsolidation_item_spec(plan))
    spec["fusion_plan"]["proposed_content"] = _framed(
        "Probe uses Node 99.", "reinforced: SMUGGLED")

    with pytest.raises(ReconsolidationApplyError) as exc:
        parse_reconsolidation_spec(json.dumps(spec))
    assert any("plan_hash" in v for v in exc.value.violations)


@pytest.mark.hermetic
def test_planted_content_drift_is_refused_by_the_relocated_hash():
    """Change a member AFTER the plan pinned it. preflight computes the live
    hash through the relocated primitive; if that ever stops matching what
    the plan recorded, an apply could proceed against text nobody reviewed."""
    from app.services.reconsolidation.validators import preflight

    members, plan = _two_member_plan()
    clean = preflight(plan, {m.id: m for m in members})
    assert not [v for v in clean if "drifted" in v], clean

    members[0].content = _framed("Probe uses Node 18.", "DRIFTED")
    violations = preflight(plan, {m.id: m for m in members})
    assert any("content drifted" in v for v in violations), violations


@pytest.mark.hermetic
@pytest.mark.asyncio
async def test_planted_ungoverned_edge_type_is_refused_by_the_substrate():
    """The relocated memory-edge helper only asserts memory semantics.
    A conducting edge smuggled through it would materialize activation
    topology outside the learning rules that own it."""
    from app.services.mind_corpus import _add_memory_edge

    with pytest.raises(AssertionError, match="not a memory edge type"):
        await _add_memory_edge(None, 9001, 9002, "pyramidal", "smuggled")


# ── 3. the non-conducting class is one object, everywhere it is used ───────

@pytest.mark.hermetic
def test_non_conducting_class_is_the_single_definition():
    """Record fix-classify-edges-taxonomy: classify_edges destroyed 507
    memory-semantics edges because its exclusion was a hand-list written
    before those types existed. The fix derives every consumer from
    mind_corpus.NON_CONDUCTING_EDGE_TYPES; this pins the consumers that
    still carry their own spelling so drift cannot reopen the hole.
    """
    from app.services.actions.edge_link import _AUTHORITATIVE_RELATIONSHIP_TYPES
    from app.services.adjacency_cache import _ETYPE_CODE
    from app.services.mind_corpus import (
        MEMORY_EDGE_TYPES, NON_CONDUCTING_EDGE_TYPES,
    )

    assert set(NON_CONDUCTING_EDGE_TYPES) == set(MEMORY_EDGE_TYPES) | {"instantiates"}

    # adjacency_cache's CSR map codes exactly this class as non-conducting
    # (2 = instantiates, 3 = memory semantics; 1/0 conduct).
    csr_non_conducting = {k for k, v in _ETYPE_CODE.items() if v in (2, 3)}
    assert csr_non_conducting == set(NON_CONDUCTING_EDGE_TYPES), (
        f"adjacency_cache codes {sorted(csr_non_conducting)} as non-conducting "
        f"but the shared class says {sorted(NON_CONDUCTING_EDGE_TYPES)}"
    )

    # edge_link's authoritative-relationship set is the same class.
    assert set(_AUTHORITATIVE_RELATIONSHIP_TYPES) == set(NON_CONDUCTING_EDGE_TYPES)


@pytest.mark.hermetic
def test_classify_edges_excludes_the_class_not_a_hand_list():
    """The regression pin for the 2026-08-01 incident itself.

    classify_edges' WHERE must reference the expanding :non_conducting
    parameter — a literal exclusion (the old ``!= 'instantiates'``) is the
    exact defect. And the source must take the class from mind_corpus, not
    define a local copy that would rot the same way the original did.
    """
    src = (BACKEND / "app/routers/admin_graph_maintenance.py").read_text()
    assert ":non_conducting" in src, "classify_edges lost the class parameter"
    assert "!= 'instantiates'" not in src, "the hand-list exclusion is back"
    assert "from app.services.mind_corpus import NON_CONDUCTING_EDGE_TYPES" in src, (
        "the exclusion no longer derives from the shared definition"
    )
