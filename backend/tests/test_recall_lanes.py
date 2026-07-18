"""Hybrid-recall lane units (mind-hybrid-recall): query-entity extraction,
entity normalization, lane SQL safety, and N-lane reciprocal-rank fusion."""

import pytest

from app.services.recall_lanes import (
    entity_lane,
    extract_query_entities,
    normalize_entities,
)
from app.services.scoring_engine import calc_hybrid_relevance, calc_rrf


class TestNormalizeEntities:
    def test_lowercases_strips_dedupes(self):
        out = normalize_entities([" Becoming Nicole ", "becoming nicole", "Rex"])
        assert out == ["becoming nicole", "rex"]

    def test_bounds_and_drops_short(self):
        out = normalize_entities(["a"] + [f"ent{i}" for i in range(30)])
        assert len(out) == 12
        assert "a" not in out

    def test_none_and_empty(self):
        assert normalize_entities(None) == []
        assert normalize_entities([]) == []


class TestExtractQueryEntities:
    def test_quoted_title(self):
        ents = extract_query_entities('Did Melanie read "Becoming Nicole"?')
        assert "becoming nicole" in ents
        assert "melanie" in ents

    def test_sentence_initial_grammar_excluded(self):
        ents = extract_query_entities("What book did Caroline recommend?")
        assert ents == ["caroline"]

    def test_capitalized_run_joins(self):
        ents = extract_query_entities("When did Caroline visit New York City?")
        assert "new york city" in ents
        assert "caroline" in ents

    def test_no_entities(self):
        assert extract_query_entities("what happened next?") == []


class _EmptyResult:
    def all(self):
        return []


class _SqlCaptureDb:
    statement = None

    async def execute(self, statement, _params):
        self.statement = str(statement)
        return _EmptyResult()


@pytest.mark.asyncio
async def test_entity_lane_excludes_legacy_scalar_json():
    """Production has legacy scalar JSON values in ``entities``. PostgreSQL's
    jsonb_array_elements_text raises on those unless the lane filters by JSON
    type before invoking the lateral function."""
    db = _SqlCaptureDb()
    assert await entity_lane(db, ["Claude CLI"]) == {}
    assert "jsonb_typeof(entities) = 'array'" in db.statement


class TestCalcRrf:
    def test_single_lane_hit_earns_partial_credit(self):
        # Neuron 2 only in the entity lane must still surface.
        fused = calc_rrf([{1: 0.9, 3: 0.5}, {2: 1.0}], k=60)
        assert set(fused) == {1, 2, 3}
        assert fused[2] > 0

    def test_multi_lane_winner_beats_single_lane(self):
        # Neuron 2 ranks high in both lanes; neuron 1 tops one lane but is
        # absent from a 2-deep second lane (worst rank 3) — fusion favors 2.
        fused = calc_rrf([{1: 0.9, 2: 0.8}, {2: 1.0, 3: 0.9}], k=60)
        assert fused[2] > fused[1]

    def test_empty_lanes(self):
        assert calc_rrf([], k=60) == {}
        assert calc_rrf([{}, {}], k=60) == {}

    def test_normalized_to_unit_interval(self):
        fused = calc_rrf([{1: 5.0, 2: 1.0}, {1: 3.0}], k=60)
        assert max(fused.values()) == 1.0
        assert all(0.0 <= v <= 1.0 for v in fused.values())

    def test_two_lane_wrapper_unchanged(self):
        kw, sem = {1: 0.2, 2: 0.9}, {1: 0.8, 2: 0.1}
        assert calc_hybrid_relevance(kw, sem, k=60) == calc_rrf([kw, sem], k=60)
