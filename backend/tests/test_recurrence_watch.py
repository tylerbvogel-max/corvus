"""Fitness functions for the recurrence watch (mind-recurrence-watch).

The organ that makes prevention-value visible: a suppressed lesson's
documented failure recurring in a WITHHELD session is ground-truth harm —
the only evidence class in the system that is exogenous to the distiller's
verdict loop. These tests pin the deterministic admission gate (deeds-
corroborated pattern: an uncited or unrelated nomination is refused), the
delivered-session exclusion (honeypot B: delivered-and-failed is
contradiction evidence, not trial harm), and the action-log round trip
the plasticity fold consumes.

All hermetic: no DB, no LLM, no running backend.
"""

from __future__ import annotations

import json

import pytest

from app.services import mind_corpus
from app.services.recurrence_watch import (
    RECURRENCE_ACTION,
    log_recurrence,
    recurrence_events,
    verify_recurrence,
    withheld_for_trial,
)

LESSON = ("npm-test-needs-node-22. npm test fails when the node version is "
          "not 22.22.0; activate it with nvm before running the suite. | "
          "failure signature: node version v20 unsupported")

EVENTS = [
    {"event": "PostToolUse", "tool": "Bash", "ok": False,
     "error": "node version v20 unsupported",
     "input": {"command": "npm test"}},
    {"event": "PostToolUse", "tool": "Bash", "ok": True,
     "input": {"command": "git status"}},
]

CITATION = "npm test -> FAILED: node version v20 unsupported"


# ── The deterministic admission gate ────────────────────────────────

@pytest.mark.hermetic
def test_gate_admits_a_cited_recurrence_of_the_documented_failure():
    assert verify_recurrence(CITATION, LESSON, EVENTS)


@pytest.mark.hermetic
def test_gate_refuses_an_uncited_nomination():
    """The event named by the citation must actually exist in the log —
    same backstop as deeds-corroborated lessons."""
    assert not verify_recurrence(
        "docker daemon crashed with a segfault during compose up",
        LESSON, EVENTS)
    assert not verify_recurrence("", LESSON, EVENTS)
    assert not verify_recurrence("   ", LESSON, EVENTS)


@pytest.mark.hermetic
def test_gate_refuses_harm_that_is_not_this_lessons_failure_mode():
    """A real failure in the log exonerates nothing by itself: the citation
    must share concrete tokens with the withheld lesson's own text, or any
    failure in a withheld session would end every trial at once."""
    unrelated_lesson = ("pg15-createdb-owner. PG15 denies CREATE on the "
                       "public schema to non-owners; pass createdb -O "
                       "yggdrasil when provisioning databases.")
    assert not verify_recurrence(CITATION, unrelated_lesson, EVENTS)


@pytest.mark.hermetic
def test_gate_protects_lessons_with_no_concrete_tokens():
    """Vague legacy lessons enjoy STRONGER protection: an unmatchable
    failure text means no recurrence can be admitted, the trial continues,
    and the pathway keeps its floor — the safe failure direction."""
    vague = "be-careful. The command failed with an error after the event."
    assert not verify_recurrence(CITATION, vague, EVENTS)


# ── Honeypot B: delivered-and-failed is not trial harm ──────────────

@pytest.mark.hermetic
def test_withheld_for_trial_excludes_neurons_delivered_in_the_session():
    events = [
        {"event": "Injection", "trigger": "UserPromptSubmit",
         "neuron_ids": [7], "withheld": [42]},
        {"event": "Injection", "trigger": "PreToolUse", "tool": "Bash",
         "neuron_ids": [], "withheld": [42, 77]},
        # Neuron 77 was ALSO delivered later (probe on another prompt):
        # the memory was in the room, so its failure is contradiction
        # evidence for attribution's lane, not trial harm.
        {"event": "Injection", "trigger": "UserPromptSubmit",
         "neuron_ids": [77], "withheld": []},
    ]
    entries, delivered = withheld_for_trial(events)
    assert delivered == {7, 77}
    assert [(e["neuron_id"], e["trigger"], e["tool"]) for e in entries] == [
        (42, "UserPromptSubmit", ""), (42, "PreToolUse", "Bash")]


