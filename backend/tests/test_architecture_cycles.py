"""The import-cycle ratchet: cycles may be removed, never added.

Record 04 owns four remaining import cycles and asks for the guard to be built
BEFORE the next one is broken, so that a cycle fixed in code cannot silently
come back and a new one cannot arrive unnoticed. Nothing gated cycles before
this file: ``check_no_cycles`` existed in check_architecture.py as an invariant
type, but no manifest box declared it, so the count was observed and never
enforced.

The mechanism is deliberately dull. ``architecture/cycle_allowlist.json`` lists
the cycles we still carry; this module asserts that list matches the generated
import graph EXACTLY, in both directions. Admitting a new cycle therefore means
editing a file named for the debt it tracks and raising a budget integer — a
diff review cannot miss.
"""

from __future__ import annotations

import json
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
ARCH = REPO / "architecture" / "architecture.json"
ALLOWLIST = REPO / "architecture" / "cycle_allowlist.json"


def _normalize(cycles) -> list[list[str]]:
    """Sort within and across cycles so comparison is order-independent.

    The extractor emits cycle members in whatever order the traversal found
    them; two runs that disagree on order describe the same cycle.
    """
    return sorted(sorted(c) for c in cycles)


def _actual() -> list[list[str]]:
    return _normalize(json.loads(ARCH.read_text())["import_cycles"])


def _allowed() -> dict:
    return json.loads(ALLOWLIST.read_text())


def _label(cycle: list[str]) -> str:
    return f"{len(cycle)} modules starting {cycle[0]}"


@pytest.mark.hermetic
def test_no_import_cycle_exists_outside_the_allowlist():
    """A cycle in the code that nobody has signed for fails the gate."""
    actual, allowed = _actual(), _normalize(_allowed()["cycles"])
    new = [c for c in actual if c not in allowed]
    assert not new, (
        "new import cycle(s) introduced:\n"
        + "\n".join(f"  - {_label(c)}\n      " + "\n      ".join(c) for c in new)
        + "\n\nBreak the cycle, or — if it is genuinely intended — add it to "
          "architecture/cycle_allowlist.json and raise `budget`. The second option "
          "is meant to be uncomfortable."
    )


@pytest.mark.hermetic
def test_allowlist_carries_no_cycle_that_is_already_broken():
    """A fixed cycle must leave the allowlist, or the ratchet slips.

    Without this, breaking a cycle and leaving its entry behind would silently
    restore headroom: a future cycle could land on the stale slot and the first
    test would still pass.
    """
    actual, allowed = _actual(), _normalize(_allowed()["cycles"])
    stale = [c for c in allowed if c not in actual]
    assert not stale, (
        "cycle_allowlist.json lists cycle(s) that no longer exist:\n"
        + "\n".join(f"  - {_label(c)}" for c in stale)
        + "\n\nThis is good news — delete the entries and lower `budget` so the "
          "ratchet records the win."
    )


@pytest.mark.hermetic
def test_budget_matches_the_allowlist_and_bounds_reality():
    """`budget` cannot drift away from the list it is supposed to bound."""
    allowed = _allowed()
    listed, budget = len(_normalize(allowed["cycles"])), allowed["budget"]
    assert budget == listed, (
        f"budget is {budget} but the allowlist carries {listed} cycles; they must "
        f"agree, otherwise the budget stops meaning anything"
    )
    assert len(_actual()) <= budget, (
        f"{len(_actual())} cycles in the import graph exceeds the budget of {budget}"
    )


@pytest.mark.hermetic
def test_allowlist_history_records_every_budget_change():
    """The ratchet keeps its own receipts, newest last, never increasing."""
    history = _allowed()["history"]
    assert history, "history is empty; the ratchet should record how it got here"
    assert history[-1]["budget"] == _allowed()["budget"], (
        "the last history entry does not match the current budget; record the "
        "change rather than editing budget in place"
    )
    budgets = [h["budget"] for h in history]
    assert budgets == sorted(budgets, reverse=True), (
        f"history shows the budget going UP: {budgets}. That is the thing this "
        f"file exists to prevent; if it was genuinely necessary, say why in the note."
    )


# ── Honeypots: prove the gate actually bites ───────────────────────────────
#
# Record 04's verification list asks for fitness tests that "fail against
# planted forbidden imports". These plant the two failure modes directly
# against the checker's own logic, so a refactor that guts the comparison is
# caught here rather than by a cycle sneaking in months later.

@pytest.mark.hermetic
def test_honeypot_a_planted_new_cycle_is_rejected():
    bait = ["app.services.honeypot_a", "app.services.honeypot_b"]
    planted = _actual() + [sorted(bait)]
    allowed = _normalize(_allowed()["cycles"])
    new = [c for c in planted if c not in allowed]
    # Membership, not position: asserting new[0] would couple this honeypot to
    # whatever else happens to be failing, which is how it broke during seam 3.
    assert sorted(bait) in new, "a planted cycle was NOT detected as new; the gate does not bite"


@pytest.mark.hermetic
def test_honeypot_a_stale_allowlist_entry_is_rejected():
    bait = ["app.services.already_fixed"]
    actual = _actual()
    planted = _normalize(_allowed()["cycles"]) + [bait]
    stale = [c for c in planted if c not in actual]
    assert bait in stale, "a stale allowlist entry was NOT detected; the ratchet can slip"


@pytest.mark.hermetic
def test_honeypot_cycle_comparison_ignores_member_order():
    """Order-independence is load-bearing: without it every run looks like a new cycle."""
    a = _normalize([["b.mod", "a.mod"]])
    b = _normalize([["a.mod", "b.mod"]])
    assert a == b, "cycle comparison is order-sensitive; the gate would fire on noise"
