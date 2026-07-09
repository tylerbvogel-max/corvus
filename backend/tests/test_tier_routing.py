"""Tier-elastic escalation routing (roadmap arch-tier-routing).

decide_tier_escalation grades a prepared context with the three prep-time
uncertainty signals (coverage, spread density, explicit regulatory citation)
and escalates the primary slot BEFORE execution; escalated_model applies the
decision as an upgrade-only swap. The audit-grade action (opus@low) bypasses
routing and the effort floor entirely — explicit beats adaptive.
"""
import contextvars
from types import SimpleNamespace

from app.config import settings
from app.services.executor import PreparedContext, _apply_primary_overrides, _apply_slot_overrides
from app.services.llm_provider import effort_var
from app.services.tier_routing import decide_tier_escalation, escalated_model


def _run_isolated(fn):
    """Run fn in a copied context so effort_var mutations don't leak across tests."""
    ctx = contextvars.copy_context()
    return ctx.run(fn)


def _ctx(relevances, spread_flags, regulations=()):
    """PreparedContext stub: packed slice from (relevance, spread_boost>0) pairs."""
    scored = [
        SimpleNamespace(relevance=r, spread_boost=1.0 if s else 0.0)
        for r, s in zip(relevances, spread_flags)
    ]
    return PreparedContext(
        system_prompt="PACKED", intent="general_query",
        departments=[], role_keys=[], keywords=[],
        all_scored=scored, resolved_regulations=list(regulations),
    )


def _calibrated(monkeypatch):
    monkeypatch.setattr(settings, "tier_routing_coverage_floor", 0.82)
    monkeypatch.setattr(settings, "tier_routing_spread_share", 0.30)
    monkeypatch.setattr(settings, "tier_routing_regulatory_escalates", True)
    monkeypatch.setattr(settings, "tier_routing_escalation_model", "sonnet")


# ---- decide_tier_escalation ----

def test_confident_query_stays_on_base_tier(monkeypatch):
    _calibrated(monkeypatch)
    ctx = _ctx([0.95, 0.93, 0.92, 0.90, 0.90, 0.4], [False] * 6)
    d = decide_tier_escalation(ctx, "How do we qualify a supplier?")
    assert not d.escalate and d.reasons == ()
    # Signals must be recorded even without escalation (threshold recalibration)
    assert d.signals["coverage"] == 0.92
    assert d.signals["spread_share"] == 0.0
    assert d.signals["regulatory"] is False


def test_low_coverage_escalates(monkeypatch):
    _calibrated(monkeypatch)
    ctx = _ctx([0.80, 0.78, 0.75, 0.75, 0.70], [False] * 5)
    d = decide_tier_escalation(ctx, "How should we structure BOM data for ERP?")
    assert d.escalate and "low_coverage" in d.reasons
    assert d.model == "sonnet"


def test_coverage_uses_top5_mean_not_saturated_max(monkeypatch):
    """RRF rank-normalizes the top neuron to relevance 1.0 on every query —
    the max carries no signal, so a thin pack behind a saturated top-1 must
    still read as low coverage."""
    _calibrated(monkeypatch)
    ctx = _ctx([1.0, 0.5, 0.4, 0.3, 0.2], [False] * 5)
    d = decide_tier_escalation(ctx, "off-graph question")
    assert d.escalate and "low_coverage" in d.reasons


def test_spread_dense_pack_escalates(monkeypatch):
    _calibrated(monkeypatch)
    rels = [0.95, 0.93, 0.92, 0.90, 0.90] + [0.5] * 5
    spread = [False] * 5 + [True] * 5  # half the pack arrived via hops
    d = decide_tier_escalation(_ctx(rels, spread), "cross-domain synthesis question")
    assert d.escalate and d.reasons == ("spread_dense",)


def test_explicit_regulatory_citation_escalates(monkeypatch):
    _calibrated(monkeypatch)
    ctx = _ctx([0.95, 0.93, 0.92, 0.90, 0.90], [False] * 5)
    d = decide_tier_escalation(ctx, "Does FAR 31.205-6 allow bonus costs?")
    assert d.escalate and d.reasons == ("regulatory",)
    assert d.signals["regulatory"] is True


def test_regulatory_needs_explicit_citation_not_resolved_engrams(monkeypatch):
    """62/63 live aero queries resolve regulations in context — resolved
    engrams must NOT be the trigger, or routing escalates everything."""
    _calibrated(monkeypatch)
    ctx = _ctx([0.95, 0.93, 0.92, 0.90, 0.90], [False] * 5,
               regulations=[SimpleNamespace(cfr_ref="14 CFR 21.137")])
    d = decide_tier_escalation(ctx, "How do we qualify a supplier?")
    assert not d.escalate, "resolved regs alone must not escalate"


