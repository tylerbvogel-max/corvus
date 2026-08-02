"""Recurrence watch — the trial's exogenous endpoint (mind-recurrence-watch).

The Goodhart review of mind-delivery-plasticity found the proxy grading
itself: attenuation was decided by distiller verdicts and "zero false
kills" was checked against those same verdicts. The remedy is a reframe,
not a rebuild — attenuation IS a controlled trial of absence (withhold
from 4-in-5 sessions, deliver to the 5th, log everything), and this module
supplies the one endpoint the verdict loop cannot: RECURRENCE. A
suppressed lesson documents a failure mode; if that failure returns in a
session where the memory was WITHHELD, that is ground-truth harm — the
trial ends, the pathway auto-restores, and the event is a VERIFIED FALSE
KILL, kept as an incident receipt.

This does not reintroduce the proxy. The original sin was inferring
absent help from absent evidence; recurrence infers PRESENT HARM from
PRESENT evidence — failures are transcript-visible in a way prevention
never is. Detection reuses the deeds-corroborated-words trust pattern:
the distiller NOMINATES candidate recurrences (it already reads every
session), and the deterministic gate here ADMITS one only when the cited
event (a) actually appears in the episode log and (b) shares concrete
tokens with the lesson said to have failed. An uncited nomination is
dropped, same as an uncorroborated lesson. A miss is safe: no harm
detected means the trial continues and the pathway keeps its floor —
vague legacy lessons simply enjoy stronger protection.

SOLE-WRITER BOUNDARY: this module never touches `delivery_pathways`.
It writes `recurrence.verified` action records; the plasticity fold (the
table's sole writer) consumes them and performs the restore, the
false-kill receipt, and the proposal supersede on its next pass.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime

from app.services.injection_channel import (
    LEGACY_PRE_TOOL,
    PRE_TOOL_TRIGGER,
    _parse_ts,
)
from app.services.mind_corpus import (
    ACTIONS_LOG,
    _CORROBORATION_STOPWORDS,
    _corroborated,
    _log_action,
)

RECURRENCE_ACTION = "recurrence.verified"


def _concrete_tokens(text: str) -> set[str]:
    """Same token discipline as the distiller's corroboration backstop:
    concrete paths/commands/error fragments only, generic vocabulary out."""
    tokens = set(re.findall(r"[a-z0-9_./-]{4,}", text.casefold()))
    return tokens - _CORROBORATION_STOPWORDS


def verify_recurrence(citation: str, lesson_text: str,
                      events: list[dict]) -> bool:
    """The deterministic admission gate. TWO checks, both required:

    1. The cited event really happened — concrete tokens from the citation
       appear in the session's actual tool events (the shared
       deeds-corroborated backstop from mind_corpus, reused verbatim).
    2. The event is THIS lesson's documented failure mode — the citation
       shares concrete tokens with the lesson's own text (label + content,
       which carries the failure signature on post-ship lessons).

    Without (2), any failure in a withheld session would exonerate every
    withheld lesson; the trial only ends when the documented harm returns.
    """
    citation = (citation or "").strip()
    if not citation or not _corroborated(citation, events):
        return False
    cite_tokens = _concrete_tokens(citation)
    lesson_tokens = _concrete_tokens(lesson_text or "")
    if not cite_tokens or not lesson_tokens:
        return False
    hits = len(cite_tokens & lesson_tokens)
    return hits >= min(2, len(cite_tokens))


def withheld_for_trial(events: list[dict]) -> tuple[list[dict], set[int]]:
    """(withheld pathway entries, delivered neuron ids) for one session.

    An entry {neuron_id, trigger, tool} counts toward the trial ONLY if the
    neuron was never delivered in the session by ANY pathway (including
    capsules): a recurrence beside a delivered copy of the memory is
    contradiction evidence for attribution's lane, not trial harm — the
    memory was in the room and failed, which is a different fact than the
    memory being absent and missed.
    """
    delivered: set[int] = set()
    raw: list[dict] = []
    for rec in events:
        if rec.get("event") != "Injection":
            continue
        delivered.update(n for n in rec.get("neuron_ids") or []
                         if isinstance(n, int))
        trigger = str(rec.get("trigger") or "unknown")
        tool = (str(rec.get("tool") or LEGACY_PRE_TOOL)
                if trigger == PRE_TOOL_TRIGGER else "")
        for nid in rec.get("withheld") or []:
            if isinstance(nid, int):
                raw.append({"neuron_id": nid, "trigger": trigger, "tool": tool})
    seen: set[tuple] = set()
    entries: list[dict] = []
    for e in raw:
        key = (e["neuron_id"], e["trigger"], e["tool"])
        if e["neuron_id"] in delivered or key in seen:
            continue
        seen.add(key)
        entries.append(e)
    return entries, delivered


def log_recurrence(session_id: str, neuron_id: int, trigger: str, tool: str,
                   label: str, citation: str) -> None:
    """One verified recurrence, one action record — the incident's raw
    material. The plasticity fold turns it into a restore + receipt."""
    _log_action(RECURRENCE_ACTION, {
        "session_id": session_id, "neuron_id": neuron_id,
        "trigger": trigger, "tool": tool or None,
        "label": label[:120], "citation": citation[:300],
    })


def recurrence_events(actions_log: str = ACTIONS_LOG,
                      ) -> list[tuple[datetime, int, str, str]]:
    """(ts, neuron_id, trigger, tool) per verified recurrence — the
    exogenous evidence stream the plasticity fold consumes."""
    out: list[tuple[datetime, int, str, str]] = []
    if not os.path.exists(actions_log):
        return out
    try:
        with open(actions_log, encoding="utf-8") as fh:
            for line in fh:
                if RECURRENCE_ACTION not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("action") != RECURRENCE_ACTION:
                    continue
                nid, when = rec.get("neuron_id"), _parse_ts(rec.get("ts"))
                if nid is None or when is None:
                    continue
                out.append((when, int(nid), str(rec.get("trigger") or "unknown"),
                            str(rec.get("tool") or "")))
    except OSError:
        pass
    return out
