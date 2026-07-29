"""mind-neuron-evidence-frame — the construction contract, adversarially.

Step 06 (2026-07-28) closed the retrieve-more door: second-hop retrieval
added memories but Oracle Funnel measured 0% assembly loss, so the misses
were topically-right neurons that could not reconstruct an answer. These
tests assert the contract that makes that shape expensive to CREATE.

The adversarial cases are the point. A validator that only accepts good
frames is worthless; what matters is that the plausible-looking near-miss
— prose that reads like a lesson, a frame missing its receipt, a volatile
fact labelled stable — cannot reach the graph.
"""

import os

os.environ.setdefault("TENANT_ID", "corvus-mind")

import pytest

from app.services.evidence_frame import (
    CANONICAL_SLOTS, EvidenceFrame, EvidenceFrameError, build_frame, enforce,
    is_framed, parse_frame, requires_frame, validate_frame,
    validate_fusion_frame,
)

VALID = build_frame(
    claim="Node 22.22.0 is required for master-corvus; Node 20 fails the Vite build.",
    entities="master-corvus, Node, Vite",
    time_scope="stable-preference",
    context="unknown",
    evidence="session:abc123; npm run build exited 1 under v20.20.0, exit 0 under v22.22.0",
    future_use="Prevents a future agent burning a cycle on a version-mismatch build failure.",
    likely_queries="Which Node version does master-corvus need?",
    confidence="high",
    volatility="stable",
)


def _without(slot: str) -> str:
    """VALID minus one slot line."""
    return "\n".join(
        line for line in VALID.splitlines()
        if not line.startswith(f"{slot}:")
    )


class TestHappyPath:
    def test_valid_frame_parses_and_round_trips(self):
        frame = parse_frame(VALID)
        assert isinstance(frame, EvidenceFrame)
        assert frame.render() == VALID
        assert is_framed(frame.render())

    def test_controlled_tokens_are_exposed(self):
        frame = parse_frame(VALID)
        assert frame.volatility == "stable"
        assert frame.time_scope == "stable-preference"
        assert frame.confidence == "high"

    def test_qualifier_after_controlled_token_is_allowed(self):
        text = VALID.replace(
            "Time scope: stable-preference",
            "Time scope: dated-event (2026-07-21)")
        assert validate_frame(text) == []
        assert parse_frame(text).time_scope == "dated-event"

    def test_multiline_slot_values_are_joined(self):
        text = VALID.replace(
            "Future-use: Prevents a future agent burning a cycle on a "
            "version-mismatch build failure.",
            "Future-use: Prevents a future agent\n  burning a cycle on a "
            "version-mismatch build failure.")
        assert validate_frame(text) == []
        assert "burning a cycle" in parse_frame(text).get("Future-use")


class TestLazyAvoidance:
    """The failure mode the record names: prose passing as durable memory."""

    def test_prose_summary_is_rejected(self):
        errors = validate_frame(
            "master-corvus needs Node 22. I verified this by running the build.")
        assert any("must be written as a frame, not prose" in e for e in errors)

    def test_legacy_lesson_shape_is_rejected(self):
        # The pre-contract body: prose + a bare Evidence line. It looks
        # framed at a glance and is exactly what must stop working.
        errors = validate_frame("Node 22 is needed.\n\nEvidence: session:abc")
        assert any("missing required slots" in e for e in errors)

    def test_empty_and_none_content_rejected(self):
        for body in ("", None, "   "):
            assert validate_frame(body), f"{body!r} must not validate"


