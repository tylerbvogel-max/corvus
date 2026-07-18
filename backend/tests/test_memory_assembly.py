"""Hermetic tests for token-bounded neuron packet assembly."""

from app.models import Neuron
from app.services.memory_assembly import (
    UTF8_BYTES_DIV4_CEIL_V1,
    assemble_memory_packet,
    estimate_memory_tokens,
    render_memory_entry,
)
from app.services.scoring_engine import NeuronScoreBreakdown


def _score(nid: int, combined: float | None = None) -> NeuronScoreBreakdown:
    value = combined if combined is not None else 1.0 - (nid / 1000)
    return NeuronScoreBreakdown(
        neuron_id=nid, burst=0.1, impact=0.2, precision=0.3,
        novelty=0.4, recency=0.5, relevance=0.6, combined=value,
    )


def _neuron(
    nid: int,
    content: str,
    summary: str | None = None,
    *,
    source_origin: str = "distiller",
) -> Neuron:
    neuron = Neuron()
    neuron.id = nid
    neuron.label = f"Memory {nid}"
    neuron.content = content
    neuron.summary = summary
    neuron.department = "Projects"
    neuron.role_key = "corvus"
    neuron.layer = 5
    neuron.authority_level = "organizational"
    neuron.source_origin = source_origin
    return neuron


def _labels(scores):
    return {s.neuron_id: f"FQ-{s.neuron_id:06X}" for s in scores}


def test_stops_at_token_ceiling_without_splitting_and_preserves_rank_order():
    scores = [_score(1), _score(2), _score(3)]
    neurons = {i: _neuron(i, f"FULL-{i}-" + (str(i) * 220)) for i in range(1, 4)}
    first_two = "\n\n".join(
        render_memory_entry(s, neurons[s.neuron_id], _labels(scores)[s.neuron_id])
        for s in scores[:2]
    )
    budget = estimate_memory_tokens(first_two)

    packet = assemble_memory_packet(
        scores, neurons, budget, 20, citation_labels=_labels(scores))

    assert [s.neuron_id for s in packet.scores] == [1, 2]
    assert packet.stop_reason == "token_ceiling"
    assert packet.estimated_tokens <= budget
    assert packet.chars == len(packet.memory_text)
    assert packet.utf8_bytes == len(packet.memory_text.encode("utf-8"))
    assert packet.estimator_version == UTF8_BYTES_DIV4_CEIL_V1
    assert "FULL-1-" in packet.memory_text and "FULL-2-" in packet.memory_text
    assert "FULL-3-" not in packet.memory_text


def test_nonfitting_neuron_is_not_split_and_does_not_starve_shorter_ranked_memory():
    scores = [_score(1), _score(2), _score(3)]
    neurons = {
        1: _neuron(1, "first compact fact"),
        2: _neuron(2, "UNSPLITTABLE-" + ("x" * 4000)),
        3: _neuron(3, "third compact fact"),
    }
    target = "\n\n".join([
        render_memory_entry(scores[0], neurons[1], _labels(scores)[1]),
        render_memory_entry(scores[2], neurons[3], _labels(scores)[3]),
    ])
    packet = assemble_memory_packet(
        scores, neurons, estimate_memory_tokens(target), 20,
        citation_labels=_labels(scores),
    )
    assert [s.neuron_id for s in packet.scores] == [1, 3]
    assert "UNSPLITTABLE" not in packet.memory_text
    assert packet.stop_reason == "token_ceiling"


def test_first_oversized_neuron_is_included_and_flagged():
    scores = [_score(1)]
    neurons = {1: _neuron(1, "oversized " * 1000)}
    packet = assemble_memory_packet(scores, neurons, 10, 20, citation_labels=_labels(scores))
    assert [s.neuron_id for s in packet.scores] == [1]
    assert packet.estimated_tokens > packet.budget
    assert packet.oversized_first_neuron is True
    assert packet.stop_reason == "candidate_exhausted"


def test_verbose_neuron_uses_stored_summary_without_generated_truncation():
    scores = [_score(1), _score(2)]
    neurons = {
        1: _neuron(1, "verbose-full-marker " * 1000, summary="safe compact summary"),
        2: _neuron(2, "second fact"),
    }
    compact = render_memory_entry(scores[0], neurons[1], _labels(scores)[1], "summary")
    budget = estimate_memory_tokens(compact) + 2
    packet = assemble_memory_packet(scores, neurons, budget, 20, citation_labels=_labels(scores))
    assert packet.representations[1] == "summary"
    assert "safe compact summary" in packet.memory_text
    assert "verbose-full-marker" not in packet.memory_text


def test_candidate_exhaustion_below_budget_and_safety_cap():
    scores = [_score(1), _score(2), _score(3)]
    neurons = {i: _neuron(i, f"fact {i}") for i in range(1, 4)}
    exhausted = assemble_memory_packet(scores[:2], neurons, 3000, 20)
    capped = assemble_memory_packet(scores, neurons, 3000, 2)
    assert exhausted.stop_reason == "candidate_exhausted"
    assert exhausted.estimated_tokens < 3000
    assert [s.neuron_id for s in capped.scores] == [1, 2]
    assert capped.stop_reason == "safety_neuron_cap"


def test_empty_candidates_and_estimator_are_deterministic():
    text = "évidence and ASCII"
    first = estimate_memory_tokens(text, UTF8_BYTES_DIV4_CEIL_V1)
    second = estimate_memory_tokens(text, UTF8_BYTES_DIV4_CEIL_V1)
    packet = assemble_memory_packet([], {}, 3000, 20)
    assert first == second
    assert first == (len(text.encode("utf-8")) + 3) // 4
    assert packet.stop_reason == "no_candidates"
    assert packet.estimated_tokens == packet.chars == packet.utf8_bytes == 0


def test_reference_authority_scope_and_provenance_badges_survive():
    score = _score(1)
    neuron = _neuron(1, "source-backed fact", source_origin="document")
    entry = render_memory_entry(score, neuron, "FQ-ABCDEF")
    assert "[FQ-ABCDEF]" in entry
    assert "[ORG]" in entry
    assert "[REFERENCE]" in entry
    assert "[scope:Projects]" in entry
    assert "[role:corvus]" in entry
    assert "[source:document]" in entry
