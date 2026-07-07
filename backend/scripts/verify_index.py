"""Bit-exact equivalence check: NeuronIndex accessors vs. the live DB queries.

Proves, against the REAL graph, that serving scoring inputs from the in-memory
NeuronIndex reproduces the DB path exactly when the index is fresh:
  - stats accessors (burst / fire-stats / dept-totals) == the _fetch_* DB queries
  - candidate hydration (keyword_hits / freshness / metadata) == _load_candidates_by_ids
  - full score_candidates (index ON) == (index OFF): identical top-K, combined
    scores within float noise
Also prints the prefilter+load+score latency with the index off vs on.

The hermetic accessor-semantics tests live in tests/test_neuron_index.py; this
script is the live-graph counterpart (re-run it after graph/scoring changes).

Usage:
    cd backend && TENANT_ID=corvus-aero PYTHONPATH=. python scripts/verify_index.py
Exits non-zero if any equivalence check fails.
"""

import asyncio
import sys
import time

from app.config import settings
from app.database import async_session
from app.services.embedding_service import embed_text
from app.services.executor import _load_candidates_by_ids
from app.services.neuron_index import ensure_index_loaded, get_index, invalidate_index
from app.services.neuron_service import (
    _fetch_burst_counts, _fetch_dept_fire_totals, _fetch_neuron_fire_stats,
    get_system_state, score_candidates,
)
from app.services.semantic_prefilter import ensure_cache_loaded, semantic_prefilter

QUERIES = (
    "What FOD prevention and tool control procedures apply to aerospace manufacturing?",
    "How must unallowable costs be treated under FAR cost principles?",
    "What ITAR requirements apply when shipping technical data abroad?",
    "How do we prepare for an AS9100D surveillance audit?",
)
KW = ("FOD", "tool control", "cost", "audit", "ITAR")


async def main() -> int:
    async with async_session() as db:
        await ensure_cache_loaded(db)
        invalidate_index()
        await ensure_index_loaded(db)  # fresh build
        idx = get_index()
        ss = await get_system_state(db)
        tq = ss.total_queries
        window = max(0, tq - settings.burst_window_queries)
        embed_text("warm")
        stats_ok = cand_ok = score_ok = True

        for q in QUERIES:
            qe = embed_text(q)
            sem = await semantic_prefilter(db, qe, top_n_override=settings.semantic_prefilter_top_n)
            nsims = {e: s for e, et, s in sem if et == "neuron"}
            ids = list(nsims.keys())

            # 1) stats accessors vs DB
            b_db = await _fetch_burst_counts(db, ids, window)
            f_db, l_db = await _fetch_neuron_fire_stats(db, ids)
            stats_ok = stats_ok and (b_db == idx.burst_counts(ids, window))
            fx, lx = idx.fire_stats(ids)
            stats_ok = stats_ok and (f_db == fx) and (l_db == lx)

            # 2) candidate hydration: index vs DB (unrestricted requester)
            settings.neuron_index_enabled = False
            c_db = await _load_candidates_by_ids(db, ids, KW, None)
            settings.neuron_index_enabled = True
            c_ix = await _load_candidates_by_ids(db, ids, KW, None)
            dbm = {c.id: c for c in c_db}
            ixm = {c.id: c for c in c_ix}
            depts = list({c.department for c in c_db if c.department})
            stats_ok = stats_ok and (await _fetch_dept_fire_totals(db, c_db) == idx.dept_totals(depts))
            cand_ok = cand_ok and set(dbm) == set(ixm) \
                and all(dbm[i].keyword_hits == ixm[i].keyword_hits for i in dbm) \
                and all(abs((dbm[i].freshness_days or 0) - (ixm[i].freshness_days or 0)) < 0.01 for i in dbm)

            # 3) full score: index off vs on
            settings.neuron_index_enabled = False
            s_off = await score_candidates(db, c_db, tq, KW, [], [], query_embedding=qe, precomputed_similarities=nsims)
            settings.neuron_index_enabled = True
            s_on = await score_candidates(db, c_ix, tq, KW, [], [], query_embedding=qe, precomputed_similarities=nsims)
            off_by = {s.neuron_id: s.combined for s in s_off}
            on_by = {s.neuron_id: s.combined for s in s_on}
            mx = max((abs(off_by[i] - on_by[i]) for i in off_by), default=0.0)
            tk_off = {s.neuron_id for s in s_off[:60]}
            tk_on = {s.neuron_id for s in s_on[:60]}
            score_ok = score_ok and set(off_by) == set(on_by) and mx < 1e-3 and (tk_off == tk_on)
            print(f"  q: max_combined_diff={mx:.2e} topK_same={tk_off == tk_on} overlap={len(tk_off & tk_on)}/60")

        await _print_timing(db, tq)
        print(f"\nSTATS exact: {stats_ok}  |  CANDIDATES exact: {cand_ok}  |  SCORE equivalent: {score_ok}")
        return 0 if (stats_ok and cand_ok and score_ok) else 1


async def _print_timing(db, tq: int) -> None:
    """Report prefilter+load+score latency with the index off vs on."""
    cap = settings.semantic_prefilter_top_n
    qe = embed_text(QUERIES[0])

    async def run() -> None:
        sem = await semantic_prefilter(db, qe, top_n_override=cap)
        ns = {e: s for e, et, s in sem if et == "neuron"}
        c = await _load_candidates_by_ids(db, list(ns.keys()), KW, None)
        await score_candidates(db, c, tq, KW, [], [], query_embedding=qe, precomputed_similarities=ns)

    for lbl, flag in (("index OFF", False), ("index ON", True)):
        settings.neuron_index_enabled = flag
        if flag:
            await ensure_index_loaded(db)
        best = 1e9
        for _ in range(4):
            a = time.perf_counter()
            await run()
            best = min(best, (time.perf_counter() - a) * 1000)
        print(f"{lbl}: prefilter+load+score = {best:.1f} ms")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
