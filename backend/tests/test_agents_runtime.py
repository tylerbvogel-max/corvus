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