@pytest.mark.hermetic
def test_withheld_for_trial_dedupes_pathways_and_defaults_pretooluse_tool():
    events = [
        {"event": "Injection", "trigger": "PreToolUse",
         "neuron_ids": [], "withheld": [42]},  # no tool field -> Bash
        {"event": "Injection", "trigger": "PreToolUse",
         "neuron_ids": [], "withheld": [42]},
    ]
    entries, _ = withheld_for_trial(events)
    assert entries == [{"neuron_id": 42, "trigger": "PreToolUse",
                        "tool": "Bash"}]


# ── The action-log round trip the plasticity fold consumes ──────────

@pytest.mark.hermetic
def test_log_recurrence_round_trips_through_recurrence_events(
        tmp_path, monkeypatch):
    log = tmp_path / "janitor-actions.jsonl"
    monkeypatch.setattr(mind_corpus, "EPISODE_DIR", str(tmp_path))
    monkeypatch.setattr(mind_corpus, "ACTIONS_LOG", str(log))
    log_recurrence("sess-1", 42, "UserPromptSubmit", "", "npm-test-needs-node-22",
                   CITATION)
    log_recurrence("sess-1", 42, "PreToolUse", "Bash", "npm-test-needs-node-22",
                   CITATION)
    events = recurrence_events(str(log))
    assert [(nid, trig, tool) for _, nid, trig, tool in events] == [
        (42, "UserPromptSubmit", ""), (42, "PreToolUse", "Bash")]
    assert all(when is not None for when, *_ in events)
    raw = [json.loads(line) for line in log.read_text().splitlines()]
    assert all(r["action"] == RECURRENCE_ACTION for r in raw)
    assert raw[0]["citation"] == CITATION


@pytest.mark.hermetic
def test_recurrence_events_ignores_attribution_and_garbage_lines(tmp_path):
    log = tmp_path / "janitor-actions.jsonl"
    with open(log, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": "2026-08-01T12:00:00+00:00", "event": "JanitorAction",
            "action": "attribution.reward", "neuron_id": 7}) + "\n")
        fh.write("not json at all\n")
        fh.write(json.dumps({
            "ts": "2026-08-01T12:01:00+00:00", "event": "JanitorAction",
            "action": RECURRENCE_ACTION, "neuron_id": 42,
            "trigger": "UserPromptSubmit", "tool": None}) + "\n")
    events = recurrence_events(str(log))
    assert len(events) == 1 and events[0][1] == 42


# ── The distiller's nomination surface ──────────────────────────────

@pytest.mark.hermetic
def test_distiller_parses_recurrence_nominations():
    from app.services.distiller import _parse_candidates
    lessons, attribs, recurs = _parse_candidates(json.dumps({
        "lessons": [], "attributions": [],
        "recurrences": [{"label": "npm-test-needs-node-22",
                         "event": CITATION}],
    }))
    assert recurs == [{"label": "npm-test-needs-node-22", "event": CITATION}]
    # Legacy bare-array replies still parse, with no nominations.
    lessons, attribs, recurs = _parse_candidates("[]")
    assert (lessons, attribs, recurs) == ([], [], [])


@pytest.mark.hermetic
def test_distiller_prompt_carries_the_third_task_and_failure_signature():
    from app.services.distiller import DISTILL_SYSTEM_PROMPT
    assert "THIRD TASK" in DISTILL_SYSTEM_PROMPT
    assert '"recurrences"' in DISTILL_SYSTEM_PROMPT
    assert "failure_signature" in DISTILL_SYSTEM_PROMPT
    assert "WITHHELD" in DISTILL_SYSTEM_PROMPT
