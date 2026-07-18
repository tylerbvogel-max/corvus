"""Policy, inhibition, compatibility, and usage-accounting regressions."""

import json
from unittest.mock import AsyncMock

import numpy as np
import pytest

import app.services.executor as executor
import app.services.inhibitory_service as inhibition
from app.services.memory_assembly import assemble_memory_packet
from app.services.scoring_engine import NeuronScoreBreakdown


def _score(nid: int, combined: float | None = None) -> NeuronScoreBreakdown:
    return NeuronScoreBreakdown(
        neuron_id=nid, burst=0, impact=0, precision=0, novelty=0,
        recency=0, relevance=0.5,
        combined=combined if combined is not None else 1.0 - nid / 1000,
    )


def _meta(text: str, embedding, scope=("Projects", "corvus")) -> dict:
    return {"text": text, "embedding": np.asarray(embedding, dtype=np.float32), "scope": scope}


def test_duplicate_suppressed_but_high_cosine_complementary_memories_survive():
    exact_a = _meta("same durable fact", [1.0, 0.0])
    exact_b = _meta("same durable fact", [1.0, 0.0])
    complementary = _meta("different operational consequence and caveat", [0.999, 0.01])
    assert inhibition._deterministic_duplicate(exact_a, exact_b, 0.92) is True
    assert inhibition._deterministic_duplicate(exact_a, complementary, 0.92) is False
    assert inhibition._deterministic_duplicate(
        exact_a, _meta("same durable fact", [1.0, 0.0], scope=("Other", "corvus")), 0.92,
    ) is False


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_redundancy_pass_preserves_ranked_order_and_counts_suppression():
    rows = [
        (1, "Projects", "corvus", "same fact", None, json.dumps([1.0, 0.0])),
        (2, "Projects", "corvus", "same fact", None, json.dumps([1.0, 0.0])),
        (3, "Projects", "corvus", "complementary caveat", None, json.dumps([1.0, 0.0])),
    ]
    db = AsyncMock()
    db.execute.return_value = _Rows(rows)
    survivors, count = await inhibition.apply_token_bounded_redundancy(
        db, [_score(1), _score(2), _score(3)])
    assert [s.neuron_id for s in survivors] == [1, 3]
    assert count == 1


@pytest.mark.asyncio
async def test_feature_flag_restores_legacy_inhibition(monkeypatch):
    scores = [_score(1), _score(2), _score(3)]
    legacy = AsyncMock(return_value=(scores, 2))
    token = AsyncMock(return_value=(scores[:1], 2))
    monkeypatch.setattr(inhibition, "apply_inhibition", legacy)
    monkeypatch.setattr(inhibition, "apply_token_bounded_redundancy", token)
    monkeypatch.setattr(executor.settings, "inhibition_enabled", True)

    monkeypatch.setattr(executor.settings, "token_bounded_assembly_enabled", False)
    out, cap, suppressed = await executor._apply_inhibition_and_boost(None, scores, 3, None)
    assert out == scores and cap == 2 and suppressed == 0
    legacy.assert_awaited_once()

    monkeypatch.setattr(executor.settings, "token_bounded_assembly_enabled", True)
    out, cap, suppressed = await executor._apply_inhibition_and_boost(None, scores, 3, None)
    assert out == scores[:1] and cap == 3 and suppressed == 2
    token.assert_awaited_once()


def test_explicit_smaller_top_k_is_respected_in_token_mode(monkeypatch):
    monkeypatch.setattr(executor.settings, "token_bounded_assembly_enabled", True)
    monkeypatch.setattr(executor.settings, "memory_candidate_limit", 150)
    assert executor._slot_spread_cfg({"top_k": 3, "token_budget": 8000})[2] == 3
    assert executor._slot_spread_cfg({"token_budget": 8000})[2] == 150


def test_cache_created_and_read_tokens_are_in_observed_total():
    class _Ctx:
        neurons_delivered = 7
        estimated_memory_tokens = 3000

    payload = executor._format_slot_result_dict(
        "haiku_neuron", "haiku", "neuron", True, "low", 0, [], None,
        {
            "response_text": "answer", "input_tokens": 100,
            "output_tokens": 10, "cost_usd": 0.01,
            "cache_creation": 200, "cache_read": 300,
        },
        8000, _Ctx(), None,
    )
    assert payload["observed_total_input_tokens"] == 600
    assert payload["memory_estimation_error_tokens"] == -2400
    assert payload["top_k"] == 7


@pytest.mark.asyncio
async def test_legacy_eight_vs_token_packet_near_three_thousand(monkeypatch):
    """Planted aperture regression: count governor vs token governor."""
    scores = [_score(i) for i in range(1, 101)]
    monkeypatch.setattr(inhibition.settings, "inhibition_enabled", True)
    monkeypatch.setattr(inhibition.settings, "inhibition_default_threshold", 15)
    monkeypatch.setattr(inhibition.settings, "inhibition_default_max_survivors", 8)
    monkeypatch.setattr(
        inhibition, "_load_neuron_metadata",
        AsyncMock(return_value=(
            {i: "Projects" for i in range(1, 101)}, {}, {}, {},
        )),
    )
    monkeypatch.setattr(inhibition, "_load_region_regulators", AsyncMock(return_value={}))
    _legacy_ranked, legacy_survivors = await inhibition.apply_inhibition(None, scores, 100)

    from app.models import Neuron
    neurons = {}
    for i in range(1, 101):
        n = Neuron()
        n.id = i
        n.label = f"Distinct {i}"
        n.content = f"Distinct memory {i}: " + (chr(64 + ((i - 1) % 26) + 1) * 120)
        n.summary = None
        n.department = "Projects"
        n.role_key = "corvus"
        n.layer = 5
        n.authority_level = "informational"
        n.source_origin = "distiller"
        neurons[i] = n
    packet = assemble_memory_packet(scores, neurons, 3000, 250)

    assert legacy_survivors == 8
    assert len(packet.scores) > legacy_survivors
    assert 2800 <= packet.estimated_tokens <= 3000
    assert packet.stop_reason == "token_ceiling"

