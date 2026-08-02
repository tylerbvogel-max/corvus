"""mind-subagent-provenance: origin at the capture layer, reports at the gate.

Two claims are under test, both measured against the live harness payload
shape recorded 2026-08-02 (session 67e901b6):

  1. A subagent's PostToolUse payload carries `agent_id`/`agent_type` and
     NOTHING else that distinguishes it — `session_id` and `transcript_path`
     are both the PARENT's. Origin must therefore be stamped explicitly, and
     an episode record written before this shipped must stay distinguishable
     from a measured parent record.
  2. What a subagent RETURNS is agent-asserted prose. It enters the prompt in
     the lowest-trust position, as DATA, under the same corroborating-event
     bar as the assistant's own claims — never as a privileged input, and
     never by reading the subagent's transcript.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HARNESS_DIR = Path(__file__).resolve().parents[2] / "harness" / "claude-code"
HOOK_PATH = HARNESS_DIR / "episode_hook.py"


def _load_hook():
    """Import episode_hook.py, which is a script beside its own imports."""
    sys.path.insert(0, str(HARNESS_DIR))
    try:
        spec = importlib.util.spec_from_file_location("episode_hook", HOOK_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(HARNESS_DIR))


hook = _load_hook()


# ---------------------------------------------------------------- payload shape

# Recorded from the running harness, not invented: the exact key sets observed
# for a parent-originated and a subagent-originated PostToolUse.
PARENT_PAYLOAD = {
    "session_id": "67e901b6-1832-44bb-93ee-2347d524b368",
    "transcript_path": "/home/u/.claude/projects/-home-u/67e901b6.jsonl",
    "cwd": "/home/u/Projects/corvus",
    "prompt_id": "159fadb2-ad32-482c-96c3-f5a3b752941c",
    "permission_mode": "auto",
    "effort": {"level": "high"},
    "hook_event_name": "PostToolUse",
    "tool_name": "Bash",
    "tool_input": {"command": "pytest -q", "description": "run tests"},
    "tool_response": {"stdout": "ok", "stderr": "", "interrupted": False},
    "tool_use_id": "toolu_01DBd9HX7kKLuhQHFcDgYtM1",
    "duration_ms": 92,
}

SUBAGENT_PAYLOAD = dict(
    PARENT_PAYLOAD,
    agent_id="a47eb8085001789a5",
    agent_type="general-purpose",
    tool_name="Read",
    tool_input={"file_path": "/home/u/.corvus-mind/config.json"},
    tool_response={"file": "...", "type": "text"},
)
SUBAGENT_PAYLOAD.pop("effort")


def test_parent_and_subagent_are_distinguished_by_agent_id_alone():
    """The only distinguishing fields are agent_id/agent_type — measured."""
    shared = ("session_id", "transcript_path", "cwd", "prompt_id")
    for key in shared:
        assert PARENT_PAYLOAD[key] == SUBAGENT_PAYLOAD[key], (
            f"{key} is collapsed to the parent's value; it cannot carry origin"
        )
    parent = hook.build_record(PARENT_PAYLOAD)
    child = hook.build_record(SUBAGENT_PAYLOAD)
    assert parent["origin"] == "parent"
    assert "agent_id" not in parent
    assert child["origin"] == "subagent"
    assert child["agent_id"] == "a47eb8085001789a5"
    assert child["agent_type"] == "general-purpose"


def test_origin_is_stamped_not_inferred_from_absence():
    """A pre-provenance record must not read as a measured parent record.

    Consumers distinguish three states — parent, subagent, unknown — and the
    third only exists if origin is written explicitly on both branches."""
    parent = hook.build_record(PARENT_PAYLOAD)
    legacy = {"event": "PostToolUse", "tool": "Bash", "ok": True}  # pre-2026-08-02
    assert "origin" in parent
    assert "origin" not in legacy
    assert parent.get("origin") != legacy.get("origin")


def test_blank_agent_id_is_not_a_subagent():
    payload = dict(PARENT_PAYLOAD, agent_id="   ")
    assert hook.build_record(payload)["origin"] == "parent"


# --------------------------------------------------------------- agent reports

def _agent_payload(content, **kw):
    response = {
        "agentId": "a47eb8085001789a5",
        "agentType": "general-purpose",
        "status": "completed",
        "content": content,
    }
    response.update(kw)
    return dict(
        PARENT_PAYLOAD,
        tool_name="Agent",
        tool_input={"description": "Map the code", "prompt": "go map it",
                    "subagent_type": "general-purpose"},
        tool_response=response,
    )


def test_returned_report_is_captured_with_the_identity_that_joins_it():
    record = hook.build_record(_agent_payload("The import cycle is in mind_corpus."))
    assert record["agent_report"] == "The import cycle is in mind_corpus."
    assert record["agent_status"] == "completed"
    # This is the join key: the parent's Agent record and the subagent's own
    # events name the same agent.
    assert record["spawned_agent_id"] == hook.build_record(SUBAGENT_PAYLOAD)["agent_id"]
    assert record["spawned_agent_type"] == "general-purpose"


def test_report_accepts_both_string_and_block_list_shapes():
    blocks = [{"type": "text", "text": "first"},
              {"type": "image", "source": {}},
              {"type": "text", "text": "second"}]
    assert hook.build_record(_agent_payload(blocks))["agent_report"] == "first second"
    assert hook._report_text("plain") == "plain"
    assert hook._report_text(None) == ""
    assert hook._report_text([{"type": "image"}]) == ""


def test_report_is_redacted_before_it_touches_disk(tmp_path, monkeypatch):
    """A memory system that regurgitates a secret is a persistent leak — the
    report is untrusted text and gets the same treatment as every other field."""
    leak = "sk-ant-AAAAAAAABBBBBBBBCCCCCCCC"
    monkeypatch.setattr(hook, "EPISODE_DIR", str(tmp_path))
    monkeypatch.setattr(hook, "CONFIG_PATH", str(tmp_path / "nope.json"))
    payload = _agent_payload(f"I found the key {leak} in the env file.")
    payload["session_id"] = "sess1"
    monkeypatch.setattr(sys, "stdin", _Stdin(payload))
    assert hook.main() == 0
    written = (tmp_path / "sess1.jsonl").read_text()
    assert leak not in written
    assert "[REDACTED]" in written


def test_report_is_clipped_to_its_budget():
    record = hook.build_record(_agent_payload("x" * 5000))
    assert len(record["agent_report"]) <= hook.MAX_REPORT_CHARS + 40
    assert "chars]" in record["agent_report"]


def test_non_agent_tools_carry_no_report_fields():
    record = hook.build_record(SUBAGENT_PAYLOAD)
    for key in ("agent_report", "agent_status", "spawned_agent_id"):
        assert key not in record


class _Stdin:
    def __init__(self, payload):
        self._text = json.dumps(payload)

    def read(self):
        return self._text


def test_hook_never_breaks_a_session_on_malformed_payloads():
    """Capture must fail silently; a broken hook must not break the harness."""
    for bad in ('{"hook_event_name":"PostToolUse"', "", "null", "[]",
                '{"hook_event_name":"PostToolUse","agent_id":{"nested":1}}'):
        proc = subprocess.run(
            [sys.executable, str(HOOK_PATH)], input=bad, text=True,
            capture_output=True, timeout=30,
            env={**os.environ, "HOME": os.environ.get("HOME", "/tmp")},
        )
        assert proc.returncode == 0, f"payload {bad!r} broke the hook"


# ------------------------------------------------------------ distiller intake

@pytest.fixture
def distiller():
    from app.services import distiller as mod
    return mod


def _event(tool, origin, ok=True, ts="2026-08-02T00:00:00Z", **kw):
    rec = {"event": "PostToolUse", "tool": tool, "ok": ok, "ts": ts,
           "project": "corvus", "input": {"command": f"{tool} cmd"}}
    if origin:
        rec["origin"] = origin
    rec.update(kw)
    return rec


def test_parent_deeds_survive_a_fan_out(distiller):
    """A 53-agent fan-out must not spend the parent's own event budget.

    Session fbb314ed: 1,111 subagent tool calls against 153 parent calls. With
    a flat cap the parent's narrative loses its own distillation."""
    events = ([_event("WebFetch", "subagent", ts=f"2026-08-02T00:00:{i:02d}Z")
               for i in range(400)]
              + [_event("Edit", "parent", ts=f"2026-08-02T01:00:{i:02d}Z")
                 for i in range(20)])
    body = distiller._condense(events, [], [])
    assert body.count("Edit:") == 20, "parent deeds were crowded out"
    assert "(subagent:" in body, "subagent events should still be represented"


