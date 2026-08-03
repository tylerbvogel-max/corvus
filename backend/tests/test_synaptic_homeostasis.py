"""Synaptic homeostasis — the sleep half (mind-synaptic-downscaling).

These are the properties that make global renormalization SAFE where every
previous pruner here was not: scaling cannot reorder, the exempt stratum is
unreachable, the floor is the only selector, and the actuator is a
reversible accessibility switch rather than a delete.

Hermetic. The pure functions carry the load-bearing invariants on purpose —
`scale_vector` is extracted precisely so rank preservation can be asserted
directly instead of inferred from database state. Live behaviour (dormancy,
dishabituation, direct-recall reachability, the runaway brake) is proven
against the real corpus in a rolled-back transaction by
scripts/probe_homeostasis.py, whose receipts ship with the record.
"""
import inspect
import random

import pytest

from app.models import Neuron
from app.services import synaptic_homeostasis as sh


# ── the load-bearing property: scaling cannot invent a victim order ─────

def test_scaling_never_inverts_rank_order():
    """A scoring pruner produces a NEW ranking every pass and so can
    condemn anything; proportional scaling is order-preserving and
    structurally cannot. This is the entire safety argument."""
    rng = random.Random(20260802)
    for _ in range(500):
        weights = [rng.uniform(0.001, 1.0) for _ in range(rng.randint(2, 40))]
        scaled = sh.scale_vector(weights)
        before = sorted(range(len(weights)), key=lambda i: weights[i])
        after = sorted(range(len(scaled)), key=lambda i: scaled[i])
        assert before == after, "scaling reordered the corpus"


def test_scaling_preserves_ratios_exactly():
    """Relative strength is what survives renormalization — that is the
    difference between downscaling and re-scoring."""
    weights = [0.2, 0.4, 0.8]
    scaled = sh.scale_vector(weights)
    assert scaled[1] / scaled[0] == pytest.approx(weights[1] / weights[0])
    assert scaled[2] / scaled[1] == pytest.approx(weights[2] / weights[1])


def test_scaling_is_monotonic_and_bounded():
    assert all(s < w for s, w in zip(sh.scale_vector([1.0, 0.5]), [1.0, 0.5]))
    assert sh.scale_vector([1.0], rate=1.0) == [1.0]
    with pytest.raises(AssertionError):
        sh.scale_vector([1.0], rate=1.5)
    with pytest.raises(AssertionError):
        sh.scale_vector([1.0], rate=0.0)


def test_floor_is_reached_in_a_sane_number_of_cycles():
    """Guards the guessed constants against a typo that would quiet the
    graph in two passes or in ten thousand."""
    weight, cycles = sh.FULL_STRENGTH, 0
    while weight >= sh.DORMANCY_FLOOR:
        weight *= sh.DOWNSCALE_RATE
        cycles += 1
        assert cycles < 1000, "floor unreachable — constants are wrong"
    assert 8 <= cycles <= 40, f"{cycles} cycles to dormancy is out of band"


# ── the exempt stratum is unreachable, not merely unlikely ──────────────

def test_charter_and_self_model_are_structurally_exempt():
    """The record requires the charter and self-model never be switched
    off. They are excluded by the population filter itself, so there is no
    code path in which they become candidates."""
    from app.services.delivery_mode import STANDING
    clause = str(sh._exempt_filter().compile(
        compile_kwargs={"literal_binds": True}))
    assert "authority_level" in clause and "organizational" in clause
    assert "department" in clause and "Assistant" in clause
    assert "delivery_mode" in clause and STANDING in clause


def test_exemption_is_applied_negated_to_the_population():
    """A planted attempt to demote an exempt row is refused by
    construction: the pass selects `~exempt`, so an organizational,
    Assistant-scoped, or standing neuron is not in the population at all."""
    source = inspect.getsource(sh.run_downscaling)
    assert "~_exempt_filter()" in source, (
        "population no longer negates the exemption — the charter wall is open")
    # and the SAME filter feeds scaling, wake and selection
    assert source.count("*population") >= 3


def test_exemption_is_null_safe_so_the_pass_stays_global():
    """REGRESSION (live probe, 2026-08-02). All three exempt columns are
    nullable. The population is `~exempt`, and in SQL
    `NOT (false OR false OR NULL)` is NULL — not true — so every neuron
    with a NULL `delivery_mode` fell out of the population entirely. The
    first live run scaled 32 of 448 eligible rows and reported itself
    healthy, because three-valued logic had quietly exempted the other 416.

    A pass whose entire claim is that it is GLOBAL cannot fail this way, so
    every nullable column in the filter is collapsed with coalesce.
    """
    clause = str(sh._exempt_filter().compile(
        compile_kwargs={"literal_binds": True})).lower()
    for column in ("authority_level", "department", "delivery_mode"):
        index = clause.find(column)
        assert index > 0, f"{column} missing from the exemption"
        window = clause[max(0, index - 40):index]
        assert "coalesce" in window, (
            f"{column} is not NULL-collapsed — a NULL there silently drops "
            f"the row out of the scaled population")


