"""Delivery plasticity — habituation closes the attribution loop.

(mind-delivery-plasticity) Attribution was afferent-only: the loop measured
per-channel yield, but improving it required a human to read the metrics
endpoint and commission a record. This module is the motor neuron —
habituation (attenuate unrewarded pathways), sensitization (restore on
reward), with an exploration floor so nothing starves.

The unit of learning is the PATHWAY, (neuron, trigger, tool), never the
neuron: the same memory can be dead at SessionStart and load-bearing on
PreToolUse/Bash. The neuron is NEVER touched — the graph keeps knowing,
delivery stops spending. Pathway state is DERIVED SIGNAL (the same class as
co_fire_count and avg_utility — see
test_derived_signal_columns_are_deliberately_unprotected), recomputed from
the episode log and attribution verdicts, entirely outside the
evidence-gated write paths.

SOLE WRITER: this module is the only writer of `delivery_pathways`
(architecture/pathway_writers.json, enforced by
tests/test_pathway_writers.py). Pure counting, batch, deterministic — no
LLM, no new service; a component inside the existing janitor runtime.

DECISION RULE (deterministic; handles the starvation confound). A
habituation candidate is not "never used" — it is "delivered often, never
used, in a channel that does reward others":

    P(0 rewards in n deliveries | channel base reward rate) < alpha

The binomial tail separates dead from starved: a channel that rewards
nobody (base rate 0) can never condemn, so a starved channel's pathways
are safe by construction. n counts deliveries SINCE THE LAST REWARD, so
any single reward resets the clock (hysteresis).

Two actuation tiers on the trust gradient:
  Tier 1, AUTOMATIC: active <-> attenuated. Reversible, audited,
    derived-signal only. The reflex.
  Tier 2, COUNTERSIGNED: `retired` requires an APPLIED countersign
    proposal (gap_source="delivery_plasticity"). The proposal-apply
    dispatcher has no branch for the "pathway-retire" item — the applied
    proposal is the countersign RECEIPT; the mutation itself happens here,
    the sole writer, on the next pass. This NEVER weakens to auto-commit.

RECURRENCE REFRAME (mind-recurrence-watch, the Goodhart remedy). The
binomial tail is TRIAGE, not judge: attenuation is a controlled trial of
absence (withhold 4-in-5, probe the 5th, log everything), and both of the
trial's endpoints are exogenous to the verdict loop — a probe reward
restores (dishabituation), and a verified recurrence of the suppressed
lesson's documented failure in a withheld session restores AND books a
VERIFIED FALSE KILL incident receipt. Zero-false-kills is computed from
those recurrence events, never from reward absence: the proxy that
nominates does not get to grade itself. A recurrence also anchors the
since-exculpation clock (like a reward), so a restored pathway is not
re-condemned next pass on the same statistics. Tier-2 proposals carry the
trial as a causal claim (withheld count + window, probe outcomes,
recurrence count zero) and are REFUSED without it. The base-rate drift
monitor contemplated in the Goodhart review is deliberately NOT built —
with the proxy demoted to triage, a tightening threshold merely starts
more cheap, reversible trials.

Timestamps in `delivery_pathways` are naive UTC (episode logs are aware
UTC; normalized on fold). All comparisons happen in naive UTC.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutopilotProposal, DeliveryPathway, Neuron, ProposalItem
from app.services.injection_channel import (
    JOIN_LOWER,
    JOIN_UPPER,
    LEGACY_PRE_TOOL,
    PRE_TOOL_TRIGGER,
    _attribution_lines,
    _parse_ts,
    reconstruct_history,
)
from app.services.mind_corpus import ACTIONS_LOG, EPISODE_DIR, _log_action

# ── Constants ───────────────────────────────────────────────────────
# ALL GUESSED (mind-delivery-plasticity, 2026-08-01) — no pathway ledger
# existed to fit them against. Revisit once the ledger has data; the
# roadmap record's review trigger requires recomputing against the
# then-current channel base rates before the first attenuation.
ALPHA_ATTENUATE = 0.05   # enter attenuated: P(0 rewards | base rate) below this
ALPHA_RESTORE = 0.25     # exit on base-rate drift: distinct from entry (hysteresis)
ALPHA_RETIRE = 0.01      # propose tier-2 retirement: much stricter than entry
IMMUNITY_MIN_DELIVERIES = 10   # cold-start: young pathways cannot habituate
MIN_PROBES_BEFORE_RETIRE = 3   # probes that must fail after attenuation

# Exploration floor — delivery probability per state. INVARIANT: no state
# may reach zero; habituation without a floor is a death spiral where the
# condemned can never present exculpatory evidence (the LoCoMo step-03
# delivery-starvation finding, wired in as a design constraint). Enforced
# by the module-level assert and by test_no_silent_kill.
PROBE_INTERVALS = {          # deliver 1-in-k while suppressed
    "attenuated": 5,         # GUESSED
    "retire-proposed": 5,    # still probing while awaiting countersign
    "retired": 25,           # GUESSED — tiny, never zero
}
STATE_FLOORS = {"active": 1.0} | {
    state: 1.0 / k for state, k in PROBE_INTERVALS.items()
}
STATES = ("active", "attenuated", "retire-proposed", "retired")
assert all(STATE_FLOORS[s] > 0.0 for s in STATES), \
    "no-silent-kill: every state must keep delivery probability > 0"

PROJECTION_PATH = os.path.expanduser(
    os.environ.get("CORVUS_MIND_PATHWAY_PROJECTION",
                   "~/.corvus-mind/delivery-pathways.json"))

GAP_SOURCE = "delivery_plasticity"


def _utc_naive(when: datetime | None) -> datetime | None:
    if when is None:
        return None
    if when.tzinfo is None:
        return when
    return when.astimezone(timezone.utc).replace(tzinfo=None)


# ── Fold: episode logs + attribution lines → per-pathway units ──────

def _scan_delivery_units(episode_dir: str) -> dict[str, dict]:
    """Per session: marker_ts plus per-neuron delivery evidence.

    Mirrors injection_channel._session_injections but keeps per-record
    timestamps, which the deliveries-since-last-reward clock needs and the
    rate instrument does not. injection_channel stays the canonical
    definition of the unit rules; test_fold_matches_instrument_unit_rules
    pins this scan against it.
    """
    sessions: dict[str, dict] = {}
    if not os.path.isdir(episode_dir):
        return sessions
    for name in sorted(os.listdir(episode_dir)):
        if not name.endswith(".jsonl") or name == os.path.basename(ACTIONS_LOG):
            continue
        path = os.path.join(episode_dir, name)
        by_neuron: dict[int, dict] = {}
        withheld: dict[tuple[int, str, str], datetime] = {}
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if '"Injection"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("event") != "Injection":
                        continue
                    when = _utc_naive(_parse_ts(rec.get("ts")))
                    trigger = str(rec.get("trigger") or "unknown")
                    tool = (str(rec.get("tool") or LEGACY_PRE_TOOL)
                            if trigger == PRE_TOOL_TRIGGER else "")
                    for nid in rec.get("neuron_ids") or []:
                        if not isinstance(nid, int):
                            continue
                        entry = by_neuron.setdefault(
                            nid, {"triggers": set(), "tools": set(), "ts": None})
                        entry["triggers"].add(trigger)
                        if trigger == PRE_TOOL_TRIGGER:
                            entry["tools"].add(tool)
                        if when is not None and (
                                entry["ts"] is None or when < entry["ts"]):
                            entry["ts"] = when
                    # Withheld sibling list (mind-recurrence-watch): the
                    # trial's denominator. Trigger/tool ride the record
                    # itself, so no ambiguity rule is needed here.
                    for nid in rec.get("withheld") or []:
                        if not isinstance(nid, int) or when is None:
                            continue
                        key = (nid, trigger, tool)
                        if key not in withheld or when < withheld[key]:
                            withheld[key] = when
        except OSError:
            continue
        if not by_neuron and not withheld:
            continue
        marker_ts = None
        try:
            with open(path + ".distilled", encoding="utf-8") as fh:
                marker_ts = _parse_ts(json.load(fh).get("distilled_at"))
        except (OSError, ValueError):
            pass
        sessions[name.removesuffix(".jsonl")] = {
            "by_neuron": by_neuron, "withheld": withheld,
            "marker_ts": marker_ts}
    return sessions


def _blank_unit() -> dict:
    return {"deliveries": [], "rewards": [], "penalties": [], "withheld": []}


def pathway_units(episode_dir: str = EPISODE_DIR,
                  actions_log: str = ACTIONS_LOG) -> tuple[dict, dict]:
    """(units keyed (neuron_id, trigger, tool), diagnostics).

    Numerator and denominator count the SAME WAY as the channel instrument
    (injection_channel): one unit per neuron per distilled session; a unit
    whose triggers (or, for PreToolUse, tools) are ambiguous is dropped
    from BOTH sides rather than guessed. Deliveries into undistilled
    sessions never had a chance to earn a verdict, so they don't count
    against the pathway.
    """
    sessions = _scan_delivery_units(episode_dir)
    lines = _attribution_lines(actions_log)
    units: dict[tuple[int, str, str], dict] = {}
    diag = {"ambiguous_units": 0, "unresolved_lines": 0,
            "sessions_distilled": 0}

    marked = sorted(
        (s["marker_ts"], sid) for sid, s in sessions.items() if s["marker_ts"])
    for sess in sessions.values():
        if not sess["marker_ts"]:
            continue
        diag["sessions_distilled"] += 1
        # Withheld units count only in DISTILLED sessions, mirroring
        # deliveries: an undistilled withheld session hasn't had its
        # recurrence check yet, so it cannot yet stand as clean trial
        # evidence. One unit per pathway per session.
        for key, when in sess["withheld"].items():
            units.setdefault(key, _blank_unit())["withheld"].append(when)
        for nid, entry in sess["by_neuron"].items():
            if len(entry["triggers"]) != 1:
                diag["ambiguous_units"] += 1
                continue
            trigger = next(iter(entry["triggers"]))
            if trigger == PRE_TOOL_TRIGGER:
                if len(entry["tools"]) != 1:
                    diag["ambiguous_units"] += 1
                    continue
                tool = next(iter(entry["tools"]))
            else:
                tool = ""
            units.setdefault((nid, trigger, tool), _blank_unit())[
                "deliveries"].append(entry["ts"])

    for when, nid, kind, _channel, trigger in lines:
        if trigger:
            triggers = {trigger}
        else:
            triggers = {
                trig
                for marker_ts, sid in marked
                if JOIN_LOWER <= marker_ts - when <= JOIN_UPPER
                and nid in sessions[sid]["by_neuron"]
                for trig in sessions[sid]["by_neuron"][nid]["triggers"]
            }
        if len(triggers) != 1:
            diag["unresolved_lines"] += 1
            continue
        trig = next(iter(triggers))
        if trig == PRE_TOOL_TRIGGER:
            tools = {
                tool
                for marker_ts, sid in marked
                if JOIN_LOWER <= marker_ts - when <= JOIN_UPPER
                and nid in sessions[sid]["by_neuron"]
                for tool in sessions[sid]["by_neuron"][nid]["tools"]
            }
            if len(tools) != 1:
                diag["unresolved_lines"] += 1
                continue
            tool = next(iter(tools))
        else:
            tool = ""
        key = (nid, trig, tool)
        if key not in units:
            # A verdict whose delivery unit was dropped as ambiguous —
            # count the same way as the denominator: not at all.
            diag["unresolved_lines"] += 1
            continue
        bucket = "rewards" if kind == "reward" else "penalties"
        units[key][bucket].append(_utc_naive(when))
    return units, diag


# ── Decision rule ───────────────────────────────────────────────────

def base_rate_for(history: dict, trigger: str, tool: str) -> float:
    """Channel base reward rate for one pathway, from the instrument's
    per-trigger (and per-tool, for PreToolUse) lifetime rates."""
    if trigger == PRE_TOOL_TRIGGER:
        bucket = (history.get("pretooluse_by_tool") or {}).get(tool)
    else:
        bucket = (history.get("by_trigger") or {}).get(trigger)
    pct = (bucket or {}).get("load_bearing_pct")
    return (pct or 0.0) / 100.0


def survival_tail(n_since_reward: int, base_rate: float) -> float:
    """P(0 rewards in n deliveries | channel base rate). A channel that
    rewards nobody returns 1.0 — dead and starved must not look alike."""
    assert 0.0 <= base_rate <= 1.0, "base rate is a probability"
    if base_rate <= 0.0 or n_since_reward <= 0:
        return 1.0
    if base_rate >= 1.0:
        return 0.0
    return (1.0 - base_rate) ** n_since_reward


def decide(state: str, state_changed_at: datetime | None,
           deliveries: list, rewards: list, base_rate: float,
           proposal_state: str | None, withheld: list | tuple = (),
           recurrences: list | tuple = ()) -> tuple[str, dict]:
    """Next state for one pathway. Pure; returns (new_state, evidence).

    `retired` is decided here ONLY from an applied countersign proposal —
    there is no path into it from statistics alone.

    `withheld`: timestamps of withheld deliveries (the trial denominator);
    `recurrences`: timestamps of VERIFIED recurrences of the pathway's
    documented failure (mind-recurrence-watch) — ground-truth harm from
    absence. A recurrence is exogenous exculpation: it restores a
    suppressed pathway (booking a verified false kill) and anchors the
    since-exculpation clock exactly like a reward, so the restored
    pathway is not re-condemned next pass on unchanged statistics.
    """
    assert state in STATES, f"unknown pathway state: {state}"
    delivered_n = len(deliveries)
    last_reward = max(rewards) if rewards else None
    last_recurrence = max(recurrences) if recurrences else None
    anchors = [t for t in (last_reward, last_recurrence) if t is not None]
    last_exculpation = max(anchors) if anchors else None
    n_since = (sum(1 for d in deliveries if d and d > last_exculpation)
               if last_exculpation else delivered_n)
    tail = survival_tail(n_since, base_rate)
    evidence = {"delivered_n": delivered_n, "rewarded_n": len(rewards),
                "recurrence_n": len(recurrences),
                "n_since_reward": n_since, "base_rate": round(base_rate, 4),
                "tail": round(tail, 6)}
    rewarded_since_change = (
        last_reward is not None and state_changed_at is not None
        and last_reward > state_changed_at)
    recurred_since_change = (
        last_recurrence is not None and state_changed_at is not None
        and last_recurrence > state_changed_at)

    if state == "active":
        if delivered_n < IMMUNITY_MIN_DELIVERIES:
            return "active", evidence | {"immune": True}
        if tail < ALPHA_ATTENUATE:
            return "attenuated", evidence
        return "active", evidence

    if state == "attenuated":
        if recurred_since_change:
            # The documented failure returned while the memory was withheld:
            # ground-truth harm, the trial ends, the kill was FALSE.
            return "active", evidence | {"verified_false_kill": True}
        if rewarded_since_change:
            return "active", evidence | {"dishabituation": True}
        if tail > ALPHA_RESTORE:
            return "active", evidence | {"base_rate_drift": True}
        probes = sum(1 for d in deliveries
                     if d and state_changed_at and d > state_changed_at)
        evidence["probes_since_attenuation"] = probes
        if (probes >= MIN_PROBES_BEFORE_RETIRE and tail < ALPHA_RETIRE
                and proposal_state != "rejected"):
            trial_withheld = sorted(
                w for w in withheld
                if w and (state_changed_at is None or w > state_changed_at))
            if not trial_withheld:
                # No withholding observed → no trial ran → no verdict to
                # countersign. The pathway stays attenuated (floored).
                return "attenuated", evidence | {"no_trial": True}
            evidence["trial"] = {
                "withheld_n": len(trial_withheld),
                "withheld_window": [
                    trial_withheld[0].isoformat(timespec="seconds"),
                    trial_withheld[-1].isoformat(timespec="seconds")],
                "probes_fired": probes,
                "probe_rewards": sum(
                    1 for r in rewards
                    if r and state_changed_at and r > state_changed_at),
                "recurrence_n": sum(
                    1 for r in recurrences
                    if r and state_changed_at and r > state_changed_at),
            }
            return "retire-proposed", evidence
        return "attenuated", evidence

    if state == "retire-proposed":
        if recurred_since_change:
            # Ground-truth harm arrived while the proposal was pending:
            # restore, book the false kill, and the caller supersedes the
            # drifted proposal — same path as a probe reward.
            return "active", evidence | {"verified_false_kill": True}
        if rewarded_since_change:
            # Exculpatory evidence arrived while the proposal was pending:
            # restore, and the caller supersedes the drifted proposal.
            return "active", evidence | {"dishabituation": True}
        if proposal_state == "applied":
            return "retired", evidence
        if proposal_state in ("rejected", "superseded"):
            return "attenuated", evidence | {"proposal_state": proposal_state}
        return "retire-proposed", evidence

    # retired: exculpatory evidence reopens the reflex loop, but does not
    # overturn the countersign to full active on its own. A recurrence
    # still books the incident — the countersigned kill was false too.
    if recurred_since_change:
        return "attenuated", evidence | {"verified_false_kill": True}
    if rewarded_since_change:
        return "attenuated", evidence | {"dishabituation": True}
    return "retired", evidence


# ── Sole-writer mutations ───────────────────────────────────────────

def _transition(row: DeliveryPathway, new_state: str, evidence: dict,
                now: datetime) -> None:
    """The ONLY place a pathway's state changes. Audit-logged, and the
    tier-2 boundary is asserted, not trusted: `retired` demands an applied
    countersign proposal on the row."""
    old = row.state
    assert new_state in STATES and old in STATES
    if new_state == "retired":
        assert evidence.get("applied_proposal_id"), \
            "REFUSED: retirement without an applied countersign proposal"
    row.state = new_state
    row.state_changed_at = now
    _log_action(f"plasticity.{new_state.replace('-', '_')}", {
        "neuron_id": row.neuron_id, "trigger": row.trigger,
        "tool": row.tool or None, "from": old, "to": new_state,
        **evidence})


def trial_claim(evidence: dict) -> str:
    """Render the tier-2 proposal's CAUSAL claim (mind-recurrence-watch).

    Tyler countersigns an experiment's result, not the distiller's opinion:
    the body must carry the trial — sessions withheld (count + window),
    probes fired and their outcomes, recurrence count ZERO — and a proposal
    without that evidence is REFUSED here, before it can be staged."""
    trial = evidence.get("trial") or {}
    assert trial.get("withheld_n", 0) > 0, (
        "REFUSED: tier-2 retirement proposal without withheld-trial "
        "evidence — no trial ran, so there is no result to countersign")
    assert trial.get("recurrence_n", 0) == 0, (
        "REFUSED: tier-2 retirement proposal with a recorded recurrence — "
        "the trial found harm; the pathway must restore, not retire")
    window = trial.get("withheld_window") or ["?", "?"]
    return (f"trial of absence: withheld {trial['withheld_n']} time(s) "
            f"({window[0]} -> {window[-1]}), {trial.get('probes_fired', 0)} "
            f"probes fired ({trial.get('probe_rewards', 0)} rewarded), "
            f"0 recurrences of the documented failure while withheld")


async def _queue_retire_proposal(db: AsyncSession, row: DeliveryPathway,
                                 evidence: dict) -> int:
    """Stage tier-2 retirement for HUMAN SIGN-OFF. The reflex may flinch on
    its own; it does not amputate on its own."""
    claim = trial_claim(evidence)  # REFUSES verdict-shaped proposals
    label = (await db.execute(
        select(Neuron.label).where(Neuron.id == row.neuron_id)
    )).scalar_one_or_none() or f"#{row.neuron_id}"
    where = row.trigger + (f"/{row.tool}" if row.tool else "")
    proposal = AutopilotProposal(
        state="proposed", gap_source=GAP_SOURCE,
        gap_description=(
            f"retire delivery pathway '{label[:60]}' via {where}: {claim}; "
            f"triage stats (nomination only, not the verdict): "
            f"{evidence['delivered_n']} deliveries, "
            f"{evidence['rewarded_n']} rewards, P(dead|channel rate "
            f"{evidence['base_rate']:.1%}) tail {evidence['tail']:.4f}"),
        gap_evidence_json=json.dumps(evidence),
    )
    db.add(proposal)
    await db.flush()
    db.add(ProposalItem(
        proposal_id=proposal.id, action="pathway-retire",
        target_neuron_id=row.neuron_id, field="delivery_pathway_state",
        old_value=row.state, new_value="retired",
        reason=(f"Habituation: pathway ({row.neuron_id}, {where}) is "
                f"statistically dead in a live channel. Approval retires "
                f"DELIVERY on this pathway only (floored, never zero); the "
                f"neuron itself is untouched and stays recallable."),
    ))
    return proposal.id


def write_projection(rows: list[DeliveryPathway],
                     path: str = PROJECTION_PATH) -> dict:
    """Project non-active pathway states to the file the inject hook reads
    at delivery time — hot path stays filesystem-only, zero DB. Atomic
    replace, same discipline as the charter capsule projection."""
    for state, floor in STATE_FLOORS.items():
        assert floor > 0.0, f"no-silent-kill: {state} floor must stay > 0"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "probe_intervals": PROBE_INTERVALS,
        "suppressed": {
            f"{r.neuron_id}|{r.trigger}|{r.tool}": r.state
            for r in rows if r.state != "active"
        },
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    os.replace(tmp, path)
    return payload


def false_kill_receipts(actions_log: str = ACTIONS_LOG) -> list[dict]:
    """Verified-false-kill incident receipts — the trophy case, first-class
    and EXOGENOUS: each one is grounded in a token-verified recurrence
    event, never in reward absence (the proxy does not grade itself)."""
    out: list[dict] = []
    if not os.path.exists(actions_log):
        return out
    try:
        with open(actions_log, encoding="utf-8") as fh:
            for line in fh:
                if '"plasticity.false_kill"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("action") == "plasticity.false_kill":
                    out.append(rec)
    except OSError:
        pass
    return out


async def run_plasticity(db: AsyncSession) -> dict:
    """The janitor pass: fold attribution into pathway counters, recompute
    states, queue tier-2 proposals, project for the hot path."""
    # Lazy import: recurrence_watch pulls in the distiller module tree,
    # which the pure decision-rule path here must not depend on.
    from app.services.recurrence_watch import recurrence_events

    history = reconstruct_history()
    units, diag = pathway_units()
    recur_by_key: dict[tuple[int, str, str], list[datetime]] = {}
    for when, r_nid, r_trig, r_tool in recurrence_events():
        recur_by_key.setdefault((r_nid, r_trig, r_tool), []).append(
            _utc_naive(when))

    rows = (await db.execute(select(DeliveryPathway))).scalars().all()
    by_key = {(r.neuron_id, r.trigger, r.tool): r for r in rows}

    # Tier-2 outcomes first: countersign verdicts land before statistics.
    proposal_states: dict[int, str] = {}
    proposal_ids = [r.proposal_id for r in rows if r.proposal_id]
    if proposal_ids:
        for pid, pstate in (await db.execute(
                select(AutopilotProposal.id, AutopilotProposal.state)
                .where(AutopilotProposal.id.in_(proposal_ids)))).all():
            proposal_states[pid] = pstate

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    transitions: list[dict] = []
    false_kills: list[str] = []
    proposed = 0
    for key, unit in units.items():
        nid, trigger, tool = key
        row = by_key.get(key)
        if row is None:
            # state is set explicitly: the column default lands at INSERT,
            # and decide() sees this row before any flush.
            row = DeliveryPathway(neuron_id=nid, trigger=trigger, tool=tool,
                                  state="active")
            db.add(row)
            by_key[key] = row
        deliveries = sorted(d for d in unit["deliveries"] if d)
        rewards = sorted(r for r in unit["rewards"] if r)
        row.delivered_n = len(unit["deliveries"])
        row.rewarded_n = len(unit["rewards"])
        row.penalized_n = len(unit["penalties"])
        row.last_delivered_at = deliveries[-1] if deliveries else None
        row.last_rewarded_at = rewards[-1] if rewards else None

        pstate = proposal_states.get(row.proposal_id) if row.proposal_id else None
        old_state = row.state
        new_state, evidence = decide(
            row.state, row.state_changed_at, deliveries, rewards,
            base_rate_for(history, trigger, tool), pstate,
            withheld=sorted(w for w in unit["withheld"] if w),
            recurrences=recur_by_key.get(key, ()))
        if new_state == row.state:
            continue
        if new_state == "retired":
            evidence["applied_proposal_id"] = row.proposal_id
        if new_state == "retire-proposed":
            row.proposal_id = await _queue_retire_proposal(db, row, evidence)
            evidence["proposal_id"] = row.proposal_id
            proposed += 1
        if (old_state == "retire-proposed" and new_state == "active"
                and pstate == "proposed"):
            # Exculpatory evidence while pending: the recorded old-state
            # drifted, so the proposal follows the kernel's terminal path.
            proposal = await db.get(AutopilotProposal, row.proposal_id)
            if proposal is not None:
                proposal.state = "superseded"
                proposal.review_notes = (
                    "delivery_plasticity: a verified recurrence of the "
                    "documented failure arrived after this was proposed — "
                    "the trial found harm; verified false kill booked, "
                    "restored to active"
                    if evidence.get("verified_false_kill") else
                    "delivery_plasticity: pathway earned a reward after "
                    "this was proposed; evidence drifted, restored to active")
        if evidence.get("verified_false_kill"):
            # The incident receipt, kept as a trophy, never smoothed over.
            _log_action("plasticity.false_kill", {
                "neuron_id": nid, "trigger": trigger, "tool": tool or None,
                "from": old_state, "to": new_state, **evidence})
            false_kills.append(f"{nid}|{trigger}|{tool}")
        _transition(row, new_state, evidence, now)
        transitions.append({"pathway": f"{nid}|{trigger}|{tool}",
                            "from": old_state, "to": new_state})

    await db.commit()
    projection = write_projection(list(by_key.values()))
    report = {
        "pathways": len(by_key), "transitions": transitions,
        "retire_proposed": proposed,
        # ZERO FALSE KILLS is re-grounded (mind-recurrence-watch): counted
        # from verified recurrence events, never from reward absence.
        "verified_false_kills": false_kills,
        # This pass's receipts are already on disk by now — the file IS
        # the lifetime count, no addition.
        "verified_false_kills_lifetime": len(false_kill_receipts()),
        "suppressed_now": len(projection["suppressed"]), **diag,
        "base_rates_used": {
            "by_trigger": {k: v.get("load_bearing_pct")
                           for k, v in (history.get("by_trigger") or {}).items()},
            "pretooluse_by_tool": {
                k: v.get("load_bearing_pct")
                for k, v in (history.get("pretooluse_by_tool") or {}).items()},
        },
    }
    return report
