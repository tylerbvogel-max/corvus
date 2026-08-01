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
        except OSError:
            continue
        if not by_neuron:
            continue
        marker_ts = None
        try:
            with open(path + ".distilled", encoding="utf-8") as fh:
                marker_ts = _parse_ts(json.load(fh).get("distilled_at"))
        except (OSError, ValueError):
            pass
        sessions[name.removesuffix(".jsonl")] = {
            "by_neuron": by_neuron, "marker_ts": marker_ts}
    return sessions


def _blank_unit() -> dict:
    return {"deliveries": [], "rewards": [], "penalties": []}


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
           proposal_state: str | None) -> tuple[str, dict]:
    """Next state for one pathway. Pure; returns (new_state, evidence).

    `retired` is decided here ONLY from an applied countersign proposal —
    there is no path into it from statistics alone.
    """
    assert state in STATES, f"unknown pathway state: {state}"
    delivered_n = len(deliveries)
    last_reward = max(rewards) if rewards else None
    n_since = (sum(1 for d in deliveries if d and last_reward and d > last_reward)
               if last_reward else delivered_n)
    tail = survival_tail(n_since, base_rate)
    evidence = {"delivered_n": delivered_n, "rewarded_n": len(rewards),
                "n_since_reward": n_since, "base_rate": round(base_rate, 4),
                "tail": round(tail, 6)}
    rewarded_since_change = (
        last_reward is not None and state_changed_at is not None
        and last_reward > state_changed_at)

    if state == "active":
        if delivered_n < IMMUNITY_MIN_DELIVERIES:
            return "active", evidence | {"immune": True}
        if tail < ALPHA_ATTENUATE:
            return "attenuated", evidence
        return "active", evidence

    if state == "attenuated":
        if rewarded_since_change:
            return "active", evidence | {"dishabituation": True}
        if tail > ALPHA_RESTORE:
            return "active", evidence | {"base_rate_drift": True}
        probes = sum(1 for d in deliveries
                     if d and state_changed_at and d > state_changed_at)
        evidence["probes_since_attenuation"] = probes
        if (probes >= MIN_PROBES_BEFORE_RETIRE and tail < ALPHA_RETIRE
                and proposal_state != "rejected"):
            return "retire-proposed", evidence
        return "attenuated", evidence

    if state == "retire-proposed":
        if rewarded_since_change:
            # Exculpatory evidence arrived while the proposal was pending:
            # restore, and the caller supersedes the drifted proposal.
            return "active", evidence | {"dishabituation": True}
        if proposal_state == "applied":
            return "retired", evidence
        if proposal_state in ("rejected", "superseded"):
            return "attenuated", evidence | {"proposal_state": proposal_state}
        return "retire-proposed", evidence

    # retired: a probe reward reopens the reflex loop, but does not
    # overturn the countersign to full active on its own.
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


async def _queue_retire_proposal(db: AsyncSession, row: DeliveryPathway,
                                 evidence: dict) -> int:
    """Stage tier-2 retirement for HUMAN SIGN-OFF. The reflex may flinch on
    its own; it does not amputate on its own."""
    label = (await db.execute(
        select(Neuron.label).where(Neuron.id == row.neuron_id)
    )).scalar_one_or_none() or f"#{row.neuron_id}"
    where = row.trigger + (f"/{row.tool}" if row.tool else "")
    proposal = AutopilotProposal(
        state="proposed", gap_source=GAP_SOURCE,
        gap_description=(
            f"retire delivery pathway '{label[:60]}' via {where}: "
            f"{evidence['delivered_n']} deliveries, "
            f"{evidence['rewarded_n']} rewards, "
            f"{evidence['probes_since_attenuation']} failed probes since "
            f"attenuation; P(dead|channel rate "
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


async def run_plasticity(db: AsyncSession) -> dict:
    """The janitor pass: fold attribution into pathway counters, recompute
    states, queue tier-2 proposals, project for the hot path."""
    history = reconstruct_history()
    units, diag = pathway_units()

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
            base_rate_for(history, trigger, tool), pstate)
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
            # Exculpatory reward while pending: the recorded old-state
            # drifted, so the proposal follows the kernel's terminal path.
            proposal = await db.get(AutopilotProposal, row.proposal_id)
            if proposal is not None:
                proposal.state = "superseded"
                proposal.review_notes = (
                    "delivery_plasticity: pathway earned a reward after "
                    "this was proposed; evidence drifted, restored to active")
        _transition(row, new_state, evidence, now)
        transitions.append({"pathway": f"{nid}|{trigger}|{tool}",
                            "from": old_state, "to": new_state})

    await db.commit()
    projection = write_projection(list(by_key.values()))
    report = {
        "pathways": len(by_key), "transitions": transitions,
        "retire_proposed": proposed,
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