def test_census_reports_exempt_so_the_arithmetic_is_checkable():
    """scaled + exempt must account for the whole population. That sum is
    the tripwire for the NULL bug above: a filter that silently exempts
    most of the graph still looks healthy from its own scaled count."""
    source = inspect.getsource(sh.census)
    assert '"exempt"' in source and "_exempt_filter()" in source


def test_standing_delivery_is_exempt_for_a_stated_reason():
    """Standing facts earn their place by preventing mistakes, not by
    generating retrieval traffic — scaling them on disuse would condemn
    them for the exact property that qualified them."""
    doc = sh._exempt_filter.__doc__ or ""
    assert "standing" in doc.lower() and "charter" in doc.lower()


# ── selection is emergent, never a ranked kill list ─────────────────────

def test_no_ranking_or_limit_in_the_scaling_statement():
    """`order_by(...).limit(N)` over a value score is exactly the shape
    that produced every previous bad prune. The scaling statement must
    have no ordering and no cap."""
    source = inspect.getsource(sh.run_downscaling)
    head, _, _ = source.partition("# ── wake")
    assert "order_by" not in head, "scaling picked an order — that is a kill list"
    assert "limit(" not in head, "scaling was capped — that is a kill list"


def test_runaway_brake_refuses_whole_batches():
    """Truncating an oversized batch would demote a set chosen by id,
    which is a ranking by another name AND hides the anomaly."""
    source = inspect.getsource(sh.run_downscaling)
    assert "MAX_DORMANT_PER_RUN" in source
    assert "refused" in source
    assert sh.MAX_DORMANT_PER_RUN > 0


# ── forgetting is an accessibility switch ──────────────────────────────

def test_dormancy_filters_compiled_delivery_only():
    """Dormant means quiet, not gone. The exclusion is offered to
    compiled-delivery consumers and touches nothing about retrievability."""
    clause = " ".join(str(f) for f in sh.dormancy_exclusion_filters())
    assert "dormant_at" in clause
    assert "is_active" not in clause, "dormancy must never deactivate a neuron"
    assert "superseded_by" not in clause, "dormancy must never retire a neuron"


RETRIEVAL_PATH_MODULES = (
    "executor", "scoring_engine", "neuron_service", "recall_primitives",
    "recall_lanes", "propagation", "memory_assembly", "prompt_assembler",
    "skill_signpost",
)


def test_homeostatic_axis_never_reaches_the_retrieval_path():
    """The sleep-side scalar must not become a ninth scoring signal.

    027's own premise is that this axis is deliberately separate from
    `avg_utility`, which IS read by recall scoring — the whole reason a second
    column exists is that renormalizing the evidence column would move neurons
    across trust tiers as a side effect. That separation is currently a
    convention held up by nobody, so it is asserted here at the source.

    There is a second consequence, measured 2026-08-03 (mind-recall-fixture).
    Frozen-corpus fixtures are pg_dumps that predate later migrations; a
    fixture captured before 027 can only be replayed under it by migrating the
    restored copy, which brings `homeostatic_weight` in at its server_default
    (1.0) and `dormant_at` at NULL rather than at the values live held. That is
    sound ONLY while these columns stay off the selection path — the eval
    harness records exactly that acknowledgement
    (~/.corvus-mind/evals/recall-probe/probe.py, accept_unbackfilled). The day
    a scorer starts reading either column, every migrated fixture silently
    begins measuring a ranking function reading defaults for a corpus that
    never had them. This test is the tripwire for that day: if it fails, the
    acknowledgement is void and those fixtures must be recaptured, not
    migrated.
    """
    import importlib

    leaked = {}
    for name in RETRIEVAL_PATH_MODULES:
        module = importlib.import_module(f"app.services.{name}")
        source = inspect.getsource(module)
        hits = [c for c in ("homeostatic_weight", "dormant_at") if c in source]
        if hits:
            leaked[name] = hits
    assert not leaked, (
        f"the homeostatic axis reached the retrieval path: {leaked}. Either "
        "revert it, or accept that pre-027 fixtures can no longer be migrated "
        "forward and must be recaptured (mind-recall-fixture)."
    )


def test_nothing_in_the_module_deletes_or_deactivates():
    """No deletion path exists — asserted at the source, because the
    guarantee is 'there is no such code', not 'the tests did not hit it'."""
    source = inspect.getsource(sh)
    body = source.partition('"""')[2].partition('"""')[2]  # strip the docstring
    for forbidden in ("delete(", "db.delete", "is_active=False",
                      "is_active = False", "superseded_by="):
        assert forbidden not in body, f"module contains a retirement path: {forbidden}"