def test_regulatory_trigger_disabled_by_setting(monkeypatch):
    _calibrated(monkeypatch)
    monkeypatch.setattr(settings, "tier_routing_regulatory_escalates", False)
    ctx = _ctx([0.95, 0.93, 0.92, 0.90, 0.90], [False] * 5)
    d = decide_tier_escalation(ctx, "Does FAR 31.205-6 allow bonus costs?")
    assert not d.escalate


def test_empty_pack_reads_as_zero_coverage_and_escalates(monkeypatch):
    """Uncertainty about uncertainty routes UP: no packed context means the
    failure mode must be cost (sonnet on a thin answer), never quality."""
    _calibrated(monkeypatch)
    d = decide_tier_escalation(_ctx([], []), "anything")
    assert d.escalate and "low_coverage" in d.reasons
    assert d.signals["coverage"] == 0.0


def test_session_overlap_is_recorded_but_never_triggers(monkeypatch):
    _calibrated(monkeypatch)
    ctx = _ctx([0.95, 0.93, 0.92, 0.90, 0.90], [False] * 5)
    d = decide_tier_escalation(ctx, "follow-up question", session_overlap=0.05)
    assert not d.escalate, "low drift-gate overlap alone must not escalate"
    assert d.signals["session_overlap"] == 0.05


# ---- escalated_model (upgrade-only ladder) ----

def test_escalation_upgrades_haiku_only(monkeypatch):
    _calibrated(monkeypatch)
    d = decide_tier_escalation(_ctx([], []), "x")  # escalating decision
    assert escalated_model("haiku", d) == "sonnet"
    assert escalated_model("sonnet", d) == "sonnet", "never touch the target tier"
    assert escalated_model("opus", d) == "opus", "never downgrade"


def test_escalation_skips_non_claude_models(monkeypatch):
    _calibrated(monkeypatch)
    d = decide_tier_escalation(_ctx([], []), "x")
    assert escalated_model("groq-llama", d) == "groq-llama"


def test_no_decision_or_no_escalation_keeps_model(monkeypatch):
    _calibrated(monkeypatch)
    assert escalated_model("haiku", None) == "haiku"
    calm = decide_tier_escalation(
        _ctx([0.95, 0.93, 0.92, 0.90, 0.90], [False] * 5), "easy question",
    )
    assert escalated_model("haiku", calm) == "haiku"


def test_escalation_to_opus_ladder(monkeypatch):
    _calibrated(monkeypatch)
    monkeypatch.setattr(settings, "tier_routing_escalation_model", "opus")
    d = decide_tier_escalation(_ctx([], []), "x")
    assert escalated_model("haiku", d) == "opus"
    assert escalated_model("sonnet", d) == "opus"


# ---- integration with the primary-slot override chain ----

def test_primary_override_applies_escalation(monkeypatch):
    _calibrated(monkeypatch)
    monkeypatch.setattr(settings, "primary_answer_effort", "")
    monkeypatch.setattr(settings, "primary_answer_model", "")
    d = decide_tier_escalation(_ctx([], []), "x")
    got = _run_isolated(lambda: _apply_primary_overrides("haiku", tier_decision=d))
    assert got == "sonnet"


def test_primary_answer_model_setting_wins_over_routing(monkeypatch):
    """Explicit beats adaptive: a configured primary model ignores routing."""
    _calibrated(monkeypatch)
    monkeypatch.setattr(settings, "primary_answer_effort", "")
    monkeypatch.setattr(settings, "primary_answer_model", "opus")
    d = decide_tier_escalation(_ctx([], []), "x")
    got = _run_isolated(lambda: _apply_primary_overrides("haiku", tier_decision=d))
    assert got == "opus"


# ---- audit-grade action (explicit opus@low) ----

def test_audit_slot_forces_opus_at_low_effort(monkeypatch):
    """The measured audit profile (faithfulness 5.0, terse) exists ONLY at low
    effort — the primary effort floor must not bump it."""
    monkeypatch.setattr(settings, "primary_answer_effort", "high")
    monkeypatch.setattr(settings, "primary_answer_model", "")

    def check():
        effort_var.set("medium")
        model = _apply_slot_overrides({"audit": True}, "haiku", is_primary=True)
        assert model == "opus"
        assert effort_var.get() == "low", "audit action must pin low effort"

    _run_isolated(check)


def test_audit_beats_tier_routing(monkeypatch):
    _calibrated(monkeypatch)
    monkeypatch.setattr(settings, "primary_answer_effort", "")
    monkeypatch.setattr(settings, "primary_answer_model", "")
    d = decide_tier_escalation(_ctx([], []), "x")  # escalates to sonnet

    def check():
        model = _apply_slot_overrides(
            {"audit": True}, "haiku", is_primary=True, tier_decision=d,
        )
        assert model == "opus", "explicit audit action must beat routing"

    _run_isolated(check)
