"""Reflective judgments must remain falsifiable and evidence-gated."""

import json
from types import SimpleNamespace

import pytest

from app.services import reflection_review


LESSONS = [
    SimpleNamespace(id=11, label="channel split", content="Pooled injection hid a failing SessionStart channel."),
    SimpleNamespace(id=12, label="fixture drift", content="The fixture moved during evaluation; inspect migration evidence."),
    SimpleNamespace(id=13, label="forced zero", content="A zero was structurally forced by session deduplication."),
]
DRAFT = {
    "name": "mind-diagnose-recall", "description": "Use when diagnosing recall.",
    "kind": "procedure", "task": "Diagnose recall before changing ranking.",
    "reflection": {
        "claim": "A pooled rate can hide one failing delivery channel.",
        "prediction": "A matched channel split will expose the weaker channel when this claim applies.",
        "falsifier": "Comparable trigger rates from a verified probe would defeat the hidden-channel explanation.",
        "probe": "Compare trigger-level rates with a read-only verified metrics query.",
    },
    "facts": [
        {"source_id": 11, "quote": LESSONS[0].content},
        {"source_id": 12, "quote": LESSONS[1].content},
    ],
    "steps": [
        {"when": "The pooled rate falls", "action": "Split by channel and inspect fixture state.",
         "source_ids": [11, 12], "basis": "synthesized", "check": "Compare matched rates."},
        {"when": "A result is zero", "action": "Verify the metric can vary.",
         "source_ids": [13], "basis": "derived-check", "check": "Inspect metric and dedup code."},
    ],
}


@pytest.mark.asyncio
async def test_compose_model_emits_prediction_and_falsifier_after_critic(monkeypatch):
    calls = []
    async def fake_chat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            assert "falsifier" in kwargs["system_prompt"]
            return {"text": json.dumps(DRAFT)}
        return {"text": '{"supported":true,"synthesis_gain":true,"issues":[]}'}
    from app.services import llm_provider
    monkeypatch.setattr(llm_provider, "llm_chat", fake_chat)
    result = await reflection_review.compose_model(LESSONS)
    assert result["reflection"]["falsifier"].startswith("Comparable")
    assert result["critic_pass"] is True
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_new_evidence_requires_exact_quote_and_independent_critic(monkeypatch):
    record = {"reflection": DRAFT["reflection"], "source_ids": [11, 12, 13]}
    new = [SimpleNamespace(id=44, content="The verified split showed comparable trigger rates.", label="counterexample")]
    calls = []
    async def fake_chat(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {"text": json.dumps({"observations": [{
                "source_id": 44, "verdict": "falsifies",
                "quote": "The verified split showed comparable trigger rates.",
                "reason": "The prediction did not hold under the probe."}]})}
        return {"text": '{"supported":true,"issues":[]}'}
    from app.services import llm_provider
    monkeypatch.setattr(llm_provider, "llm_chat", fake_chat)
    result = await reflection_review.judge_new_evidence(record, new)
    assert result[0]["verdict"] == "falsifies"
    assert result[0]["source_id"] == 44
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_forged_new_evidence_quote_never_reaches_critic(monkeypatch):
    calls = []
    async def fake_chat(**kwargs):
        calls.append(kwargs)
        return {"text": json.dumps({"observations": [{
            "source_id": 44, "verdict": "falsifies",
            "quote": "The system is definitely broken", "reason": "bad"}]})}
    from app.services import llm_provider
    monkeypatch.setattr(llm_provider, "llm_chat", fake_chat)
    record = {"reflection": DRAFT["reflection"], "source_ids": [11, 12, 13]}
    new = [SimpleNamespace(id=44, content="The verified split showed comparable trigger rates.", label="counterexample")]
    assert await reflection_review.judge_new_evidence(record, new) is None
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_valid_no_match_can_advance_evidence_clock(monkeypatch):
    calls = []
    async def fake_chat(**kwargs):
        calls.append(kwargs)
        return {"text": '{"observations":[]}'}
    from app.services import llm_provider
    monkeypatch.setattr(llm_provider, "llm_chat", fake_chat)
    record = {"reflection": DRAFT["reflection"], "source_ids": [11, 12, 13]}
    new = [SimpleNamespace(id=44, content="An unrelated observation.", label="unrelated")]
    assert await reflection_review.judge_new_evidence(record, new) == []
    assert len(calls) == 1