class TestRequiredSlots:
    @pytest.mark.parametrize("slot", CANONICAL_SLOTS)
    def test_every_slot_is_required(self, slot):
        errors = validate_frame(_without(slot))
        assert any(slot in e and "missing" in e for e in errors)

    def test_omitted_evidence_is_named_in_the_error(self):
        errors = validate_frame(_without("Evidence"))
        assert any("Evidence" in e for e in errors)

    def test_omitted_time_scope_is_named_in_the_error(self):
        errors = validate_frame(_without("Time scope"))
        assert any("Time scope" in e for e in errors)

    def test_empty_required_slot_is_rejected(self):
        text = VALID.replace(
            "Claim: Node 22.22.0 is required for master-corvus; "
            "Node 20 fails the Vite build.", "Claim:")
        assert any("Claim" in e and "empty" in e for e in validate_frame(text))

    def test_out_of_order_slots_are_rejected(self):
        lines = VALID.splitlines()
        lines[0], lines[1] = lines[1], lines[0]
        errors = validate_frame("\n".join(lines))
        assert any("out of canonical order" in e for e in errors)

    def test_duplicate_slot_is_rejected(self):
        errors = validate_frame(VALID + "\nVolatility: perishable")
        assert any("duplicate" in e for e in errors)

    def test_all_violations_reported_together(self):
        """A composing agent must be able to repair in one retry."""
        broken = _without("Evidence")
        broken = broken.replace("Volatility: stable", "Volatility: permanent")
        errors = validate_frame(broken)
        assert len(errors) >= 2
        assert any("Evidence" in e for e in errors)
        assert any("Volatility" in e for e in errors)


class TestHonestAbsenceVersusInvention:
    """Unknown motivation may be recorded; unknown provenance may not."""

    def test_context_may_be_unknown(self):
        assert validate_frame(VALID.replace(
            "Context: unknown", "Context: unstated")) == []

    def test_entities_may_be_none(self):
        assert validate_frame(VALID.replace(
            "Entities: master-corvus, Node, Vite", "Entities: none")) == []

    @pytest.mark.parametrize("marker", ["unknown", "unstated", "none", "n/a"])
    def test_evidence_may_never_be_an_absence_marker(self, marker):
        text = VALID.replace(
            "Evidence: session:abc123; npm run build exited 1 under "
            "v20.20.0, exit 0 under v22.22.0", f"Evidence: {marker}")
        errors = validate_frame(text)
        assert any("Evidence" in e and "must carry substance" in e
                   for e in errors)

    def test_future_use_may_not_be_unknown(self):
        text = VALID.replace(
            "Future-use: Prevents a future agent burning a cycle on a "
            "version-mismatch build failure.", "Future-use: unknown")
        assert any("Future-use" in e for e in validate_frame(text))


class TestControlledVocabulary:
    @pytest.mark.parametrize("bad", ["permanent", "forever", "high", ""])
    def test_invented_volatility_is_rejected(self, bad):
        text = VALID.replace("Volatility: stable", f"Volatility: {bad}")
        assert validate_frame(text)

    def test_invented_time_scope_is_rejected(self):
        text = VALID.replace("Time scope: stable-preference",
                             "Time scope: whenever")
        assert any("Time scope" in e for e in validate_frame(text))

    def test_invented_confidence_is_rejected(self):
        text = VALID.replace("Confidence: high", "Confidence: very-high")
        assert any("Confidence" in e for e in validate_frame(text))

    def test_perishable_is_accepted(self):
        """The staleness durability gate depends on this class existing."""
        assert validate_frame(
            VALID.replace("Volatility: stable", "Volatility: perishable")) == []


class TestLikelyQueries:
    def test_keyword_dump_is_rejected(self):
        text = VALID.replace(
            "Likely queries: Which Node version does master-corvus need?",
            "Likely queries: node, version, master-corvus")
        assert any("Likely queries" in e for e in validate_frame(text))

    def test_short_claim_is_rejected(self):
        text = VALID.replace(
            "Claim: Node 22.22.0 is required for master-corvus; "
            "Node 20 fails the Vite build.", "Claim: yes")
        assert any("Claim" in e and "too short" in e for e in validate_frame(text))