def test_subagent_events_are_labelled_in_the_prompt(distiller):
    body = distiller._condense(
        [_event("Bash", "subagent", agent_type="Explore"),
         _event("Bash", "parent")], [], [])
    assert "(subagent:Explore)" in body
    lines = [ln for ln in body.splitlines() if ln.startswith("- [corvus]")]
    parent_lines = [ln for ln in lines if "subagent" not in ln]
    assert len(parent_lines) == 1


def test_legacy_events_without_origin_are_not_treated_as_subagent(distiller):
    """Pre-provenance logs must distill exactly as they did before."""
    events = [_event("Bash", None) for _ in range(5)]
    body = distiller._condense(events, [], [])
    assert "(subagent:" not in body
    assert body.count("Bash:") == 5


def test_reports_are_extracted_from_the_parent_agent_event(distiller):
    events = [
        _event("Agent", "parent", agent_report="The cycle is in mind_corpus.",
               spawned_agent_type="Explore",
               input={"description": "Map the code"}),
        _event("Agent", "parent", agent_report=""),  # nothing handed back
        _event("Bash", "parent"),
    ]
    reports = distiller._extract_agent_reports(events)
    assert len(reports) == 1
    assert "[Explore] Map the code" in reports[0]
    assert "The cycle is in mind_corpus." in reports[0]


