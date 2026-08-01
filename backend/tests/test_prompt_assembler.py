"""Tests for prompt assembler."""

from app.models import Neuron
from app.services.scoring_engine import NeuronScoreBreakdown
import app.services.prompt_assembler as prompt_assembler
from app.services.prompt_assembler import assemble_prompt, _estimate_tokens, _get_voice


def _make_neuron(id, label, content=None, summary=None, department="Engineering", role_key="mech_eng", layer=5):
    n = Neuron()
    n.id = id
    n.label = label
    n.content = content
    n.summary = summary
    n.department = department
    n.role_key = role_key
    n.layer = layer
    return n


def _make_score(neuron_id, combined=0.5):
    return NeuronScoreBreakdown(
        neuron_id=neuron_id, burst=0.5, impact=0.5,
        precision=0.5, novelty=0.5, recency=0.5, relevance=0.5, combined=combined,
    )


def test_assemble_basic(monkeypatch):
    monkeypatch.setattr(prompt_assembler, "INTENT_VOICE_MAP", {
        "engineering": "You are an aerospace engineer.",
        "general_query": "You are a general assistant.",
    })
    neurons = {
        1: _make_neuron(1, "Stress Analysis", content="Run FEA for load cases.", summary="Stress analysis"),
    }
    scores = [_make_score(1, 0.8)]
    prompt = assemble_prompt("engineering_analysis", scores, neurons)
    assert "aerospace engineer" in prompt.lower()
    assert "Stress Analysis" in prompt


def test_respects_budget():
    # Create a neuron with very long content
    long_content = "x " * 10000  # ~20K chars = ~5000 tokens
    neurons = {
        1: _make_neuron(1, "Big Neuron", content=long_content, summary="Short summary"),
    }
    scores = [_make_score(1, 0.9)]
    prompt = assemble_prompt("engineering", scores, neurons, budget_tokens=500)
    # Should fall back to summary since content is too large
    assert "Short summary" in prompt
    assert long_content not in prompt


def test_voice_mapping(monkeypatch):
    monkeypatch.setattr(prompt_assembler, "INTENT_VOICE_MAP", {
        "compliance": "You are a compliance specialist.",
        "engineering": "You are an engineer.",
        "finance": "You are a financial analyst.",
        "general_query": "You are a general assistant.",
    })
    assert "compliance" in _get_voice("compliance_risk_review").lower()
    assert "engineer" in _get_voice("engineering_analysis").lower()
    assert "financial" in _get_voice("finance_reporting").lower()


def test_estimate_tokens():
    assert _estimate_tokens("abcd") == 1
    assert _estimate_tokens("abcdefgh") == 2