class TestFusionRelationship:
    """A merge must say what it did to what it absorbed."""

    def test_plain_frame_is_not_enough_for_a_fusion(self):
        errors = validate_fusion_frame(VALID)
        assert any("relationship" in e for e in errors)

    @pytest.mark.parametrize(
        "rel", ["reinforced", "corrected", "narrowed", "superseded",
                "contradicted"])
    def test_each_declared_relationship_is_accepted(self, rel):
        text = VALID.replace(
            "Context: unknown", f"Context: {rel} — the prior memory.")
        assert validate_fusion_frame(text) == []

    def test_malformed_frame_reports_frame_errors_first(self):
        errors = validate_fusion_frame(_without("Evidence"))
        assert any("Evidence" in e for e in errors)


class TestScopeOfEnforcement:
    @pytest.mark.parametrize("node_type", ["lesson", "context-scope",
                                           "tool-profile"])
    def test_durable_memory_classes_require_a_frame(self, node_type):
        assert requires_frame(node_type, "principle")

    @pytest.mark.parametrize("node_type", ["project", "role", "department",
                                           "skill", "document", "reference"])
    def test_scaffolding_and_ingest_classes_are_exempt(self, node_type):
        assert not requires_frame(node_type, None)

    def test_structural_abstraction_is_exempt_regardless_of_node_type(self):
        """A project container is scaffolding even though it is a 'project'
        node; the structural marker is what settles it."""
        assert not requires_frame("lesson", "structural")

    def test_enforce_raises_for_durable_memory(self):
        with pytest.raises(EvidenceFrameError) as exc:
            enforce("just some prose", node_type="lesson",
                    abstraction_type="principle", where="unit-test")
        assert "unit-test" in str(exc.value)

    def test_enforce_is_silent_for_scaffolding(self):
        enforce("Project scope container for corvus lessons.",
                node_type="project", abstraction_type="structural",
                where="unit-test")

    def test_enforce_passes_a_valid_frame(self):
        enforce(VALID, node_type="lesson", abstraction_type="principle",
                where="unit-test")


class TestBuildFrame:
    def test_build_frame_fails_closed_on_missing_future_use(self):
        with pytest.raises(EvidenceFrameError):
            build_frame(claim="A sufficiently long claim about something.",
                        evidence="session:x", future_use="",
                        likely_queries="What is it?")

    def test_build_frame_fails_closed_on_missing_likely_queries(self):
        with pytest.raises(EvidenceFrameError):
            build_frame(claim="A sufficiently long claim about something.",
                        evidence="session:x", future_use="Needed later.",
                        likely_queries="")

    def test_build_frame_defaults_are_conservative(self):
        """An unset volatility must never read as 'stable' — the staleness
        gate grants stable facts protection they have not earned."""
        frame = parse_frame(build_frame(
            claim="A sufficiently long claim about something.",
            evidence="session:x", future_use="Needed later.",
            likely_queries="What is it?"))
        assert frame.volatility == "uncertain"
        assert frame.time_scope == "unknown"


class TestEmbeddingProjection:
    def test_semantic_text_drops_audit_apparatus(self):
        semantic = parse_frame(VALID).semantic_text()
        assert "session:abc123" not in semantic
        assert "high" not in semantic.split()
        assert "Claim:" not in semantic

    def test_semantic_text_keeps_substance(self):
        semantic = parse_frame(VALID).semantic_text()
        assert "master-corvus" in semantic
        assert "Which Node version does master-corvus need?" in semantic

    def test_semantic_text_drops_absence_markers(self):
        """A corpus where most neurons literally contain 'unknown' would
        have that token contribute to every vector while meaning nothing."""
        assert "unknown" not in parse_frame(VALID).semantic_text()

    def test_embedding_input_is_frame_aware(self):
        from app.services.reconsolidation.inheritance import embedding_input
        text = embedding_input("label", "summary", VALID)
        assert "Volatility" not in text
        assert "master-corvus" in text

    def test_embedding_input_leaves_legacy_content_alone(self):
        from app.services.reconsolidation.inheritance import embedding_input
        legacy = "plain prose body\n\nEvidence: session:x"
        assert embedding_input("label", "sum", legacy) == \
            f"label. sum {legacy}"
