"""Delivery-channel split for memory injection (mind-charter-composition).

Injection reaches a session through two structurally different channels,
and they FAIL DIFFERENTLY, so one pooled load-bearing rate cannot judge
both:

  STANDING   delivered unconditionally, before the system knows what the
             session is about (SessionStart ambient recall + the two
             designated capsules). Succeeds by being PRESENT — a
             temperament directive produces no engagement event, and a
             mistake it prevented leaves no trace in an event log.
             Attribution structurally under-counts this channel.
  RETRIEVED  delivered in response to a concrete trigger (a user prompt,
             a tool call). Succeeds by being RELEVANT, which attribution
             can actually observe.

Pooling them lets 76% standing volume dilute the retrieved rate into
meaninglessness. This module is the instrument that separates them.

Two paths, because the corpus predates the instrument:

  FORWARD   `channel_for_trigger` is stamped onto every injection at
            distill time, so new sessions carry their channel in the
            attribution log and .distilled marker — no inference.
  HISTORIC  `reconstruct_history` recovers the split for already-distilled
            sessions. Attribution log lines carry a neuron_id and a
            timestamp but no session, so each line is joined to the
            .distilled marker written in the same distill pass, and the
            join is ACCEPTED ONLY IF that session's episode log actually
            injected that neuron. A line whose candidate sessions
            disagree about the channel is reported unresolved, never
            guessed. Measured on the 2026-07-28 corpus: 1,060 of 1,063
            lines resolve, 3 unresolved.

No LLM, no writes, no DB — episode logs and markers only.
"""

import json
import os
from datetime import datetime, timedelta, timezone

from app.services.mind_janitors import ACTIONS_LOG, EPISODE_DIR

STANDING = "standing"
RETRIEVED = "retrieved"

# Delivered without a task-specific trigger. SessionStart runs a real
# recall query, but that query is generic ("working knowledge for
# {project}") and fires before the session's subject exists — it is
# unconditional delivery, so it belongs with the capsules.
STANDING_TRIGGERS = frozenset({"SessionStart"})
CAPSULE_PREFIX = "capsule:"

# A distill pass writes its attribution lines and then the marker.
# `distilled_at` is second-truncated, so the marker can stamp up to a
# second BEFORE a line it followed — hence the negative lower bound.
JOIN_LOWER = timedelta(seconds=-5)
JOIN_UPPER = timedelta(seconds=120)

ATTRIBUTION_PREFIX = "attribution."
REWARD_KINDS = ("reward", "penalty")

# The one trigger whose delivery is gated by tool name, and the tool every
# record predating the `tool` field was necessarily produced by.
PRE_TOOL_TRIGGER = "PreToolUse"
LEGACY_PRE_TOOL = "Bash"


