"""Provider fallback + codex CLI plumbing — pure-function coverage."""

import os

os.environ.setdefault("TENANT_ID", "corvus-mind")

from app.services import llm_provider as lp


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
    assert lp._fallback_candidates("sonnet") == ["sonnet", "codex-terra"]
    assert lp._fallback_candidates("gemini-flash") == ["gemini-flash"]


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
