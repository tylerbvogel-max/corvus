"""Integration tests for the execute_agent turn loop with a scripted fake LLM.

Previously only the pure helpers were tested; the loop itself (dispatch,
allow-list rejection, schema enforcement, HTTPException recovery, LLM-failure
abort, per-mutation commits, per-agent effort) had never executed under test.
No real LLM, no real DB.
"""
import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException

import app.agents.runtime as rt
from app.agents.registry import AgentDefinition, AgentTrigger
from app.agents.tool_base import Tool, ToolRegistry
from app.services.llm_provider import effort_var


# ── Fakes ───────────────────────────────────────────────────────────────

class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self):
        self.added = []
        self._next_id = 1
        self.commits = 0

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = self._next_id
                self._next_id += 1

    async def commit(self):
        self.commits += 1

    async def execute(self, _stmt):
        children = [a for a in self.added if getattr(a, "parent_action_id", None) is not None]
        return _FakeResult(children)


def _agent(allow=("echo_read", "echo_mut"), max_turns=6, effort=""):
    return AgentDefinition(
        name="test_agent", role="tester", description="", model="haiku",
        max_tokens=100, max_turns=max_turns, tool_allow_list=tuple(allow),
        system_prompt="You are a test agent.", trigger=AgentTrigger(),
        source_path=Path("/dev/null"), effort=effort,
    )


def _tool_registry():
    reg = ToolRegistry()

    async def echo_read(_s, inp):
        return {"ok": True, "echo": inp.get("q")}

    async def echo_mut(_s, _inp):
        return {"ok": True, "wrote": 1}

    async def boom_http(_s, _inp):
        raise HTTPException(status_code=404, detail="finding is stale")

    reg.register(Tool(name="echo_read", description="read", input_schema={
        "type": "object", "properties": {"q": {"type": "string"}},
    }, func=echo_read, is_mutating=False))
    reg.register(Tool(name="echo_mut", description="mutate", input_schema={
        "type": "object",
        "properties": {"rationale": {"type": "string", "minLength": 20, "maxLength": 500}},
        "required": ["rationale"],
    }, func=echo_mut, is_mutating=True))
    reg.register(Tool(name="boom_http", description="raises HTTPException",
                      input_schema={"type": "object"}, func=boom_http, is_mutating=True))
    return reg


class _StubAgentRegistry:
    def __init__(self, agent):
        self._agent = agent

    def get(self, _name):
        return self._agent