def test_skill_compiler_excludes_dormant_but_substrate_does_not():
    """`_load_lessons` is shared substrate: consolidation, lint and the
    compiler all read it. Dormant rows must keep being MAINTAINED and stop
    being COMPILED, so the filter belongs in the compiler, not below it."""
    from app.services import mind_corpus, skill_compiler
    assert "dormant_at" in inspect.getsource(skill_compiler.run_compile)
    assert "dormant" not in inspect.getsource(mind_corpus._load_lessons), (
        "dormancy leaked into shared substrate — dormant rows would stop "
        "being deduped, judged and re-embedded")


def test_re_potentiation_clears_dormancy_in_the_same_statement():
    """Dishabituation with no human in the loop: the statement that
    restores strength is the statement that clears dormancy, so a used
    memory cannot come back at full weight and stay quiet."""
    source = inspect.getsource(sh.run_downscaling)
    wake = source.partition("# ── wake")[2].partition("# ── the floor")[0]
    assert "homeostatic_weight=FULL_STRENGTH" in wake
    assert "dormant_at" in wake, "wake restores weight but leaves it dormant"


# ── discipline: shadow-first, evidence-time, no LLM, guessed constants ──

def test_shadow_is_the_default():
    """Live demotion is opt-in until an acceptance window shows zero false
    kills on real rows."""
    import os
    saved = os.environ.pop("CORVUS_HOMEOSTASIS_APPLY", None)
    try:
        assert sh.apply_enabled() is False, "unset must mean shadow"
        for off in ("0", "false", "no", "off", "nonsense", ""):
            os.environ["CORVUS_HOMEOSTASIS_APPLY"] = off
            assert sh.apply_enabled() is False, f"{off!r} enabled live writes"
        for on in ("1", "true", "yes", "on", "TRUE"):
            os.environ["CORVUS_HOMEOSTASIS_APPLY"] = on
            assert sh.apply_enabled() is True, f"{on!r} did not enable"
    finally:
        os.environ.pop("CORVUS_HOMEOSTASIS_APPLY", None)
        if saved is not None:
            os.environ["CORVUS_HOMEOSTASIS_APPLY"] = saved


@pytest.mark.asyncio
async def test_zero_cycles_is_a_no_op():
    """Decay rides evidence time, never wall time: the 6h timer keeps
    firing through a month of absence, and un-gated sleep would walk the
    graph to the floor with nobody around to reinforce anything."""
    report = await sh.run_downscaling(None, cycles=0, apply=True)
    assert report["scaled"] == 0
    assert report["dormant"] == []
    assert "evidence time is frozen" in report["skipped"]


def test_no_llm_anywhere_in_the_pass():
    source = inspect.getsource(sh)
    for forbidden in ("llm_chat", "llm_provider", "claude", "anthropic"):
        assert forbidden not in source.lower().replace(
            "no llm", "").replace("no model", ""), f"LLM reference: {forbidden}"


def test_constants_are_labeled_guessed():
    """Same discipline as delivery plasticity: an unmeasured constant that
    does not say so reads as a measured one."""
    source = inspect.getsource(sh)
    assert "GUESSED" in source
    for name in ("DOWNSCALE_RATE", "DORMANCY_FLOOR"):
        line = next(ln for ln in source.splitlines() if ln.startswith(name))
        index = source.splitlines().index(line)
        preceding = "\n".join(source.splitlines()[max(0, index - 6):index])
        assert "GUESSED" in preceding, f"{name} is not labeled GUESSED"


# ── volatility: read and recorded, never obeyed ────────────────────────

def test_volatility_is_parsed_from_the_evidence_frame():
    framed = ("Claim: something\nConfidence: high\nVolatility: perishable "
              "(a later observation can overwrite it)")
    assert sh.volatility_of(framed) == "perishable"
    assert sh.volatility_of("Claim: x\nVolatility: Stable") == "stable"


def test_unframed_content_reports_unlabeled_not_stable():
    """The audit measured 92% of the corpus carrying no frame at all. An
    unlabeled row must never read as `stable`, which would be protection
    it never earned."""
    assert sh.volatility_of("plain prose with no frame") == "unlabeled"
    assert sh.volatility_of(None) == "unlabeled"
    assert sh.volatility_of("") == "unlabeled"


def test_volatility_never_reaches_the_rate():
    """The kickoff audit returned Fisher p=0.30 at n=32 on 8.1% coverage,
    so the record's own review trigger selects the uniform rate. The label
    is recorded against every outcome so the assumption finally accumulates
    paired evidence — but it must not steer the actuator."""
    source = inspect.getsource(sh.run_downscaling)
    assert "volatility_of(n.content)" in source, "volatility is not recorded"
    scaling = source.partition("# ── the floor")[0]
    assert "volatility" not in scaling, (
        "volatility reached the scaling path — the audit did not support a "
        "rate split (p=0.30, n=32); see VOLATILITY-AUDIT.md")
    assert "DOWNSCALE_RATE" in source
    # exactly one rate constant exists; there is no per-label rate table
    assert not any(ln.strip().startswith("RATE_BY") or "rates =" in ln.lower()
                   for ln in source.splitlines())