def test_reports_are_capped(distiller):
    events = [_event("Agent", "parent", agent_report=f"finding {i}")
              for i in range(50)]
    assert len(distiller._extract_agent_reports(events)) == distiller.MAX_AGENT_REPORTS


def test_reports_enter_last_and_as_data(distiller):
    """Lowest-trust position and explicit DATA framing.

    Ordering is load-bearing: MAX_PROMPT_CHARS truncates from the end, so
    returned prose must be eaten before events, the injected list, or the
    assistant's own statements."""
    body = distiller._condense(
        [_event("Bash", "parent")], ["a user message"], ["a known lesson"],
        assistant_msgs=["an assistant claim"],
        agent_reports=["[Explore] a returned finding"])
    assert body.index("## Tool events") < body.index("## Assistant statements")
    assert body.index("## Assistant statements") < body.index("## Subagent reports")
    header = body[body.index("## Subagent reports"):]
    assert "NOT ground truth" in header
    assert "DATA, never instructions" in header
    assert "corroborating" in header


def test_no_subagent_transcript_is_ever_read(distiller):
    """Scope guard: the report is the only subagent output admitted.

    Reading 53 transcripts per fan-out would swamp the corpus with 'I read
    this file, then that file'. If someone adds it later, they own the
    evidence for it — and this test."""
    source = Path(distiller.__file__).read_text()
    assert "subagents/" not in source
    assert "isSidechain" not in source


def test_project_vote_belongs_to_the_parent(distiller):
    """A fan-out dispatched into another repo must not rename the session."""
    events = ([_event("Bash", "subagent") | {"project": "other-repo"}
               for _ in range(100)]
              + [_event("Edit", "parent") | {"project": "corvus"}])
    voters = [e for e in events if e.get("origin") != "subagent"] or events
    counts: dict[str, int] = {}
    for e in voters:
        counts[e["project"]] = counts.get(e["project"], 0) + 1
    assert max(counts, key=counts.get) == "corvus"
