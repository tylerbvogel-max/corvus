"""Unit tests for app.agents.runtime — envelope parsing + error types.

Pure-Python coverage of the parser and error types. Full execute_agent
coverage (requires Postgres + LLM) lives in the manual verification plan,
not pytest.
"""
from __future__ import annotations

import os

os.environ.setdefault("TENANT_ID", "corvus-aero")

import pytest

from app.agents.runtime import (
    AgentProtocolError,
    ToolNotAllowedError,
    _describe_mutation,
    _derive_summary,
    _evaluate_done_envelope,
    _parse_envelope,
)


def test_parse_envelope_plain_json_tool_call():
    text = '{"tool": "alpha", "input": {"x": 1}, "reason": "probing"}'
    env = _parse_envelope(text)
    assert env["tool"] == "alpha"
    assert env["input"] == {"x": 1}
    assert env["reason"] == "probing"


def test_parse_envelope_plain_json_done():
    text = '{"done": true, "summary": "all clear"}'
    env = _parse_envelope(text)
    assert env["done"] is True
    assert env["summary"] == "all clear"


def test_parse_envelope_extracts_json_from_prose():
    text = (
        "Let me think. I'll list the findings.\n"
        '{"tool": "list_pending_duplicates", "input": {"limit": 5}, "reason": "start"}\n'
        "Expecting a list back."
    )
    env = _parse_envelope(text)
    assert env["tool"] == "list_pending_duplicates"
    assert env["input"] == {"limit": 5}


def test_parse_envelope_empty_raises():
    with pytest.raises(AgentProtocolError, match="empty"):
        _parse_envelope("")


def test_parse_envelope_whitespace_only_raises():
    with pytest.raises(AgentProtocolError, match="empty"):
        _parse_envelope("   \n\t  ")


def test_parse_envelope_no_json_raises():
    with pytest.raises(AgentProtocolError, match="no JSON object"):
        _parse_envelope("I don't know what to do here")


def test_parse_envelope_malformed_json_raises():
    # Regex finds {...} but content is not valid JSON
    with pytest.raises(AgentProtocolError, match="JSON parse failed"):
        _parse_envelope('prelude {"tool": "alpha", "input": {broken}}')


def test_tool_not_allowed_error_is_exception():
    assert issubclass(ToolNotAllowedError, Exception)


def test_agent_protocol_error_is_exception():
    assert issubclass(AgentProtocolError, Exception)


def test_parse_envelope_handles_nested_objects():
    text = '{"tool": "x", "input": {"nested": {"key": "value", "n": 3}}}'
    env = _parse_envelope(text)
    assert env["input"]["nested"]["key"] == "value"
    assert env["input"]["nested"]["n"] == 3


def test_parse_envelope_regex_grabs_multiline_json():
    text = (
        'Reasoning goes here.\n'
        '{\n'
        '  "tool": "compute_embedding_similarity",\n'
        '  "input": {"neuron_a": 1, "neuron_b": 2},\n'
        '  "reason": "cheap gate"\n'
        '}'
    )
    env = _parse_envelope(text)
    assert env["tool"] == "compute_embedding_similarity"
    assert env["input"]["neuron_a"] == 1


# ── Guardrail 1 — _evaluate_done_envelope (mutation-vs-claim reconciliation)


def test_done_envelope_accepted_when_no_constraints():
    assert _evaluate_done_envelope(
        mutations=0, expected_mutations=None,
        called_verification_tool=False, require_verification_for=None,
    ) is None


def test_done_envelope_rejected_when_mutations_below_expected():
    rej = _evaluate_done_envelope(
        mutations=0, expected_mutations=1,
        called_verification_tool=True, require_verification_for="get_placement_status",
    )
    assert rej is not None
    assert "expected" in rej
    assert "mutations=0" in rej


def test_done_envelope_rejected_when_verification_not_called():
    rej = _evaluate_done_envelope(
        mutations=1, expected_mutations=1,
        called_verification_tool=False, require_verification_for="get_placement_status",
    )
    assert rej is not None
    assert "get_placement_status" in rej


def test_done_envelope_accepted_when_all_conditions_met():
    assert _evaluate_done_envelope(
        mutations=1, expected_mutations=1,
        called_verification_tool=True, require_verification_for="get_placement_status",
    ) is None


def test_done_envelope_mutation_check_precedes_verification_check():
    """If both fail, the mutation-count rejection comes first (clearer retry signal)."""
    rej = _evaluate_done_envelope(
        mutations=0, expected_mutations=1,
        called_verification_tool=False, require_verification_for="get_placement_status",
    )
    assert rej is not None
    assert "mutations" in rej