def channel_for_trigger(trigger: str) -> str:
    """The delivery channel an injection trigger belongs to."""
    assert isinstance(trigger, str), "trigger must be a string"
    if trigger in STANDING_TRIGGERS or trigger.startswith(CAPSULE_PREFIX):
        return STANDING
    return RETRIEVED


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _session_injections(path: str) -> tuple[dict[str, set[int]],
                                            dict[str, set[int]],
                                            datetime | None]:
    """neuron ids injected into one session, keyed by trigger and by tool,
    plus the earliest injection timestamp.

    Three returns rather than two because two concurrent records landed here
    and both readings are load-bearing. mind-charter-composition needs the
    earliest timestamp as the session's anchor for windowing, so that a
    charter change is measurable against the sessions that followed it.
    mind-pretooluse-reach needs the per-tool split because PreToolUse is the
    only trigger whose reach is gated by tool, and a pooled PreToolUse rate
    that hid a Bash regression would be worse than no number. Neither
    subsumes the other; collapsing them back to a pair would silently drop
    one record's instrument.

    The per-tool mapping is keyed by tool name alone and populated only for
    PreToolUse records. Records written before 2026-08-01 carry no `tool`
    field. They are counted as Bash — not as a fallback guess, but because
    the hook's gate admitted nothing else: `if payload["tool_name"] !=
    "Bash": return 0`. That is what makes the sub-rate one continuous series
    across the widening rather than two incomparable halves.
    """
    per_trigger: dict[str, set[int]] = {}
    per_tool: dict[str, set[int]] = {}
    first_ts: datetime | None = None
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
                when = _parse_ts(rec.get("ts"))
                if when is not None and (first_ts is None or when < first_ts):
                    first_ts = when
                trigger = str(rec.get("trigger") or "unknown")
                bucket = per_trigger.setdefault(trigger, set())
                tool_bucket = None
                if trigger == PRE_TOOL_TRIGGER:
                    tool_bucket = per_tool.setdefault(
                        str(rec.get("tool") or LEGACY_PRE_TOOL), set())
                for nid in rec.get("neuron_ids") or []:
                    if isinstance(nid, int):
                        bucket.add(nid)
                        if tool_bucket is not None:
                            tool_bucket.add(nid)
    except OSError:
        return {}, {}, None
    return per_trigger, per_tool, first_ts


def _scan_sessions(episode_dir: str) -> dict[str, dict]:
    """Per-session injection sets plus distill-marker timestamp."""
    sessions: dict[str, dict] = {}
    if not os.path.isdir(episode_dir):
        return sessions
    for name in sorted(os.listdir(episode_dir)):
        if not name.endswith(".jsonl") or name == os.path.basename(ACTIONS_LOG):
            continue
        path = os.path.join(episode_dir, name)
        per_trigger, per_tool, started_at = _session_injections(path)
        if not per_trigger:
            continue
        marker_ts = None
        try:
            with open(path + ".distilled", encoding="utf-8") as fh:
                marker_ts = _parse_ts(json.load(fh).get("distilled_at"))
        except (OSError, ValueError):
            pass
        every: set[int] = set()
        for ids in per_trigger.values():
            every |= ids
        sessions[name.removesuffix(".jsonl")] = {
            "per_trigger": per_trigger, "per_tool": per_tool,
            "all": every, "marker_ts": marker_ts,
            "started_at": started_at}
    return sessions


