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
    """Every rank-fused lane must ORDER BY a TOTAL order, not a partial one.

    Measured 2026-08-04 (roadmap record mind-recall-warm-state). These lanes
    end in `LIMIT :top_n`, and scoring_engine.calc_rrf turns each lane's row
    ORDER into the rank it fuses — Python's sort is stable, so tied lane scores
    inherit whatever order Postgres emitted. On the corvus-mind corpus the
    entity lane comes back 43 of 50 rows tied. `ORDER BY score DESC` alone over
    that is a partial order, which handed the query planner two decisions it
    has no business making: which rows survive the LIMIT, and what order they
    arrive in.

    What it cost, concretely: replaying one 66-command session against a
    byte-frozen corpus, a GroupAggregate plan and a HashAggregate plan for the
    same entity-lane query emitted the tied rows differently, moving neuron
    1541 from lane position 35 to 21. Its fused score went 1.3287 -> 1.3923 and
    it crossed the top-2 delivery boundary — a different lesson injected, from
    an identical corpus, because the planner's statistics had changed.

    The repair was chosen by measurement, not by taste. Two candidates were
    replayed head to head under a forced plan change: a SQL tie-breaker, and
    competition ranking in calc_rrf so tied scores share a rank. Only the SQL
    tie-breaker is plan-invariant. Competition ranking fixes the order WITHIN
    the returned set but cannot fix WHICH rows the LIMIT returned, and half the
    measured instability was membership — of 20 queries that reordered under a
    forced plan, only 10 kept the same rows.

    So the invariant asserted here is the general one rather than a list of
    known offenders: any LIMIT-bounded statement in this module whose order
    reaches fusion must break ties on the primary key. A new lane that forgets
    fails, without anyone having to remember to update an allowlist.
    """

    def test_every_rank_fused_lane_orders_by_a_total_order(self):
        import inspect
        import re

        from app.services import recall_lanes

        pattern = re.compile(
            r"ORDER\s+BY\s+(?P<keys>[^\n]+?)\s*\n\s*LIMIT\b", re.IGNORECASE)
        # The trailing key must be the primary key — `id` or `<alias>.id`.
        # Nothing else in these statements is guaranteed unique.
        tie_broken = re.compile(r",\s*(?:[A-Za-z_]\w*\.)?id\s*$", re.IGNORECASE)

        partial_orders = {}
        checked = 0
        for name, fn in vars(recall_lanes).items():
            if not inspect.isfunction(fn) or fn.__module__ != recall_lanes.__name__:
                continue
            for match in pattern.finditer(inspect.getsource(fn)):
                keys = " ".join(match.group("keys").split())
                checked += 1
                if not tie_broken.search(keys):
                    partial_orders[name] = keys

        assert checked >= 2, (
            f"expected at least the keyword and entity lanes, found {checked} "
            "LIMIT-bounded ORDER BY statements — the pattern stopped matching "
            "and this test has gone vacuous"
        )
        assert not partial_orders, (
            f"rank-fused lane(s) ordering by a PARTIAL order: {partial_orders}. "
            "A LIMIT over a non-unique ORDER BY hands calc_rrf a rank the query "
            "planner chose rather than one the data determined, and it decides "
            "the LIMIT cut too. End the ORDER BY with the primary key "
            "(`, id` or `, <alias>.id`). See mind-recall-warm-state."
        )
