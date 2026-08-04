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


class TestLaneOrderingDeterminism:
    """Rank-fused lanes must not grow new plan-dependent tie surfaces.

    Measured 2026-08-04 (roadmap record mind-recall-warm-state). Both lanes end
    in `ORDER BY score DESC LIMIT :top_n` with NO tie-breaker, and
    scoring_engine.calc_rrf turns each lane's row ORDER into the rank it fuses
    (Python's sort is stable, so tied lane scores inherit whatever order
    Postgres returned). On the corvus-mind corpus the entity lane comes back 43
    of 50 rows tied, so that order is doing real work and nothing defines it.

    What this cost, concretely: replaying one 66-command session against a
    byte-frozen corpus, a GroupAggregate plan and a HashAggregate plan for the
    same entity-lane query emitted the tied rows differently, moving neuron
    1541 from lane position 35 to 21. Its fused score went 1.3287 -> 1.3923 and
    it crossed the top-2 delivery boundary — a different lesson injected, from
    an identical corpus, because the planner's statistics had changed.

    This is a CONTAINMENT tripwire, not a fix: the two lanes below are the
    known surfaces and the record deliberately left repairing them as a
    separate decision with its own evidence. The assertion is equality, not
    subset, so both directions are loud — a new unbroken-tie LIMIT entering the
    rank-fused path fails, and so does silently repairing one of these two
    without recording that the measurements above no longer describe the system.
    """

    # (function, ORDER BY key text) for every LIMIT-bounded statement whose row
    # order becomes an RRF rank. A tie-broken lane would read "score DESC, id".
    KNOWN_UNBROKEN_TIES = {
        ("keyword_lane", "score DESC"),
        ("entity_lane", "score DESC"),
    }

    def test_rank_fused_lanes_have_no_new_unbroken_ties(self):
        import inspect
        import re

        from app.services import recall_lanes

        pattern = re.compile(
            r"ORDER\s+BY\s+(?P<keys>[^\n]+?)\s*\n\s*LIMIT\b", re.IGNORECASE)
        found = set()
        for name, fn in vars(recall_lanes).items():
            if not inspect.isfunction(fn) or fn.__module__ != recall_lanes.__name__:
                continue
            for match in pattern.finditer(inspect.getsource(fn)):
                found.add((name, " ".join(match.group("keys").split())))

        assert found == self.KNOWN_UNBROKEN_TIES, (
            "the rank-fused lane surface changed.\n"
            f"  found:    {sorted(found)}\n"
            f"  expected: {sorted(self.KNOWN_UNBROKEN_TIES)}\n"
            "A LIMIT over a non-unique ORDER BY hands calc_rrf a rank that the "
            "query planner chose, not one the data determined. If you ADDED a "
            "lane, give it a tie-breaker before it reaches fusion. If you FIXED "
            "one of these, update this set and mind-recall-warm-state's "
            "receipts — they measure a system that no longer exists."
        )
