"""Accessor-semantics tests for the NeuronIndex (the materialized scoring projection).

Proves each accessor reproduces its DB query's semantics — in particular the
distinct-query-id keying (an offset collision must NOT collapse two queries), the
burst window, per-dept distinct counts, the avg_utility 0.0->0.5 coercion, the
is_active filter, deterministic id-ordered output, and the incremental firing hook.
Hermetic: loads a controlled graph into the index singleton; no DB.

Real-graph bit-exact equivalence (index score == DB score, 60/60 top-K) is covered
by scripts/verify_index.py against the live graph.
"""

import pytest

from app.services import neuron_index
from app.services.neuron_index import get_index


META = [
    {"id": 1, "label": "FOD prevention", "summary": "tool control", "department": "Mfg",
     "role_key": "eng", "avg_utility": 0.7, "invocations": 3, "created_at_query_count": 10,
     "authority_level": "regulatory", "centrality": 0.5, "is_active": True, "freshness_days": 100.0},
    {"id": 2, "label": "cost allowability", "summary": None, "department": "Finance",
     "role_key": None, "avg_utility": 0.0, "invocations": 0, "created_at_query_count": 0,
     "authority_level": None, "centrality": 0.0, "is_active": True, "freshness_days": None},
    {"id": 3, "label": "inactive node", "summary": "x", "department": "Mfg",
     "role_key": None, "avg_utility": 0.5, "invocations": 0, "created_at_query_count": 0,
     "authority_level": None, "centrality": 0.0, "is_active": False, "freshness_days": 5.0},
]
# (neuron_id, query_id, offset, department) — note two DIFFERENT query_ids share offset 105
FIRINGS = [
    (1, 100, 100, "Mfg"),
    (1, 101, 105, "Mfg"),
    (1, 102, 105, "Mfg"),
    (2, 100, 100, "Finance"),
]


@pytest.fixture(autouse=True)
def _loaded_index():
    get_index().load(META, FIRINGS)
    yield
    neuron_index.invalidate_index()


def test_fire_stats_distinct_by_query_id_not_offset():
    """Distinct count keys on query_id — an offset collision must not collapse queries."""
    fires, last = get_index().fire_stats([1, 2, 3])
    assert fires[1] == 3           # query_ids {100,101,102} — NOT 2 (offsets {100,105})
    assert last[1] == 105
    assert fires[2] == 1
    assert 3 not in fires          # neuron 3 never fired


def test_burst_counts_window():
    """Burst = count of firing rows (with multiplicity) at/after the window."""
    idx = get_index()
    assert idx.burst_counts([1], 104) == {1: 2}   # offsets 105,105 >= 104
    assert idx.burst_counts([1], 106) == {}        # none >= 106
    assert idx.burst_counts([1], 100) == {1: 3}    # 100,105,105


def test_dept_totals_distinct_query_ids():
    idx = get_index()
    assert idx.dept_totals(["Mfg"]) == {"Mfg": 3}       # queries {100,101,102}
    assert idx.dept_totals(["Finance"]) == {"Finance": 1}
    assert idx.dept_totals(["Nope"]) == {}


def test_candidates_semantics():
    """keyword_hits (LIKE), avg_utility 0.0->0.5, is_active filter, id-sorted output."""
    cands = get_index().candidates([2, 1, 3], ["tool", "cost"])
    assert [c.id for c in cands] == [1, 2]              # sorted by id; 3 filtered (inactive)
    c1 = next(c for c in cands if c.id == 1)
    c2 = next(c for c in cands if c.id == 2)
    assert c1.keyword_hits == 1                          # "tool" in summary "tool control"
    assert c2.keyword_hits == 1                          # "cost" in label "cost allowability"
    assert c2.avg_utility == 0.5                         # 0.0 coerced to 0.5 (matches DB `or 0.5`)
    assert c1.avg_utility == 0.7
    assert c2.freshness_days is None                     # no freshness stamp
    assert c1.freshness_days is not None and c1.freshness_days >= 100.0


def test_on_firing_incremental():
    """Incremental firing updates distinct count, last offset, and invocations."""
    idx = get_index()
    idx.on_firing(1, 103, 110)      # new query_id 103, offset 110
    fires, last = idx.fire_stats([1])
    assert fires[1] == 4            # {100,101,102,103}
    assert last[1] == 110
    assert idx.burst_counts([1], 108) == {1: 1}          # only offset 110
    assert idx.dept_totals(["Mfg"]) == {"Mfg": 4}
    cands = idx.candidates([1], [])
    assert cands[0].invocations == 4                     # 3 + 1