def _attribution_lines(actions_log: str) -> list[tuple[datetime, int, str,
                                                        str | None, str | None]]:
    """(ts, neuron_id, kind, channel, trigger) per attribution verdict.

    channel/trigger are None on lines written before the distiller
    stamped delivery provenance; those fall back to the marker join."""
    out: list[tuple[datetime, int, str, str | None, str | None]] = []
    try:
        with open(actions_log, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                action = str(rec.get("action") or "")
                if not action.startswith(ATTRIBUTION_PREFIX):
                    continue
                nid, when = rec.get("neuron_id"), _parse_ts(rec.get("ts"))
                kind = action[len(ATTRIBUTION_PREFIX):]
                if nid is None or when is None or kind not in REWARD_KINDS:
                    continue
                channel = rec.get("channel")
                if channel not in (STANDING, RETRIEVED):
                    channel = None
                trigger = rec.get("trigger")
                out.append((when, int(nid), kind, channel,
                            str(trigger) if trigger else None))
    except OSError:
        pass
    return out


def _triggers_by_neuron(sess: dict) -> dict[int, set[str]]:
    """Invert one session's per-trigger sets: neuron -> triggers that
    delivered it. A neuron is one attribution unit however often it
    arrived."""
    out: dict[int, set[str]] = {}
    for trigger, ids in sess["per_trigger"].items():
        for nid in ids:
            out.setdefault(nid, set()).add(trigger)
    return out


def _tools_by_neuron(sess: dict) -> dict[int, set[str]]:
    """Invert one session's per-tool sets: neuron -> tools that delivered it."""
    out: dict[int, set[str]] = {}
    for tool, ids in (sess.get("per_tool") or {}).items():
        for nid in ids:
            out.setdefault(nid, set()).add(tool)
    return out


def _tools_for_line(nid: int, when: datetime, marked: list,
                    sessions: dict) -> set[str]:
    """Tools that could have delivered the neuron this verdict is about.

    Uses the marker join for every line, stamped or not: the distiller stamps
    channel and trigger but not the tool, and inventing one from the trigger
    is exactly the pooling this split exists to undo.
    """
    return {
        tool
        for marker_ts, sid in marked
        if JOIN_LOWER <= marker_ts - when <= JOIN_UPPER
        and nid in sessions[sid]["all"]
        for tool, ids in (sessions[sid].get("per_tool") or {}).items()
        if nid in ids
    }


def _blank() -> dict:
    return {"injected": 0, "reward": 0, "penalty": 0}


def _rate(bucket: dict) -> dict:
    injected = bucket["injected"]
    return {**bucket, "load_bearing_pct": (
        round(100.0 * bucket["reward"] / injected, 2) if injected else None)}


def reconstruct_history(episode_dir: str = EPISODE_DIR,
                        actions_log: str = ACTIONS_LOG) -> dict:
    """Split the historical load-bearing rate by delivery channel.

    Denominators count each neuron once per session it was injected into
    (attribution collapses repeat injections of one label to a single
    verdict), and only over sessions that were actually distilled — an
    undistilled session has no verdicts and would deflate every rate.
    """
    sessions = _scan_sessions(episode_dir)
    lines = _attribution_lines(actions_log)
    marked = sorted((s["marker_ts"], sid) for sid, s in sessions.items()
                    if s["marker_ts"])

    by_trigger: dict[str, dict] = {}
    by_channel: dict[str, dict] = {STANDING: _blank(), RETRIEVED: _blank()}
    # Numerator and denominator must count the same way or the rate is a
    # fiction: attribution renders ONE verdict per neuron per session, so
    # a neuron delivered twice in a session is one unit, not two. A
    # neuron whose triggers disagree is dropped from the finer breakdown
    # rather than assigned to a guess — the same rule the join uses.
    cross_channel = 0
    ambiguous_trigger = 0
    by_tool: dict[str, dict] = {}
    ambiguous_tool = 0
    for sess in sessions.values():
        if not sess["marker_ts"]:
            continue
        tools_of = _tools_by_neuron(sess)
        for nid, triggers in _triggers_by_neuron(sess).items():
            # Same unit rule as by_trigger, or the sub-rates would not sum to
            # the row they are splitting: only neurons this session saw ONLY
            # via PreToolUse count, because those are the only ones whose
            # verdict the loop below can attribute to a tool.
            if triggers == {PRE_TOOL_TRIGGER}:
                tools = tools_of.get(nid) or set()
                if len(tools) == 1:
                    by_tool.setdefault(
                        next(iter(tools)), _blank())["injected"] += 1
                else:
                    ambiguous_tool += 1
            channels = {channel_for_trigger(t) for t in triggers}
            if len(channels) == 1:
                by_channel[next(iter(channels))]["injected"] += 1
            else:
                cross_channel += 1
            if len(triggers) == 1:
                by_trigger.setdefault(
                    next(iter(triggers)), _blank())["injected"] += 1
            else:
                ambiguous_trigger += 1

    unresolved = 0
    stamped = 0
    for when, nid, kind, channel, trigger in lines:
        if channel is not None:
            stamped += 1
            triggers = {trigger} if trigger else set()
            channels = {channel}
        else:
            triggers = {
                trig
                for marker_ts, sid in marked
                if JOIN_LOWER <= marker_ts - when <= JOIN_UPPER
                and nid in sessions[sid]["all"]
                for trig, ids in sessions[sid]["per_trigger"].items()
                if nid in ids
            }
            channels = {channel_for_trigger(t) for t in triggers}
        if len(channels) != 1:
            unresolved += 1
            continue
        by_channel[channels.pop()][kind] += 1
        if len(triggers) == 1:
            trigger_key = triggers.pop()
            by_trigger.setdefault(trigger_key, _blank())[kind] += 1
            if trigger_key == PRE_TOOL_TRIGGER:
                tools = _tools_for_line(nid, when, marked, sessions)
                if len(tools) == 1:
                    by_tool.setdefault(tools.pop(), _blank())[kind] += 1
                else:
                    ambiguous_tool += 1

    pooled = _blank()
    for bucket in by_channel.values():
        for key in pooled:
            pooled[key] += bucket[key]
    return {
        "sessions_scanned": len(sessions),
        "sessions_distilled": len(marked),
        "attribution_lines": len(lines),
        "stamped_lines": stamped,
        "reconstructed_lines": len(lines) - stamped - unresolved,
        "unresolved_lines": unresolved,
        "cross_channel_neurons": cross_channel,
        "ambiguous_trigger_neurons": ambiguous_trigger,
        "ambiguous_tool_units": ambiguous_tool,
        "pooled": _rate(pooled),
        "by_channel": {k: _rate(v) for k, v in by_channel.items()},
        "by_trigger": {k: _rate(v) for k, v in sorted(
            by_trigger.items(), key=lambda kv: -kv[1]["injected"])},
        # PreToolUse only, split by the tool that triggered it. A pooled gain
        # here that hid a Bash regression would be a failure, not a result
        # (mind-pretooluse-reach), and the pooled row above cannot show it.
        "pretooluse_by_tool": {k: _rate(v) for k, v in sorted(
            by_tool.items(), key=lambda kv: -kv[1]["injected"])},
        "standing_share_pct": (
            round(100.0 * by_channel[STANDING]["injected"] / pooled["injected"], 2)
            if pooled["injected"] else None),
    }


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalise to naive UTC so window bounds and episode timestamps
    compare regardless of which side carries a tzinfo."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def standing_volume(episode_dir: str = EPISODE_DIR,
                    since: datetime | None = None,
                    until: datetime | None = None) -> dict:
    """Standing neurons per session — the before/after number a charter
    change has to move. Counts every session with injections, distilled
    or not, since delivery volume does not depend on distillation.

    Unwindowed this is a LIFETIME average, so once a charter change lands
    it reports a blend of pre- and post-change sessions and understates the
    effect indefinitely. Pass `since`/`until` — sessions are bucketed by
    their first injection — to read one side of a cutover on its own.
    Sessions with no parseable timestamp are excluded whenever a bound is
    given, rather than silently landing in the window.
    """
    sessions = _scan_sessions(episode_dir)
    lo, hi = _as_utc(since), _as_utc(until)
    per_session: list[int] = []
    retrieved: list[int] = []
    undated = 0
    for sess in sessions.values():
        if lo is not None or hi is not None:
            started = _as_utc(sess.get("started_at"))
            if started is None:
                undated += 1
                continue
            if lo is not None and started < lo:
                continue
            if hi is not None and started >= hi:
                continue
        stand = retr = 0
        for triggers in _triggers_by_neuron(sess).values():
            channels = {channel_for_trigger(t) for t in triggers}
            stand += STANDING in channels
            retr += RETRIEVED in channels
        per_session.append(stand)
        retrieved.append(retr)
    count = len(per_session)
    report = {
        "sessions": count,
        "standing_total": sum(per_session),
        "retrieved_total": sum(retrieved),
        "standing_per_session": round(sum(per_session) / count, 2) if count else None,
        "retrieved_per_session": round(sum(retrieved) / count, 2) if count else None,
    }
    if lo is not None or hi is not None:
        report["window"] = {
            "since": lo.isoformat() if lo else None,
            "until": hi.isoformat() if hi else None,
            "sessions_excluded_undated": undated,
        }
    return report
