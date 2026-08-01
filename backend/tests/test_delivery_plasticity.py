"""Fitness functions for delivery plasticity (mind-delivery-plasticity).

All hermetic: synthetic episode logs + attribution lines in tmp dirs, the
decision rule exercised directly, and the inject hook's gate loaded from
its file. No DB, no LLM, no running backend.

The two honeypots run in BOTH directions, per the roadmap record:
  - a planted pathway WITH rewards must never habituate (no false kills —
    the hard acceptance criterion is that this number is ZERO);
  - a planted dead-in-a-live-channel pathway must habituate in replay
    (the reflex actually fires).
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from app.models import DeliveryPathway
from app.services.delivery_plasticity import (
    ALPHA_ATTENUATE,
    ALPHA_RESTORE,
    ALPHA_RETIRE,
    IMMUNITY_MIN_DELIVERIES,
    MIN_PROBES_BEFORE_RETIRE,
    PROBE_INTERVALS,
    STATE_FLOORS,
    STATES,
    _transition,
    base_rate_for,
    decide,
    pathway_units,
    survival_tail,
    write_projection,
)
from app.services.injection_channel import reconstruct_history

BACKEND = pathlib.Path(__file__).resolve().parents[1]
HOOK_PATH = BACKEND.parent / "harness" / "claude-code" / "memory_inject_hook.py"

T0 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def _ts(minutes: float) -> str:
    return (T0 + timedelta(minutes=minutes)).isoformat(timespec="milliseconds")


def _naive(minutes: float) -> datetime:
    return (T0 + timedelta(minutes=minutes)).replace(tzinfo=None)


def _write_session(episode_dir, sid, injections, distilled_at_min=None):
    """injections: list of (minute, trigger, tool_or_None, [neuron_ids])."""
    path = episode_dir / f"{sid}.jsonl"
    with open(path, "a", encoding="utf-8") as fh:
        for minute, trigger, tool, nids in injections:
            rec = {"ts": _ts(minute), "event": "Injection", "session_id": sid,
                   "cwd": "/x", "trigger": trigger, "query_id": None,
                   "neuron_ids": nids, "labels": [f"n{v}" for v in nids],
                   "scores": [1.0] * len(nids)}
            if tool:
                rec["tool"] = tool
            fh.write(json.dumps(rec) + "\n")
    if distilled_at_min is not None:
        (episode_dir / f"{sid}.jsonl.distilled").write_text(
            json.dumps({"distilled_at": _ts(distilled_at_min)}))


def _write_verdict(actions_log, minute, neuron_id, kind, trigger,
                   channel="retrieved"):
    with open(actions_log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": _ts(minute), "event": "JanitorAction",
            "action": f"attribution.{kind}", "neuron_id": neuron_id,
            "label": f"n{neuron_id}", "channel": channel,
            "trigger": trigger}) + "\n")


@pytest.fixture()
def corpus(tmp_path):
    episode_dir = tmp_path / "episodes"
    episode_dir.mkdir()
    actions_log = tmp_path / "episodes" / "janitor-actions.jsonl"
    return episode_dir, actions_log


# ── No-silent-kill: the invariant, not a tunable ────────────────────

@pytest.mark.hermetic
def test_no_silent_kill_every_state_keeps_a_positive_floor():
    for state in STATES:
        assert STATE_FLOORS[state] > 0.0, (
            f"state {state!r} reached delivery probability 0 — habituation "
            f"without an exploration floor is a death spiral where the "
            f"condemned can never present exculpatory evidence")
    for state, k in PROBE_INTERVALS.items():
        assert isinstance(k, int) and k >= 1


@pytest.mark.hermetic
def test_projection_carries_the_probe_intervals_and_omits_active(tmp_path):
    rows = [
        DeliveryPathway(neuron_id=1, trigger="UserPromptSubmit", tool="",
                        state="attenuated"),
        DeliveryPathway(neuron_id=2, trigger="PreToolUse", tool="Bash",
                        state="active"),
        DeliveryPathway(neuron_id=3, trigger="PreToolUse", tool="Bash",
                        state="retired"),
    ]
    path = tmp_path / "delivery-pathways.json"
    payload = write_projection(rows, path=str(path))
    on_disk = json.loads(path.read_text())
    assert on_disk == json.loads(json.dumps(payload))
    assert on_disk["suppressed"] == {
        "1|UserPromptSubmit|": "attenuated", "3|PreToolUse|Bash": "retired"}
    assert all(k >= 1 for k in on_disk["probe_intervals"].values())


# ── The binomial decision rule, against the measured base rates ─────

@pytest.mark.hermetic
def test_decision_rule_thresholds_match_the_2026_08_01_channel_rates():
    """The record's design numbers, executable: at alpha=0.05 a pathway in
    the 2.7% standing channel needs ~110 deliveries to condemn; in the
    16.9% Bash channel, ~17. (Constants are GUESSED — recompute against
    then-current base rates before the first live attenuation.)"""
    for rate, threshold in ((0.027, 110), (0.169, 17)):
        assert survival_tail(threshold - 1, rate) >= ALPHA_ATTENUATE, (
            f"rate {rate}: condemned one delivery too early")
        assert survival_tail(threshold, rate) < ALPHA_ATTENUATE, (
            f"rate {rate}: {threshold} deliveries should condemn")


@pytest.mark.hermetic
def test_a_channel_that_rewards_nobody_can_never_condemn():
    """The starvation confound: dead and starved must not look alike. Base
    rate 0 means the channel rewards nothing — its pathways are safe."""
    assert survival_tail(10_000, 0.0) == 1.0
    deliveries = [_naive(i) for i in range(500)]
    state, _ = decide("active", None, deliveries, [], 0.0, None)
    assert state == "active"


@pytest.mark.hermetic
def test_immunity_a_young_pathway_cannot_habituate():
    hot = 0.9  # condemn threshold would be 2 deliveries without immunity
    deliveries = [_naive(i) for i in range(IMMUNITY_MIN_DELIVERIES - 1)]
    state, evidence = decide("active", None, deliveries, [], hot, None)
    assert state == "active" and evidence.get("immune")
    deliveries = [_naive(i) for i in range(IMMUNITY_MIN_DELIVERIES)]
    state, _ = decide("active", None, deliveries, [], hot, None)
    assert state == "attenuated"


@pytest.mark.hermetic
def test_hysteresis_any_single_reward_resets_the_clock():
    """40 lifetime deliveries in a 21% channel would condemn (threshold 13),
    but a reward 4 deliveries ago means n_since_reward=4 — safe."""
    deliveries = [_naive(i) for i in range(40)]
    reward = [_naive(35.5)]
    state, evidence = decide("active", None, deliveries, reward, 0.21, None)
    assert evidence["n_since_reward"] == 4
    assert state == "active"


@pytest.mark.hermetic
def test_dishabituation_a_probe_reward_restores_an_attenuated_pathway():
    attenuated_at = _naive(100)
    deliveries = [_naive(i) for i in range(40)] + [_naive(110)]
    rewards = [_naive(111)]  # earned on the probe, after attenuation
    state, evidence = decide("attenuated", attenuated_at, deliveries,
                             rewards, 0.21, None)
    assert state == "active" and evidence.get("dishabituation")


@pytest.mark.hermetic
def test_hysteresis_exit_threshold_is_distinct_from_entry():
    """Base-rate drift alone can release a pathway, but only past the
    LOOSER exit threshold — between the two thresholds nothing oscillates."""
    assert ALPHA_RESTORE > ALPHA_ATTENUATE
    deliveries = [_naive(i) for i in range(8)]
    # tail(8 @ 12%) ≈ 0.36 > ALPHA_RESTORE → release
    state, evidence = decide("attenuated", _naive(0), deliveries, [], 0.12, None)
    assert state == "active" and evidence.get("base_rate_drift")
    # tail(14 @ 12%) ≈ 0.17 — condemned no longer, but not yet past exit → hold
    deliveries = [_naive(i) for i in range(14)]
    state, _ = decide("attenuated", _naive(0), deliveries, [], 0.12, None)
    assert state == "attenuated"


# ── Tier 2: the reflex flinches; only a countersign amputates ───────

@pytest.mark.hermetic
def test_retire_proposal_needs_failed_probes_and_the_stricter_alpha():
    attenuated_at = _naive(50)
    before = [_naive(i) for i in range(30)]
    probes = [_naive(60 + i) for i in range(MIN_PROBES_BEFORE_RETIRE)]
    state, evidence = decide("attenuated", attenuated_at, before + probes,
                             [], 0.21, None)
    assert state == "retire-proposed"
    assert evidence["probes_since_attenuation"] == MIN_PROBES_BEFORE_RETIRE
    assert survival_tail(evidence["n_since_reward"], 0.21) < ALPHA_RETIRE
    # One probe short: still attenuated.
    state, _ = decide("attenuated", attenuated_at, before + probes[:-1],
                      [], 0.21, None)
    assert state == "attenuated"


@pytest.mark.hermetic
def test_a_rejected_proposal_blocks_reproposal_and_returns_to_attenuated():
    attenuated_at = _naive(50)
    deliveries = [_naive(i) for i in range(30)] + [_naive(60), _naive(61), _naive(62)]
    state, _ = decide("attenuated", attenuated_at, deliveries, [], 0.21,
                      "rejected")
    assert state == "attenuated", "a human said no; the reflex does not nag"
    state, _ = decide("retire-proposed", attenuated_at, deliveries, [], 0.21,
                      "rejected")
    assert state == "attenuated"


@pytest.mark.hermetic
def test_retirement_happens_only_through_an_applied_countersign():
    deliveries = [_naive(i) for i in range(40)]
    state, _ = decide("retire-proposed", _naive(50), deliveries, [], 0.21,
                      "applied")
    assert state == "retired"
    state, _ = decide("retire-proposed", _naive(50), deliveries, [], 0.21,
                      "proposed")
    assert state == "retire-proposed", "pending countersign must hold, not act"


@pytest.mark.hermetic
def test_honeypot_a_planted_auto_retirement_attempt_is_refused():
    """The writer-side guard, not just the decision rule: _transition is the
    only place state changes, and it asserts the countersign receipt."""
    row = DeliveryPathway(neuron_id=1, trigger="UserPromptSubmit", tool="",
                          state="retire-proposed")
    with pytest.raises(AssertionError, match="REFUSED"):
        _transition(row, "retired", {"delivered_n": 40}, _naive(0))
    assert row.state == "retire-proposed", "the refused transition must not land"


@pytest.mark.hermetic
def test_a_reward_on_a_retired_pathway_reopens_the_loop():
    deliveries = [_naive(i) for i in range(40)] + [_naive(200)]
    rewards = [_naive(201)]
    state, evidence = decide("retired", _naive(150), deliveries, rewards,
                             0.21, "applied")
    assert state == "attenuated" and evidence.get("dishabituation"), (
        "exculpatory evidence must reopen the reflex loop even after a "
        "countersigned retirement (it does not silently overturn the human)")


# ── The two replay honeypots, full fold from synthetic logs ─────────

def _build_live_channel(episode_dir, actions_log, planted_dead=999,
                        planted_alive=None):
    """A live UserPromptSubmit channel: 30 filler sessions (neurons 1-30),
    15 rewarded → base rate ~21%. The planted-dead neuron rides 40 more
    sessions with zero rewards; the planted-alive one earns a reward late
    in its run so its since-reward clock stays short."""
    for i in range(30):
        _write_session(episode_dir, f"filler-{i:02d}",
                       [(i, "UserPromptSubmit", None, [i + 1])],
                       distilled_at_min=i + 0.5)
        if i < 15:
            _write_verdict(actions_log, i + 0.4, i + 1, "reward",
                           "UserPromptSubmit")
    for i in range(40):
        nids = [planted_dead] + ([planted_alive] if planted_alive else [])
        _write_session(episode_dir, f"planted-{i:02d}",
                       [(100 + i, "UserPromptSubmit", None, nids)],
                       distilled_at_min=100 + i + 0.5)
    if planted_alive:
        _write_verdict(actions_log, 136.4, planted_alive, "reward",
                       "UserPromptSubmit")  # after the 37th delivery


@pytest.mark.hermetic
def test_honeypot_a_dead_pathway_in_a_live_channel_habituates_in_replay(corpus):
    episode_dir, actions_log = corpus
    _build_live_channel(episode_dir, actions_log, planted_dead=999)
    history = reconstruct_history(str(episode_dir), str(actions_log))
    units, diag = pathway_units(str(episode_dir), str(actions_log))
    assert diag["ambiguous_units"] == 0 and diag["unresolved_lines"] == 0

    # Fold discipline pin: pathway deliveries count the same way as the
    # channel instrument's denominator.
    delivered = sum(len(u["deliveries"]) for (nid, trig, _), u in units.items()
                    if trig == "UserPromptSubmit")
    assert delivered == history["by_trigger"]["UserPromptSubmit"]["injected"]

    unit = units[(999, "UserPromptSubmit", "")]
    rate = base_rate_for(history, "UserPromptSubmit", "")
    assert rate > 0.15, "fixture sanity: the channel must be visibly live"
    state, evidence = decide("active", None, sorted(unit["deliveries"]),
                             sorted(unit["rewards"]), rate, None)
    assert state == "attenuated", (
        f"40 deliveries, 0 rewards, channel rate {rate:.1%} — the reflex "
        f"must fire (evidence: {evidence})")


@pytest.mark.hermetic
def test_honeypot_a_rewarded_pathway_never_habituates_in_replay(corpus):
    """ZERO FALSE KILLS — the hard acceptance criterion. Same live channel,
    same 40-delivery ride; one reward near the end keeps the pathway safe."""
    episode_dir, actions_log = corpus
    _build_live_channel(episode_dir, actions_log, planted_dead=999,
                        planted_alive=888)
    history = reconstruct_history(str(episode_dir), str(actions_log))
    units, _ = pathway_units(str(episode_dir), str(actions_log))
    unit = units[(888, "UserPromptSubmit", "")]
    assert len(unit["rewards"]) == 1
    rate = base_rate_for(history, "UserPromptSubmit", "")
    state, evidence = decide("active", None, sorted(unit["deliveries"]),
                             sorted(unit["rewards"]), rate, None)
    assert state == "active", (
        f"FALSE KILL: a pathway with a trailing-window reward attenuated "
        f"(evidence: {evidence})")
    # ...and the dead one still dies alongside it, from the same fold.
    dead = units[(999, "UserPromptSubmit", "")]
    state, _ = decide("active", None, sorted(dead["deliveries"]),
                      sorted(dead["rewards"]), rate, None)
    assert state == "attenuated"


# ── Fold unit rules ─────────────────────────────────────────────────

@pytest.mark.hermetic
def test_fold_drops_ambiguous_units_from_both_sides(corpus):
    episode_dir, actions_log = corpus
    # Neuron 5 arrives via TWO triggers in one session: no pathway can own
    # the verdict, so neither counts the delivery — same rule as the
    # channel instrument.
    _write_session(episode_dir, "amb",
                   [(0, "UserPromptSubmit", None, [5]),
                    (1, "PreToolUse", "Bash", [5])],
                   distilled_at_min=2)
    _write_verdict(actions_log, 1.9, 5, "reward", None)
    units, diag = pathway_units(str(episode_dir), str(actions_log))
    assert not units
    assert diag["ambiguous_units"] == 1


@pytest.mark.hermetic
def test_fold_splits_pretooluse_by_tool_and_legacy_records_count_as_bash(corpus):
    episode_dir, actions_log = corpus
    _write_session(episode_dir, "s1",
                   [(0, "PreToolUse", "Read", [7])], distilled_at_min=1)
    # Legacy record: no tool field — the gate that wrote it admitted only Bash.
    _write_session(episode_dir, "s2",
                   [(0, "PreToolUse", None, [7])], distilled_at_min=1)
    units, _ = pathway_units(str(episode_dir), str(actions_log))
    assert set(units) == {(7, "PreToolUse", "Read"), (7, "PreToolUse", "Bash")}


@pytest.mark.hermetic
def test_fold_ignores_undistilled_sessions(corpus):
    episode_dir, actions_log = corpus
    _write_session(episode_dir, "nomark",
                   [(0, "UserPromptSubmit", None, [3])])  # no marker
    units, _ = pathway_units(str(episode_dir), str(actions_log))
    assert not units, ("a delivery that never had a chance to earn a verdict "
                       "must not count against the pathway")


# ── The hook's gate: suppression, probe slot, fail-open ─────────────

def _load_hook():
    spec = importlib.util.spec_from_file_location("mih_under_test", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.hermetic
def test_hook_gate_suppresses_but_the_probe_slot_still_fires(tmp_path):
    hook = _load_hook()
    proj = tmp_path / "delivery-pathways.json"
    proj.write_text(json.dumps({
        "probe_intervals": {"attenuated": 5},
        "suppressed": {"42|UserPromptSubmit|": "attenuated"},
    }))
    hook.PATHWAY_PROJECTION = str(proj)
    hits = [{"neuron_id": 42, "label": "n42", "score": 1.0},
            {"neuron_id": 43, "label": "n43", "score": 1.0}]

    outcomes = {}
    for i in range(200):
        kept, probes = hook._pathway_gate(f"sess-{i}", list(hits),
                                          "UserPromptSubmit", None)
        assert any(h["neuron_id"] == 43 for h in kept), \
            "an unsuppressed pathway must never be touched"
        delivered_42 = any(h["neuron_id"] == 42 for h in kept)
        assert delivered_42 == (42 in probes)
        outcomes[f"sess-{i}"] = delivered_42
    fired = sum(outcomes.values())
    assert 0 < fired < 200, (
        f"probe slot fired {fired}/200 — 0 is a silent kill, 200 is no "
        f"suppression; 1-in-5 expects ~40")
    # Deterministic: the same session always gets the same answer.
    for i in (0, 7, 123):
        kept, _ = hook._pathway_gate(f"sess-{i}", list(hits),
                                     "UserPromptSubmit", None)
        assert any(h["neuron_id"] == 42 for h in kept) == outcomes[f"sess-{i}"]


@pytest.mark.hermetic
def test_hook_gate_fails_open(tmp_path):
    hook = _load_hook()
    hits = [{"neuron_id": 42, "label": "n42", "score": 1.0}]
    # Missing projection: deliver.
    hook.PATHWAY_PROJECTION = str(tmp_path / "missing.json")
    kept, probes = hook._pathway_gate("s", list(hits), "UserPromptSubmit", None)
    assert kept == hits and not probes
    # Corrupt k (zero) — the floor may never reach zero, so deliver.
    proj = tmp_path / "bad.json"
    proj.write_text(json.dumps({
        "probe_intervals": {"attenuated": 0},
        "suppressed": {"42|UserPromptSubmit|": "attenuated"},
    }))
    hook.PATHWAY_PROJECTION = str(proj)
    kept, _ = hook._pathway_gate("s", list(hits), "UserPromptSubmit", None)
    assert kept == hits


@pytest.mark.hermetic
def test_hook_gate_keys_pathways_by_tool_for_pretooluse(tmp_path):
    hook = _load_hook()
    proj = tmp_path / "delivery-pathways.json"
    proj.write_text(json.dumps({
        "probe_intervals": {"attenuated": 1_000_000},
        "suppressed": {"42|PreToolUse|Bash": "attenuated"},
    }))
    hook.PATHWAY_PROJECTION = str(proj)
    hits = [{"neuron_id": 42, "label": "n42", "score": 1.3}]
    kept_bash, _ = hook._pathway_gate("s0", list(hits), "PreToolUse", "Bash")
    kept_read, _ = hook._pathway_gate("s0", list(hits), "PreToolUse", "Read")
    assert not kept_bash, "suppressed on Bash (probe interval set huge)"
    assert kept_read == hits, "the SAME neuron on another tool is untouched"
