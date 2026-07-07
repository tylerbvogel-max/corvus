"""Counterbalanced eval judging (position-bias fix).

The A/B priming eval exposed a judge position bias: all non-tie winners sat
in the last-presented slot regardless of content. The fix judges both the
forward and reversed presentation orders, averages per-answer scores, and
only declares a winner both orderings agree on. These tests cover the pure
reconciliation logic — no LLM.
"""
from app.routers.query import (
    _reconcile_eval_passes, _scores_by_index, _winner_index,
)


def _row(letter: str, **dims) -> dict:
    base = {"answer": letter, "accuracy": 3, "completeness": 3,
            "clarity": 3, "faithfulness": 3, "overall": 3}
    base.update(dims)
    return base


def _pass(scores, winner) -> dict:
    return {"scores": scores, "verdict": "v", "winner": winner,
            "input_tokens": 0, "output_tokens": 0}


# ---- letter -> original-index mapping ----

def test_scores_by_index_forward_and_reversed():
    scores = [_row("A"), _row("B")]
    fwd = _scores_by_index(scores, 2, reversed_order=False)
    assert fwd[0]["answer"] == "A" and fwd[1]["answer"] == "B"
    rev = _scores_by_index(scores, 2, reversed_order=True)
    assert rev[1]["answer"] == "A" and rev[0]["answer"] == "B", \
        "in the reversed pass, letter A is the LAST original slot"


def test_scores_by_index_ignores_garbage_letters():
    out = _scores_by_index([_row("C"), _row(""), _row("AB")], 2, reversed_order=False)
    assert out == {}, "letters outside A..chr(64+n) must be dropped"


def test_winner_index_handles_tie_none_and_case():
    assert _winner_index("A", 2, reversed_order=False) == 0
    assert _winner_index("a", 2, reversed_order=False) == 0
    assert _winner_index("A", 2, reversed_order=True) == 1
    assert _winner_index("tie", 2, reversed_order=False) is None
    assert _winner_index(None, 2, reversed_order=False) is None
    assert _winner_index("Z", 2, reversed_order=False) is None


# ---- reconciliation ----

def test_consensus_winner_survives_both_orderings():
    # Slot 0 wins both passes: letter A forward, letter B reversed.
    fwd = _pass([_row("A", overall=5), _row("B", overall=3)], "A")
    rev = _pass([_row("A", overall=3), _row("B", overall=5)], "B")
    merged, winner, downgraded = _reconcile_eval_passes(2, fwd, rev)
    assert winner == "A" and downgraded is False
    assert merged[0]["overall"] == 5 and merged[1]["overall"] == 3


def test_position_biased_winner_downgraded_to_tie():
    # Judge picks the last-presented answer in BOTH passes -> different
    # underlying slots -> no consensus -> tie, flagged as downgraded.
    fwd = _pass([_row("A"), _row("B")], "B")   # slot 1
    rev = _pass([_row("A"), _row("B")], "B")   # slot 0
    _, winner, downgraded = _reconcile_eval_passes(2, fwd, rev)
    assert winner == "tie" and downgraded is True


def test_one_winner_one_tie_downgrades():
    fwd = _pass([_row("A"), _row("B")], "A")
    rev = _pass([_row("A"), _row("B")], "tie")
    _, winner, downgraded = _reconcile_eval_passes(2, fwd, rev)
    assert winner == "tie" and downgraded is True


def test_both_ties_stay_tie_without_downgrade_note():
    fwd = _pass([_row("A"), _row("B")], "tie")
    rev = _pass([_row("A"), _row("B")], "tie")
    _, winner, downgraded = _reconcile_eval_passes(2, fwd, rev)
    assert winner == "tie" and downgraded is False


def test_scores_average_half_up_across_orderings():
    # Slot 0: forward accuracy 4 (letter A), reversed accuracy 5 (letter B)
    fwd = _pass([_row("A", accuracy=4), _row("B", accuracy=2)], None)
    rev = _pass([_row("A", accuracy=2), _row("B", accuracy=5)], None)
    merged, winner, _ = _reconcile_eval_passes(2, fwd, rev)
    assert merged[0]["accuracy"] == 5, "mean 4.5 rounds half-up to 5"
    assert merged[1]["accuracy"] == 2
    assert winner is None, "no winner reported by either pass -> None (legacy shape)"


def test_single_pass_parse_failure_uses_surviving_pass():
    fwd = _pass([], None)  # unparseable
    rev = _pass([_row("A", overall=4), _row("B", overall=2)], "B")
    merged, winner, downgraded = _reconcile_eval_passes(2, fwd, rev)
    # reversed letters map back: A -> slot 1, B -> slot 0
    assert merged[0]["overall"] == 2 and merged[1]["overall"] == 4
    assert winner == "tie" and downgraded is True, \
        "one-pass winner cannot be confirmed by the failed pass -> tie"


def test_both_passes_unparseable_degrades_to_empty():
    merged, winner, downgraded = _reconcile_eval_passes(2, _pass([], None), _pass([], None))
    assert merged == [] and winner is None and downgraded is False


def test_three_slots_reversed_mapping():
    # n=3 reversed: A->idx2, B->idx1, C->idx0
    rev = _scores_by_index([_row("A"), _row("B"), _row("C")], 3, reversed_order=True)
    assert rev[2]["answer"] == "A" and rev[1]["answer"] == "B" and rev[0]["answer"] == "C"
    assert _winner_index("C", 3, reversed_order=True) == 0
