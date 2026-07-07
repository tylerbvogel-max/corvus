"""Cheap recall mode (plat-cheap-recall) — hermetic tests.

Covers: keyword tokenizer, neighbor-vote tally, pipeline stage selection, and
cheap stage output shape. No DB, no LLM — the embed/vote helpers are patched.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.scoring_engine import extract_keywords
from app.services.executor import _tally_neighbor_votes
from app.services.pipeline.state import PipelineState
from app.services.pipeline.stages import CheapClassifyStage, build_default_pipeline


class _Ctx:
    db = None


def _state(msg: str = "What torque spec applies to AN6 bolts?") -> PipelineState:
    return PipelineState(
        user_message=msg, effective_top_k=10, effective_pool=100,
        effective_budget=2000,
    )


# ── Keyword tokenizer ────────────────────────────────────────────────

def test_extract_keywords_filters_stopwords():
    kws = extract_keywords("What are the requirements for ITAR compliance?")
    assert "itar" in kws
    assert "compliance" in kws
    assert "what" not in kws
    assert "requirements" not in kws  # domain stop word
    assert "the" not in kws


def test_extract_keywords_dedupes_and_bounds():
    kws = extract_keywords("torque torque torque " + " ".join(f"kw{i}" for i in range(20)))
    assert kws.count("torque") == 1
    assert len(kws) <= 8


def test_extract_keywords_strips_punctuation():
    kws = extract_keywords("Explain FAR 52.219-9, please. (subcontracting!)")
    assert "far" in kws
    assert "subcontracting" in kws
    assert "please" not in kws


# ── Neighbor-vote tally ──────────────────────────────────────────────

def test_neighbor_vote_majority_region_wins():
    hits = [(1, 0.9), (2, 0.8), (3, 0.7)]
    rows = [(1, "Engineering", "mech_eng"), (2, "Engineering", "mech_eng"), (3, "Finance", None)]
    regions, roles = _tally_neighbor_votes(hits, rows)
    assert regions[0] == "Engineering"
    assert "Finance" in regions  # 0.7/2.4 = 29% > 20% share
    assert roles == ["mech_eng"]


def test_neighbor_vote_low_share_tag_dropped():
    hits = [(1, 0.9), (2, 0.9), (3, 0.9), (4, 0.9), (5, 0.9), (6, 0.1)]
    rows = [(i, "Engineering", None) for i in range(1, 6)] + [(6, "HR", None)]
    regions, _roles = _tally_neighbor_votes(hits, rows)
    assert regions == ["Engineering"]  # HR at 0.1/4.6 ~ 2% share


def test_neighbor_vote_empty():
    assert _tally_neighbor_votes([], []) == ([], [])


def test_neighbor_vote_ignores_null_tags():
    hits = [(1, 0.9)]
    rows = [(1, None, None)]
    regions, roles = _tally_neighbor_votes(hits, rows)
    assert regions == [] and roles == []


# ── Pipeline mode selection ──────────────────────────────────────────

def test_pipeline_uses_cheap_classify_stage():
    cheap = build_default_pipeline("cheap")
    assert isinstance(cheap[1], CheapClassifyStage)
    assert len(cheap) == 9


def test_pipeline_rejects_unknown_mode():
    with pytest.raises(AssertionError):
        build_default_pipeline("turbo")


# ── Cheap stage output shape ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_cheap_stage_zero_cost_and_tags():
    with patch(
        "app.services.executor._embed_query_async",
        new=AsyncMock(return_value=[0.1] * 384),
    ), patch(
        "app.services.executor._neighbor_vote_classify",
        new=AsyncMock(return_value=(["Engineering"], ["mech_eng"], 0.72)),
    ):
        state = await CheapClassifyStage().run(_state(), _Ctx())

    assert state.classify_result["cost_usd"] == 0.0
    assert state.classify_result["input_tokens"] == 0
    assert state.classify_result["recall_mode"] == "cheap"
    assert state.classify_result["neighbor_top_similarity"] == 0.72
    assert state.departments == ["Engineering"]
    assert state.role_keys == ["mech_eng"]
    assert state.intent == "general_query"
    assert "torque" in state.keywords
    assert state.query_embedding is not None


@pytest.mark.asyncio
async def test_cheap_stage_survives_embedding_failure():
    with patch(
        "app.services.executor._embed_query_async",
        new=AsyncMock(side_effect=RuntimeError("model load failed")),
    ):
        state = await CheapClassifyStage().run(_state(), _Ctx())

    assert state.query_embedding is None
    assert state.departments == []
    assert len(state.keywords) > 0  # keyword-only recall still works