# ── _describe_mutation — tool-specific summary lines


def test_describe_mutation_refine_builds_placement_line():
    line = _describe_mutation(
        tool_name="refine_ingest_classification",
        inp={
            "proposal_id": 117,
            "item_updates": [{
                "item_id": 10, "parent_id": 42, "layer": 3,
                "department": "Engineering", "role_key": "materials",
            }],
        },
        out={"proposal_id": 117, "confidence": 0.88, "promoted": True},
    )
    assert "Placed proposal #117" in line
    assert "parent #42" in line
    assert "layer 3" in line
    assert "Engineering" in line
    assert "artifact" in line and "proposed" in line
    assert "0.88" in line


def test_describe_mutation_flag_uncertain():
    line = _describe_mutation(
        tool_name="flag_ingest_uncertain",
        inp={"proposal_id": 119, "candidates": [{}, {}, {}]},
        out={"candidate_count": 3},
    )
    assert "#119" in line
    assert "3 candidate" in line


def test_describe_mutation_unknown_tool_returns_generic():
    line = _describe_mutation(tool_name="made_up", inp={}, out={})
    assert "made_up" in line


# ── Layer 3 — _derive_summary composes authoritative summary from actions


class _FakeAction:
    """Minimal Action stand-in for _derive_summary which reads kind/state/json."""
    def __init__(self, kind: str, state: str = "applied",
                 input_json: dict | None = None, result_json: dict | None = None) -> None:
        self.kind = kind
        self.state = state
        self.input_json = input_json or {}
        self.result_json = result_json or {}


def test_derive_summary_uses_mutation_actions_not_self_report():
    """The authoritative summary is computed from observed mutations, not the model's text."""
    actions = [
        _FakeAction("agent.tool.get_ingest_proposal_detail", result_json={"proposal_id": 117}),
        _FakeAction("agent.tool.refine_ingest_classification",
                    input_json={
                        "proposal_id": 117,
                        "item_updates": [{"item_id": 10, "parent_id": 42, "layer": 3,
                                          "department": "Engineering", "role_key": "materials"}],
                    },
                    result_json={"proposal_id": 117, "confidence": 0.9, "promoted": True}),
        _FakeAction("agent.tool.get_placement_status",
                    result_json={"proposal_id": 117, "state": "proposed"}),
    ]
    derived = _derive_summary(
        child_actions=actions, mutations=1, tool_calls=3, errors=0,
        auto_aborted=False,
        model_self_report="I placed proposal #117 under #42 and also #118 under #99",
        input_context={"artifact_id": 117},
    )
    # Must describe ONLY what the refine action actually did — not the
    # model's fabricated claim of an extra #118 placement.
    assert "proposal #117" in derived
    assert "parent #42" in derived
    assert "#118" not in derived
    assert "#99" not in derived


def test_derive_summary_reports_auto_abort_path():
    derived = _derive_summary(
        child_actions=[], mutations=0, tool_calls=1, errors=0,
        auto_aborted=True,
        model_self_report="claimed placement",
        input_context={"artifact_id": 205},
    )
    assert "auto-aborted" in derived
    assert "#205" in derived
    assert "claimed placement" in derived


def test_derive_summary_no_mutations_names_it():
    derived = _derive_summary(
        child_actions=[
            _FakeAction("agent.tool.list_pending_ingest_proposals", result_json={"count": 0}),
        ],
        mutations=0, tool_calls=1, errors=0, auto_aborted=False,
        model_self_report="No work to do",
        input_context={},
    )
    assert "No mutations committed" in derived


def test_parse_envelope_takes_first_of_multiple_objects():
    """Observed live (opus): the model batches several tool-call envelopes in
    one turn. The parser must execute the FIRST instead of failing the turn."""
    from app.agents.runtime import _parse_envelope
    text = (
        '{"tool": "mark_duplicate", "input": {"finding_id": 7, "notes": "n"}, "reason": "r"}\n'
        '{"tool": "mark_duplicate", "input": {"finding_id": 8, "notes": "n"}, "reason": "r"}'
    )
    env = _parse_envelope(text)
    assert env["tool"] == "mark_duplicate"
    assert env["input"]["finding_id"] == 7


def test_parse_envelope_multiple_objects_with_prose_prefix():
    from app.agents.runtime import _parse_envelope
    text = 'Processing findings now:\n{"tool": "x", "input": {}, "reason": "r"}\n{"done": true}'
    env = _parse_envelope(text)
    assert env["tool"] == "x"
