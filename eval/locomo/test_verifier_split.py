"""Step 02 (mind-answer-verifier-split) — verdict plumbing.

The split's trust properties live in three small functions: verdict parsing
fails closed, only unsupported/unchecked claims are silenced, and the
verifier never sees the gold answer. Cheap to test, expensive to get wrong.
"""
import asyncio
import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("run_locomo.py")
SPEC = importlib.util.spec_from_file_location("run_locomo", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
locomo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(locomo)


def test_parse_verdict_accepts_the_three_contract_values():
    for v in ("supported", "partially-supported", "unsupported"):
        assert locomo.parse_verdict('{"verdict": "%s"}' % v) == v


def test_parse_verdict_tolerates_surrounding_prose():
    assert locomo.parse_verdict(
        'Sure! {"verdict": "supported"} hope that helps') == "supported"


def test_parse_verdict_fails_closed_on_garbage():
    for text in ("", "supported", '{"verdict": "maybe"}', '{"other": 1}',
                 '{broken json', None):
        assert locomo.parse_verdict(text) == "unparseable"


def test_apply_verdict_silences_only_unchecked_or_unsupported_claims():
    draft = "Wrenfield glazed a teal ewer"  # synthetic, not dataset text
    assert locomo.apply_verdict(draft, "supported") == draft
    assert locomo.apply_verdict(draft, "partially-supported") == draft
    assert locomo.apply_verdict(draft, "unsupported") == locomo.REFUSAL_TEXT
    assert locomo.apply_verdict(draft, "unparseable") == locomo.REFUSAL_TEXT


def test_is_refusal_text_matches_lib_convention():
    assert locomo.is_refusal_text("No information available")
    assert locomo.is_refusal_text("  no information available.")
    assert not locomo.is_refusal_text("The trip was in May")
    assert not locomo.is_refusal_text("")


def test_verifier_never_sees_gold():
    """Structural guard: verify_claim's inputs are memories, question and
    draft — there is no parameter through which gold could arrive, and the
    prompt never references one."""
    captured = {}

    async def fake_llm_retry(**kwargs):
        captured.update(kwargs)
        return {"text": '{"verdict": "unsupported"}'}

    original = locomo.llm_retry
    locomo.llm_retry = fake_llm_retry
    try:
        out = asyncio.run(locomo.verify_claim(
            "What did the speaker glaze?", "- kiln note: a teal ewer",
            "a teal ewer"))
    finally:
        locomo.llm_retry = original

    assert out["verdict"] == "unsupported"
    assert out["latency_ms"] >= 0
    assert captured["workload"] == "verify"
    assert captured["model"] == locomo.VERIFY_MODEL
    assert "GOLD" not in captured["system_prompt"]
    assert "GOLD" not in captured["user_message"]
    assert "gold" not in locomo.VERIFY_PROMPT.lower()
