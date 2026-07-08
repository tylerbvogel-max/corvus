"""Layer-2 citation grounding: claim-vs-source relevance scoring (log-only).

Hermetic — the embedder is faked with keyword vectors so cosine is exactly
1.0 for on-topic pairs and 0.0 for off-topic pairs.
"""
import asyncio
from unittest.mock import patch

import pytest

import app.services.citation_relevance as cr
import app.services.executor as ex
from app.services.citation_hopping import HopMap


def _fake_embed_batch(texts):
    """Keyword embedder: 'torque' -> x-axis, everything else -> y-axis."""
    return [[1.0, 0.0] if "torque" in t.lower() else [0.0, 1.0] for t in texts]


class _Neuron:
    def __init__(self, label, summary="", content=""):
        self.label = label
        self.summary = summary
        self.content = content


def _hop_map():
    return HopMap(
        token_by_neuron={1: "FQ-AAA111", 2: "FQ-BBB222"},
        neuron_by_token={"FQ-AAA111": 1, "FQ-BBB222": 2},
    )


_NEURONS = {
    1: _Neuron("Torque verification", content="Torque wrenches are verified daily. Torque values are recorded."),
    2: _Neuron("Supplier onboarding", content="New suppliers complete an audit. Onboarding takes two weeks."),
}


# ── Sentence windows ─────────────────────────────────────────────────────

def test_sentence_windows_short_text_single_window():
    assert cr._sentence_windows("One sentence only.") == ["One sentence only."]


def test_sentence_windows_stride_and_bound():
    text = " ".join(f"Sentence number {i}." for i in range(30))
    windows = cr._sentence_windows(text)
    assert len(windows) == cr._MAX_WINDOWS_PER_SOURCE
    assert "Sentence number 0. Sentence number 1." == windows[0]


# ── Scoring ──────────────────────────────────────────────────────────────

def _score(answer, max_claims=10):
    with patch.object(cr, "embed_batch", _fake_embed_batch):
        return cr.score_citation_relevance(answer, _hop_map(), _NEURONS, [], max_claims)


def test_on_topic_citation_scores_high():
    out = _score("Torque wrenches must be verified before use [FQ-AAA111].")
    assert out["checked"] == 1
    assert out["claims"][0]["score"] == pytest.approx(1.0)


def test_wrong_source_citation_scores_low():
    """The failure this layer exists to catch: valid key, irrelevant source."""
    out = _score("Torque wrenches must be verified before use [FQ-BBB222].")
    assert out["claims"][0]["score"] == pytest.approx(0.0)
    assert out["min"] == pytest.approx(0.0)


def test_multi_token_claim_takes_max_over_sources():
    out = _score("Torque wrenches must be verified before use [FQ-AAA111][FQ-BBB222].")
    assert out["claims"][0]["score"] == pytest.approx(1.0), \
        "max-pooling: one relevant source among the cited set suffices"


def test_no_citations_returns_none():
    assert _score("No citations in this answer at all.") is None


def test_unknown_token_scores_none_but_others_still_scored():
    # FQ-DDD444 is hex-valid (parses as a citation token) but absent from the
    # hop map — e.g. a fabricated-but-well-formed key this layer can't resolve.
    out = _score(
        "Torque wrenches are verified daily [FQ-AAA111]. "
        "Suppliers are audited on torque too [FQ-DDD444]."
    )
    by_token = {tuple(c["tokens"]): c["score"] for c in out["claims"]}
    assert by_token[("FQ-AAA111",)] == pytest.approx(1.0)
    assert by_token[("FQ-DDD444",)] is None, "unresolvable key -> no score, not a crash"
    assert out["scored"] == 1


# ── Guards integration ───────────────────────────────────────────────────

class _GuardCtx:
    system_prompt = ""
    hop_map = _hop_map()
    neuron_map = _NEURONS
    resolved_regulations = []


@pytest.mark.asyncio
async def test_guards_attach_relevance_payload(monkeypatch):
    monkeypatch.setattr(ex.settings, "citation_relevance_enabled", True)

    async def fake_clean(_ctx, _msg, text):
        return text, 0
    monkeypatch.setattr(ex, "_clean_answer_citations", fake_clean)
    with patch.object(cr, "embed_batch", _fake_embed_batch):
        cleaned, fabricated, ungrounded, relevance = await ex._slot_grounding_guards(
            _GuardCtx(), "q", "Torque wrenches are verified daily [FQ-AAA111].")
    assert relevance["checked"] == 1
    assert relevance["claims"][0]["score"] == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_guards_skip_relevance_when_disabled(monkeypatch):
    monkeypatch.setattr(ex.settings, "citation_relevance_enabled", False)

    async def fake_clean(_ctx, _msg, text):
        return text, 0
    monkeypatch.setattr(ex, "_clean_answer_citations", fake_clean)
    *_rest, relevance = await ex._slot_grounding_guards(
        _GuardCtx(), "q", "Torque [FQ-AAA111].")
    assert relevance is None
