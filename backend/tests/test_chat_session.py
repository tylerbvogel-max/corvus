"""Hero-chat CLI session persistence.

Covers: CLI argv construction for stateless/new/resumed sessions, the
cache-stable prompt restructure (static system preamble + packed context in
the user message), the resume-failure fallback, and the router's opt-in
session-spec resolution.
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

import app.services.executor as ex
import app.services.llm_provider as lp
from app.routers.query import _session_spec_from_request
from app.schemas import QueryRequest


# ── CLI argv construction ────────────────────────────────────────────────

def _haiku_info():
    return lp.MODEL_REGISTRY["haiku"]


def test_stateless_args_unchanged():
    args = lp._build_anthropic_args(_haiku_info(), "sys", "low", None)
    assert "--no-session-persistence" in args
    ti = args.index("--tools")
    assert args[ti + 1] == "", "built-in CLI tools must be disabled (~17.8k tokens/call)"
    assert "--session-id" not in args and "--resume" not in args
    assert args[args.index("--system-prompt") + 1] == "sys"


def test_new_session_args():
    args = lp._build_anthropic_args(
        _haiku_info(), "sys", "low", {"session_id": "abc-123", "resume": False})
    assert "--no-session-persistence" not in args, \
        "persisting calls must not disable session persistence"
    assert args[args.index("--session-id") + 1] == "abc-123"
    assert "--resume" not in args


def test_resume_session_args():
    args = lp._build_anthropic_args(
        _haiku_info(), "sys", "low", {"session_id": "abc-123", "resume": True})
    assert args[args.index("--resume") + 1] == "abc-123"
    assert "--session-id" not in args and "--no-session-persistence" not in args


# ── Cache-stable prompt restructure ──────────────────────────────────────

class _Ctx:
    def __init__(self, system_prompt="PACKED NEURON CONTEXT"):
        self.system_prompt = system_prompt


def test_session_payload_moves_context_into_user_message():
    sys_prompt, msg = ex._session_call_payload(_Ctx(), "What is the torque spec?")
    assert sys_prompt == ex._CHAT_SESSION_PREAMBLE, \
        "session system prompt must be the static (cache-stable) preamble"
    assert "PACKED NEURON CONTEXT" in msg and "What is the torque spec?" in msg
    assert msg.index("PACKED NEURON CONTEXT") < msg.index("What is the torque spec?")


def test_session_payload_no_ctx_passthrough():
    assert ex._session_call_payload(None, "hi") == ("", "hi")


# ── Direct-call session mode + resume fallback ───────────────────────────

def _llm_result(**extra):
    return {"text": "answer", "input_tokens": 10, "output_tokens": 5,
            "cost_usd": 0.0, "cache_creation_tokens": 0, "cache_read_tokens": 0,
            "model_version": "haiku", **extra}


def test_direct_call_session_mode_restructures_prompt():
    seen = {}

    async def fake_llm(**kwargs):
        seen.update(kwargs)
        return _llm_result(session_id="sess-1")

    with patch.object(ex, "llm_chat", fake_llm):
        out = asyncio.run(ex._run_direct_call(
            _Ctx(), "question?", None, model="haiku",
            session_spec={"session_id": "sess-1", "resume": True}))
    assert seen["system_prompt"] == ex._CHAT_SESSION_PREAMBLE
    assert "PACKED NEURON CONTEXT" in seen["user_message"]
    assert seen["session"] == {"session_id": "sess-1", "resume": True}
    assert out["session_id"] == "sess-1"


def test_direct_call_resume_failure_falls_back_to_fresh_session():
    calls = []

    async def flaky_llm(**kwargs):
        calls.append(kwargs["session"])
        if len(calls) == 1:
            raise AssertionError("claude CLI failed (exit 1): no conversation")
        return _llm_result(session_id=kwargs["session"]["session_id"])

    with patch.object(ex, "llm_chat", flaky_llm):
        out = asyncio.run(ex._run_direct_call(
            _Ctx(), "question?", None, model="haiku",
            session_spec={"session_id": "gone-123", "resume": True}))
    assert len(calls) == 2
    assert calls[0]["resume"] is True
    assert calls[1]["resume"] is False and calls[1]["session_id"] != "gone-123"
    assert out["text"] == "answer"


def test_direct_call_new_session_failure_is_not_retried():
    async def broken_llm(**kwargs):
        raise AssertionError("claude CLI failed (exit 1)")

    with patch.object(ex, "llm_chat", broken_llm):
        with pytest.raises(AssertionError):
            asyncio.run(ex._run_direct_call(
                _Ctx(), "q?", None, model="haiku",
                session_spec={"session_id": "new-1", "resume": False}))


# ── Router opt-in resolution ─────────────────────────────────────────────

def _req(**kw):
    return QueryRequest(message="hello", **kw)


def test_session_spec_requires_opt_in(monkeypatch):
    from app.routers import query as q
    monkeypatch.setattr(q.settings, "chat_session_persistence", True)
    assert _session_spec_from_request(_req()) is None
    spec = _session_spec_from_request(_req(persist_session=True))
    assert spec is not None and spec["resume"] is False and spec["session_id"]
    spec2 = _session_spec_from_request(_req(llm_session_id="a" * 12))
    assert spec2 == {"session_id": "a" * 12, "resume": True, "refresh": False}
    spec3 = _session_spec_from_request(_req(llm_session_id="a" * 12, refresh_context=True))
    assert spec3["refresh"] is True


def test_session_spec_kill_switch(monkeypatch):
    from app.routers import query as q
    monkeypatch.setattr(q.settings, "chat_session_persistence", False)
    assert _session_spec_from_request(_req(persist_session=True)) is None


def test_llm_session_id_pattern_rejects_garbage():
    with pytest.raises(ValidationError):
        _req(llm_session_id="../../etc/passwd")
    with pytest.raises(ValidationError):
        _req(llm_session_id="x; rm -rf /")


# ── Drift-gated recall ───────────────────────────────────────────────────

class _ScoredCtx:
    """Fake PreparedContext with a packed neuron-id set."""
    def __init__(self, ids, system_prompt="STORED BLOCK"):
        self.system_prompt = system_prompt
        self.all_scored = [type("S", (), {"neuron_id": i})() for i in ids]


def _gate_env(monkeypatch, stored_ids=None, overlap_threshold=0.6):
    monkeypatch.setattr(ex.settings, "chat_context_drift_gate", True)
    monkeypatch.setattr(ex.settings, "chat_context_reuse_overlap", overlap_threshold)
    ex._session_ctx_cache.clear()
    if stored_ids is not None:
        ex._remember_session_context("sess-1", _ScoredCtx(stored_ids))


_SLOT = [{"mode": "haiku_neuron"}]


def test_gate_reuses_on_near_duplicate_pack(monkeypatch):
    _gate_env(monkeypatch, stored_ids=range(10))
    spec = {"session_id": "sess-1", "resume": True}
    fresh = _ScoredCtx(list(range(8)) + [90, 91], system_prompt="FRESH")  # 8/10 overlap
    ctx, overlap = ex._apply_drift_gate(spec, _SLOT, fresh)
    assert ctx is not None and ctx.system_prompt == "STORED BLOCK", \
        "reuse must return the ACTIVE ctx so citation grading stays consistent"
    assert overlap == pytest.approx(0.8)
    assert spec["reuse_context"] is True


def test_gate_repacks_on_drifted_pack(monkeypatch):
    _gate_env(monkeypatch, stored_ids=range(10))
    spec = {"session_id": "sess-1", "resume": True}
    fresh = _ScoredCtx(range(100, 110))  # zero overlap
    ctx, overlap = ex._apply_drift_gate(spec, _SLOT, fresh)
    assert ctx is None and "reuse_context" not in spec
    assert overlap == pytest.approx(0.0)


def test_gate_never_reuses_first_turn_or_refresh(monkeypatch):
    _gate_env(monkeypatch, stored_ids=range(10))
    fresh = _ScoredCtx(range(10))  # identical pack
    first = {"session_id": "sess-1", "resume": False}
    assert ex._apply_drift_gate(first, _SLOT, fresh) == (None, None)
    refresh = {"session_id": "sess-1", "resume": True, "refresh": True}
    assert ex._apply_drift_gate(refresh, _SLOT, fresh) == (None, None)


def test_gate_off_for_multi_slot_and_kill_switch(monkeypatch):
    _gate_env(monkeypatch, stored_ids=range(10))
    spec = {"session_id": "sess-1", "resume": True}
    fresh = _ScoredCtx(range(10))
    two = [{"mode": "haiku_neuron"}, {"mode": "opus_neuron"}]
    assert ex._apply_drift_gate(spec, two, fresh) == (None, None)
    monkeypatch.setattr(ex.settings, "chat_context_drift_gate", False)
    assert ex._apply_drift_gate(spec, _SLOT, fresh) == (None, None)


def test_gate_missing_state_repacks(monkeypatch):
    _gate_env(monkeypatch, stored_ids=None)  # restart wiped the cache
    spec = {"session_id": "sess-1", "resume": True}
    assert ex._apply_drift_gate(spec, _SLOT, _ScoredCtx(range(10))) == (None, None)


def test_session_ctx_cache_bounded():
    ex._session_ctx_cache.clear()
    for i in range(ex._SESSION_CTX_MAX + 20):
        ex._remember_session_context(f"s-{i}", _ScoredCtx([1]))
    assert len(ex._session_ctx_cache) == ex._SESSION_CTX_MAX
    assert "s-0" not in ex._session_ctx_cache, "oldest must be evicted"
    ex._session_ctx_cache.clear()


def test_update_session_context_rekeys_to_returned_id():
    ex._session_ctx_cache.clear()
    ex._update_session_context(
        {"session_id": "asked-1"},
        [{"llm_session_id": "forked-2"}],
        _ScoredCtx([1, 2, 3]),
    )
    assert "forked-2" in ex._session_ctx_cache
    assert ex._session_ctx_cache["forked-2"]["packed_ids"] == frozenset({1, 2, 3})
    ex._session_ctx_cache.clear()


def test_reuse_payload_sends_bare_question():
    sys_prompt, msg = ex._session_call_payload(_Ctx(), "just the question?", False)
    assert sys_prompt == ex._CHAT_SESSION_PREAMBLE
    assert msg == "just the question?"
    assert "PACKED" not in msg


def test_reuse_fallback_repacks_full_context():
    """A reuse turn whose resume fails lands in a FRESH session with no
    transcript — the fallback call must carry the full context block."""
    calls = []

    async def flaky_llm(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise AssertionError("claude CLI failed: no conversation")
        return _llm_result(session_id="fresh-9")

    with patch.object(ex, "llm_chat", flaky_llm):
        asyncio.run(ex._run_direct_call(
            _Ctx(), "question?", None, model="haiku",
            session_spec={"session_id": "gone", "resume": True, "reuse_context": True}))
    assert "PACKED NEURON CONTEXT" not in calls[0]["user_message"], "reuse turn sends bare question"
    assert "PACKED NEURON CONTEXT" in calls[1]["user_message"], "fallback must repack"