def _wire(monkeypatch, agent, llm_responses):
    """Patch registries + a scripted llm_chat into the runtime module."""
    monkeypatch.setattr(rt, "get_agent_registry", lambda: _StubAgentRegistry(agent))
    monkeypatch.setattr(rt, "get_tool_registry", _tool_registry)
    script = list(llm_responses)
    seen_efforts: list = []

    async def fake_llm_chat(**_kwargs):
        seen_efforts.append(effort_var.get())
        assert script, "fake LLM script exhausted — agent looped more than expected"
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return {"text": item, "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0}

    monkeypatch.setattr(rt, "llm_chat", fake_llm_chat)
    return seen_efforts


_LONG_RATIONALE = "this rationale is comfortably over twenty characters long"


# ── Tests ───────────────────────────────────────────────────────────────

def test_happy_path_tool_then_done(monkeypatch):
    agent = _agent()
    _wire(monkeypatch, agent, [
        '{"tool": "echo_mut", "input": {"rationale": "%s"}, "reason": "commit"}' % _LONG_RATIONALE,
        '{"done": true, "summary": "did the thing"}',
    ])
    sess = _FakeSession()
    result = asyncio.run(rt.execute_agent(sess, "test_agent"))
    assert result.tool_calls == 1
    assert result.mutations == 1
    assert result.errors == 0
    assert result.turns == 2
    assert result.model_self_report == "did the thing"
    # Per-mutation durability: one commit for the mutation + one at finalize
    assert sess.commits == 2


def test_allow_list_rejection_is_recoverable(monkeypatch):
    agent = _agent(allow=("echo_read",))
    _wire(monkeypatch, agent, [
        '{"tool": "echo_mut", "input": {"rationale": "%s"}, "reason": "try forbidden"}' % _LONG_RATIONALE,
        '{"done": true, "summary": "gave up"}',
    ])
    sess = _FakeSession()
    result = asyncio.run(rt.execute_agent(sess, "test_agent"))
    assert result.errors == 1
    assert result.mutations == 0
    # The rejection is recorded as a failed child action for audit
    failed = [a for a in sess.added if getattr(a, "state", "") == "failed"]
    assert len(failed) == 1


def test_schema_violation_rejected_then_retry_succeeds(monkeypatch):
    """The rationale 20-500 contract is enforced at dispatch: a short rationale
    is rejected with feedback, and the corrected retry commits."""
    agent = _agent()
    _wire(monkeypatch, agent, [
        '{"tool": "echo_mut", "input": {"rationale": "too short"}, "reason": "first try"}',
        '{"tool": "echo_mut", "input": {"rationale": "%s"}, "reason": "retry"}' % _LONG_RATIONALE,
        '{"done": true, "summary": "committed on retry"}',
    ])
    sess = _FakeSession()
    result = asyncio.run(rt.execute_agent(sess, "test_agent"))
    assert result.errors == 1, "short rationale must be rejected"
    assert result.mutations == 1, "corrected retry must commit"
    failed = [a for a in sess.added if getattr(a, "state", "") == "failed"]
    assert any("schema violation" in (a.error_message or "") for a in failed)


def test_http_exception_is_recoverable_not_fatal(monkeypatch):
    """Shared proposal helpers raise HTTPException on stale findings; the run
    must continue with feedback instead of crashing."""
    agent = _agent(allow=("boom_http",))
    _wire(monkeypatch, agent, [
        '{"tool": "boom_http", "input": {}, "reason": "touch stale finding"}',
        '{"done": true, "summary": "skipped the stale finding"}',
    ])
    sess = _FakeSession()
    result = asyncio.run(rt.execute_agent(sess, "test_agent"))
    assert result.errors == 1
    assert result.mutations == 0
    failed = [a for a in sess.added if getattr(a, "state", "") == "failed"]
    assert any("HTTP 404" in (a.error_message or "") for a in failed)


def test_llm_failure_aborts_cleanly_with_record(monkeypatch):
    """A CLI/LLM failure mid-run finalizes the root action with the failure on
    record instead of leaving it stuck 'pending'."""
    agent = _agent()
    _wire(monkeypatch, agent, [AssertionError("claude CLI failed (exit 1)")])
    sess = _FakeSession()
    result = asyncio.run(rt.execute_agent(sess, "test_agent"))
    assert result.errors == 1
    assert "LLM call failed" in result.summary
    root = sess.added[0]
    assert root.state == "applied", "root action must be finalized, not stuck pending"
    assert root.result_json["auto_aborted"] is True
    assert "LLM call failed" in root.result_json["abort_reason"]


def test_done_guardrail_auto_abort_without_mutation(monkeypatch):
    agent = _agent()
    _wire(monkeypatch, agent, [
        '{"done": true, "summary": "premature"}',
        '{"done": true, "summary": "still premature"}',
    ])
    sess = _FakeSession()
    result = asyncio.run(rt.execute_agent(sess, "test_agent", expected_mutations=1))
    assert result.mutations == 0
    root = sess.added[0]
    assert root.result_json["auto_aborted"] is True
    assert root.result_json["done_rejections"] == 2


def test_agent_effort_applied_and_restored(monkeypatch):
    agent = _agent(effort="medium")
    seen = _wire(monkeypatch, agent, ['{"done": true, "summary": "noop"}'])

    def run():
        effort_var.set("low")  # ambient request-level effort
        result = asyncio.run(rt.execute_agent(_FakeSession(), "test_agent"))
        return result, effort_var.get()

    import contextvars
    _, ambient_after = contextvars.copy_context().run(run)
    assert seen == ["medium"], "agent turn must run at the agent's own effort"
    assert ambient_after == "low", "ambient effort must be restored after the turn"
