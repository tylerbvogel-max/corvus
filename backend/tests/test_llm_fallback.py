"""Provider fallback + codex CLI plumbing — pure-function coverage."""

import os

import pytest

os.environ.setdefault("TENANT_ID", "corvus-mind")

from app.services import llm_provider as lp
from app.routers import query as query_router


def test_fallback_chains_are_same_grade():
    # crosswalk invariant: every chain entry exists in the registry and
    # sits within one price step of its primary (grade parity, not vibes)
    for primary, chain in lp.FALLBACK_CHAINS.items():
        p = lp.MODEL_REGISTRY[primary]
        for name in chain:
            f = lp.MODEL_REGISTRY[name]
            assert f.provider != p.provider, "fallback must change provider"
            assert 0.5 <= (f.input_price / p.input_price) <= 2.0, \
                f"{name} is not same-grade with {primary}"


def test_fallback_candidates_order():
    assert lp._fallback_candidates("codex-terra") == ["codex-terra", "sonnet"]
    assert lp._fallback_candidates("gemini-flash") == ["gemini-flash"]


def test_default_aliases_make_codex_primary(monkeypatch):
    monkeypatch.setattr(
        lp.settings,
        "llm_model_aliases",
        '{"haiku":"codex-luna","sonnet":"codex-terra","opus":"codex-sol"}',
    )
    assert lp._resolve_alias("haiku") == "codex-luna"
    assert lp._resolve_alias("sonnet") == "codex-terra"
    assert lp._resolve_alias("opus") == "codex-sol"
    assert lp._resolve_alias("codex-sol") == "codex-sol"


def test_empty_alias_map_is_single_setting_rollback(monkeypatch):
    monkeypatch.setattr(lp.settings, "llm_model_aliases", "{}")
    assert lp._resolve_alias("haiku") == "haiku"
    assert lp._fallback_candidates("haiku") == ["haiku", "codex-luna"]


def test_available_roster_discloses_effective_routing(monkeypatch):
    monkeypatch.setattr(
        lp.settings,
        "llm_model_aliases",
        '{"haiku":"codex-luna","sonnet":"codex-terra","opus":"codex-sol"}',
    )
    monkeypatch.setattr(lp, "_provider_available", lambda _provider: True)
    rows = lp.get_available_models()
    assert rows[0]["provider"] == "openai_codex"
    haiku = next(row for row in rows if row["display_name"] == "haiku")
    assert haiku["effective_model"] == "codex-luna"
    assert haiku["effective_provider"] == "openai_codex"


def test_lapse_error_classification():
    assert lp._is_lapse_error(RuntimeError("claude CLI failed (exit 1): usage limit reached"))
    assert lp._is_lapse_error(AssertionError("Not logged in — run claude login"))
    assert lp._is_lapse_error(RuntimeError("Your credit balance is too low"))
    assert not lp._is_lapse_error(ValueError("unexpected JSON in response"))
    assert not lp._is_lapse_error(KeyError("text"))


def test_build_codex_args_shape():
    info = lp.MODEL_REGISTRY["codex-terra"]
    args = lp._build_codex_args(info, "low")
    assert args[0] == lp._CODEX_CLI_PATH and args[1] == "exec"
    assert "--json" in args and "-" == args[-1]
    assert args[args.index("-m") + 1] == "gpt-5.6-terra"
    assert "model_reasoning_effort=low" in args
    assert "read-only" in args and "--ephemeral" in args
    # invalid effort never reaches argv
    assert "model_reasoning_effort=ultra" not in lp._build_codex_args(info, "ultra")


def test_parse_codex_events():
    stdout = "\n".join([
        '{"type":"thread.started","thread_id":"t1"}',
        'not json',
        '{"type":"item.completed","item":{"id":"i0","type":"agent_message","text":"verdict: []"}}',
        '{"type":"turn.completed","usage":{"input_tokens":13047,"cached_input_tokens":8960,"output_tokens":5}}',
    ])
    text, usage = lp._parse_codex_events(stdout)
    assert text == "verdict: []"
    assert usage["input_tokens"] == 13047 and usage["cached_input_tokens"] == 8960


def test_codex_provider_registered_and_available_check():
    assert "openai_codex" in lp._PROVIDER_DISPATCH
    # availability mirrors the anthropic pattern: CLI binary presence
    assert isinstance(lp._provider_available("openai_codex"), bool)


@pytest.mark.asyncio
async def test_default_alias_is_served_by_codex(monkeypatch):
    calls = []

    async def fake_call(info, *_args, **_kwargs):
        calls.append(info.display_name)
        return {"text": "ok", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0}

    monkeypatch.setattr(
        lp.settings,
        "llm_model_aliases",
        '{"sonnet":"codex-terra"}',
    )
    monkeypatch.setattr(lp, "_provider_available", lambda _provider: True)
    monkeypatch.setattr(lp, "_call_provider", fake_call)
    monkeypatch.setattr(
        "app.services.model_usage_ledger.record_model_usage",
        lambda **_kwargs: None,
    )
    lp._provider_down_until.clear()

    result = await lp.llm_chat("system", "message", model="sonnet")

    assert calls == ["codex-terra"]
    assert result["served_by"] == "codex-terra"
    assert result["provider"] == "openai_codex"


@pytest.mark.asyncio
async def test_codex_lapse_falls_back_to_anthropic(monkeypatch):
    calls = []

    async def fake_call(info, *_args, **_kwargs):
        calls.append(info.display_name)
        if info.provider == "openai_codex":
            raise AssertionError("codex CLI failed: usage limit reached")
        return {"text": "ok", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0}

    monkeypatch.setattr(
        lp.settings,
        "llm_model_aliases",
        '{"sonnet":"codex-terra"}',
    )
    monkeypatch.setattr(lp, "_provider_available", lambda _provider: True)
    monkeypatch.setattr(lp, "_call_provider", fake_call)
    monkeypatch.setattr(
        "app.services.model_usage_ledger.record_model_usage",
        lambda **_kwargs: None,
    )
    lp._provider_down_until.clear()

    result = await lp.llm_chat("system", "message", model="sonnet")

    assert calls == ["codex-terra", "sonnet"]
    assert result["served_by"] == "sonnet"
    assert result["provider"] == "anthropic"
    assert result["fallback_from"] == "codex-terra"
    lp._provider_down_until.clear()


def test_casual_chat_defaults_to_codex_luna():
    request = query_router.ChatRequest(message="hello")
    assert request.model == "codex-luna"


@pytest.mark.asyncio
async def test_casual_chat_reports_actual_serving_model_and_cost(monkeypatch):
    async def fake_chat(*_args, **_kwargs):
        return {
            "text": "hello",
            "served_by": "codex-luna",
            "input_tokens": 12,
            "output_tokens": 3,
            "cost_usd": 0.0042,
        }

    monkeypatch.setattr(query_router, "llm_chat", fake_chat)

    response = await query_router.simple_chat(
        query_router.ChatRequest(message="hello", model="haiku")
    )

    assert response.model == "codex-luna"
    assert response.cost_usd == 0.0042
