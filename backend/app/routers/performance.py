"""GET /admin/performance — Pure SQL analytics, no LLM invocation."""

import math
from decimal import Decimal
from types import MappingProxyType
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
import numpy as np
from scipy import stats as sp_stats

from app.database import get_db
from app.services.llm_provider import MODEL_REGISTRY

# Minimum sample size before a mode's aggregate stats are published to the UI.
# Below this, point estimates are too unstable to be useful evidence.
MIN_MODE_N = 10


def _num(val):
    """Convert Decimal/None to float for JSON serialization and arithmetic."""
    if val is None:
        return 0
    if isinstance(val, Decimal):
        return float(val)
    return val


router = APIRouter(prefix="/admin", tags=["admin"])

# Per-million-token prices sourced from MODEL_REGISTRY (single source of truth
# shared with llm_provider._estimate_cost_anthropic and the backfill script).
PRICING = MappingProxyType({
    name: {"input": MODEL_REGISTRY[name].input_price, "output": MODEL_REGISTRY[name].output_price}
    for name in ("haiku", "sonnet", "opus")
})


def _cohens_d(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return 0.0
    pooled = np.sqrt(((na - 1) * a.std(ddof=1)**2 + (nb - 1) * b.std(ddof=1)**2) / (na + nb - 2))
    return float((a.mean() - b.mean()) / pooled) if pooled > 0 else 0.0


def _effect_label(d: float) -> str:
    ad = abs(d)
    if ad < 0.2: return "negligible"
    if ad < 0.5: return "small"
    if ad < 0.8: return "medium"
    return "large"


def _power_n(d: float) -> int:
    """Sample size per group needed for 80% power at alpha=0.05."""
    if abs(d) < 0.01:
        return 99999
    from scipy.stats import norm
    z_a = norm.ppf(0.975)
    z_b = norm.ppf(0.80)
    return math.ceil(((z_a + z_b) / abs(d)) ** 2)


async def _get_scores(db: AsyncSession, mode: str, col: str = "overall") -> list[float]:
    rows = (await db.execute(text(
        f"SELECT {col} FROM eval_scores WHERE answer_mode = :m"
    ), {"m": mode})).fetchall()
    return [float(r[0]) for r in rows]


async def _cost_summary(db: AsyncSession) -> dict:
    row = (await db.execute(text("""
        SELECT
            COUNT(*) as n,
            COALESCE(SUM(cost_usd), 0) as total_cost,
            COALESCE(AVG(cost_usd), 0) as avg_cost,
            MIN(cost_usd) as min_cost,
            MAX(cost_usd) as max_cost,
            COALESCE(SUM(classify_input_tokens), 0) as cls_in,
            COALESCE(SUM(classify_output_tokens), 0) as cls_out,
            COALESCE(SUM(execute_input_tokens), 0) as exe_in,
            COALESCE(SUM(execute_output_tokens), 0) as exe_out,
            COALESCE(SUM(eval_input_tokens), 0) as eval_in,
            COALESCE(SUM(eval_output_tokens), 0) as eval_out
        FROM queries
    """))).fetchone()

    r = [_num(v) for v in row]
    return {
        "total_queries": r[0],
        "total_cost": round(r[1], 4),
        "avg_cost": round(r[2], 4),
        "min_cost": round(r[3], 4),
        "max_cost": round(r[4], 4),
        "classify_tokens": {"input": r[5], "output": r[6]},
        "execute_tokens": {"input": r[7], "output": r[8]},
        "eval_tokens": {"input": r[9], "output": r[10]},
        "total_input_tokens": r[5] + r[7] + r[9],
        "total_output_tokens": r[6] + r[8] + r[10],
    }


async def _cost_by_activity(db: AsyncSession) -> dict:
    """Break the total LLM spend down by activity bucket.

    v1 buckets come from existing tables:
      - query_pipeline : SUM(queries.cost_usd) — user-facing queries end-to-end
      - autopilot      : SUM(autopilot_runs.cost_usd) — background tick cost
      - untracked      : activities whose per-call LLM cost is not aggregated
                         today: agent runs (actions.kind LIKE 'agent.%'),
                         document ingestion, eval runs outside queries,
                         condense-context Haiku calls from the hero page.

    The "untracked" bucket is the gap flagged in the cost-attribution
    roadmap item (master-corvus gov-cost-attribution). Per-call cost
    tagging would eliminate it.
    """
    query_cost_row = (await db.execute(text(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM queries"
    ))).fetchone()
    autopilot_cost_row = (await db.execute(text(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM autopilot_runs WHERE status = 'completed'"
    ))).fetchone()

    query_cost = float(query_cost_row[0] or 0) if query_cost_row else 0.0
    autopilot_cost = float(autopilot_cost_row[0] or 0) if autopilot_cost_row else 0.0
    buckets = [
        {
            "key": "query_pipeline",
            "label": "User queries",
            "total_cost": round(query_cost, 4),
            "source_table": "queries.cost_usd",
            "description": "End-to-end cost for user-facing questions (classify + execute + eval + refine).",
        },
        {
            "key": "autopilot",
            "label": "Autopilot (background maintenance)",
            "total_cost": round(autopilot_cost, 4),
            "source_table": "autopilot_runs.cost_usd",
            "description": "Cost of each autopilot tick (completed runs only).",
        },
    ]
    total_tracked = sum(b["total_cost"] for b in buckets)
    return {
        "buckets": buckets,
        "total_tracked": round(total_tracked, 4),
        "gap_note": (
            "Agent runs, document ingestion, condense-context summarization, "
            "and standalone eval runs are NOT currently aggregated here. "
            "Per-call activity tagging (tracked on master-corvus as "
            "gov-cost-attribution) would make these visible."
        ),
    }


async def _cost_modeling(db: AsyncSession) -> dict:
    row2 = (await db.execute(text("""
        SELECT
            AVG(classify_input_tokens) as avg_cls_in,
            AVG(classify_output_tokens) as avg_cls_out,
            AVG(execute_input_tokens) as avg_exe_in,
            AVG(execute_output_tokens) as avg_exe_out
        FROM queries WHERE execute_input_tokens > 0
    """))).fetchone()

    avg_cls_in = float(row2[0] or 0)
    avg_cls_out = float(row2[1] or 0)
    avg_exe_in = float(row2[2] or 0)
    avg_exe_out = float(row2[3] or 0)

    h = PRICING["haiku"]
    s = PRICING["sonnet"]
    o = PRICING["opus"]

    haiku_neuron_cost = (
        (avg_cls_in * h["input"] + avg_cls_out * h["output"]) / 1_000_000 +
        (avg_exe_in * h["input"] + avg_exe_out * h["output"]) / 1_000_000
    )
    sonnet_raw_cost = (avg_cls_in * s["input"] + avg_exe_out * s["output"]) / 1_000_000
    opus_raw_cost = (avg_cls_in * o["input"] + avg_exe_out * o["output"]) / 1_000_000

    return {
        "avg_tokens": {
            "classify_input": round(avg_cls_in),
            "classify_output": round(avg_cls_out),
            "execute_input": round(avg_exe_in),
            "execute_output": round(avg_exe_out),
        },
        "per_query_cost": {
            "haiku_neuron": round(haiku_neuron_cost, 4),
            "sonnet_raw": round(sonnet_raw_cost, 4),
            "opus_raw": round(opus_raw_cost, 4),
        },
        "savings_vs_opus": round((1 - haiku_neuron_cost / opus_raw_cost) * 100, 1) if opus_raw_cost > 0 else 0,
        "savings_vs_sonnet": round((1 - haiku_neuron_cost / sonnet_raw_cost) * 100, 1) if sonnet_raw_cost > 0 else 0,
        "projected_monthly_100k": {
            "haiku_neuron": round(haiku_neuron_cost * 100_000, 2),
            "sonnet_raw": round(sonnet_raw_cost * 100_000, 2),
            "opus_raw": round(opus_raw_cost * 100_000, 2),
        },
        "annual_savings_vs_opus_100k": round((opus_raw_cost - haiku_neuron_cost) * 100_000 * 12, 2),
        "annual_savings_vs_sonnet_100k": round((sonnet_raw_cost - haiku_neuron_cost) * 100_000 * 12, 2),
    }


async def _quality_by_mode(db: AsyncSession) -> dict:
    # Gate on HAVING COUNT(*) >= MIN_MODE_N so 1-2 sample exploratory modes
    # (gemini/groq/or-*) do not appear as headline evidence alongside modes
    # with hundreds of evaluations.
    rows = (await db.execute(text("""
        SELECT answer_mode, COUNT(*) as n,
            ROUND(AVG(accuracy)::numeric, 2) as acc,
            ROUND(AVG(completeness)::numeric, 2) as comp,
            ROUND(AVG(clarity)::numeric, 2) as clar,
            ROUND(AVG(faithfulness)::numeric, 2) as faith,
            ROUND(AVG(overall)::numeric, 2) as overall
        FROM eval_scores GROUP BY answer_mode
        HAVING COUNT(*) >= :min_n
        ORDER BY overall DESC
    """), {"min_n": MIN_MODE_N})).fetchall()

    quality_by_mode = [
        {
            "mode": r[0], "n": _num(r[1]),
            "accuracy": _num(r[2]), "completeness": _num(r[3]),
            "clarity": _num(r[4]), "faithfulness": _num(r[5]), "overall": _num(r[6]),
        }
        for r in rows
    ]

    haiku_n_overall = next((_num(r[6]) for r in rows if r[0] == "haiku_neuron"), None)
    opus_r_overall = next((_num(r[6]) for r in rows if r[0] == "opus_raw"), None)
    quality_ratio = round(haiku_n_overall / opus_r_overall * 100) if haiku_n_overall and opus_r_overall else None

    return {"quality_by_mode": quality_by_mode, "quality_ratio": quality_ratio}


async def _reliability(db: AsyncSession) -> dict:
    rows = (await db.execute(text("""
        SELECT overall, COUNT(*) as n FROM eval_scores
        WHERE answer_mode = 'haiku_neuron' GROUP BY overall ORDER BY overall
    """))).fetchall()

    score_dist = [{"score": r[0], "count": r[1]} for r in rows]
    total_scored = sum(r[1] for r in rows)
    good_count = sum(r[1] for r in rows if r[0] >= 4)

    return {
        "distribution": score_dist,
        "total_evaluated": total_scored,
        "score_4_plus": good_count,
        "reliability_pct": round(good_count / total_scored * 100) if total_scored > 0 else 0,
    }


async def _quality_trend(db: AsyncSession, total_queries: int) -> dict:
    median_id = total_queries // 2
    rows = (await db.execute(text(f"""
        SELECT
            CASE WHEN q.id <= {median_id} THEN 'early' ELSE 'late' END as period,
            COUNT(DISTINCT q.id) as queries,
            ROUND(AVG(e.overall)::numeric, 2) as avg_overall,
            ROUND(AVG(e.accuracy)::numeric, 2) as avg_acc,
            ROUND(AVG(e.completeness)::numeric, 2) as avg_comp
        FROM queries q JOIN eval_scores e ON e.query_id = q.id
        WHERE e.answer_mode = 'haiku_neuron'
        GROUP BY CASE WHEN q.id <= {median_id} THEN 'early' ELSE 'late' END
    """))).fetchall()

    return {r[0]: {"queries": _num(r[1]), "overall": _num(r[2]), "accuracy": _num(r[3]), "completeness": _num(r[4])} for r in rows}


async def _neuron_stats(db: AsyncSession) -> dict:
    row = (await db.execute(text("SELECT COUNT(*) FROM neurons WHERE is_active = true"))).fetchone()
    active_neurons = row[0]

    row = (await db.execute(text("SELECT COUNT(*) FROM neurons WHERE invocations = 0"))).fetchone()
    never_fired = row[0]

    rows = (await db.execute(text("""
        SELECT
            CASE
                WHEN invocations = 0 THEN 'never_fired'
                WHEN invocations BETWEEN 1 AND 5 THEN '1_to_5'
                WHEN invocations BETWEEN 6 AND 20 THEN '6_to_20'
                ELSE 'over_20'
            END as bucket, COUNT(*) as n
        FROM neurons WHERE is_active = true GROUP BY bucket
    """))).fetchall()
    utilization = {r[0]: r[1] for r in rows}

    row = (await db.execute(text("SELECT COUNT(DISTINCT neuron_id) FROM neuron_firings"))).fetchone()
    distinct_fired = row[0]

    rows = (await db.execute(text("""
        SELECT layer, COUNT(*) FROM neurons WHERE is_active = true GROUP BY layer ORDER BY layer
    """))).fetchall()
    layer_dist = {f"L{r[0]}": r[1] for r in rows}

    rows = (await db.execute(text("""
        SELECT department, COUNT(*) FROM neurons WHERE is_active = true AND department IS NOT NULL
        GROUP BY department ORDER BY COUNT(*) DESC
    """))).fetchall()
    dept_dist = {r[0]: r[1] for r in rows}

    return {
        "active_neurons": active_neurons,
        "distinct_fired": distinct_fired,
        "never_fired": never_fired,
        "utilization": utilization,
        "layer_distribution": layer_dist,
        "department_distribution": dept_dist,
    }


async def _refinement_impact(db: AsyncSession) -> dict:
    row = (await db.execute(text(
        "SELECT COUNT(*) FROM neuron_refinements WHERE action = 'create'"
    ))).fetchone()
    created = row[0]

    row = (await db.execute(text(
        "SELECT COUNT(*) FROM neuron_refinements WHERE action = 'update'"
    ))).fetchone()
    updated = row[0]

    rows = (await db.execute(text("""
        SELECT
            CASE
                WHEN created_at_query_count <= 25 THEN 'seed'
                WHEN created_at_query_count <= 50 THEN 'q26_50'
                WHEN created_at_query_count <= 75 THEN 'q51_75'
                ELSE 'q76_plus'
            END as phase, COUNT(*) as n
        FROM neurons GROUP BY phase ORDER BY MIN(created_at_query_count)
    """))).fetchall()
    graph_growth = {r[0]: r[1] for r in rows}

    return {
        "neurons_created": created,
        "neurons_updated": updated,
        "graph_growth": graph_growth,
    }


async def _autopilot_stats(db: AsyncSession) -> dict:
    rows = (await db.execute(text("""
        SELECT status, COUNT(*) as n,
            ROUND(AVG(eval_overall)::numeric, 2) as avg_score,
            COALESCE(SUM(neurons_created), 0) as created,
            COALESCE(SUM(updates_applied), 0) as updated,
            ROUND(COALESCE(SUM(cost_usd), 0)::numeric, 4) as cost
        FROM autopilot_runs GROUP BY status
    """))).fetchall()

    return [
        {"status": r[0], "runs": _num(r[1]), "avg_score": _num(r[2]),
         "created": _num(r[3]), "updated": _num(r[4]), "cost": _num(r[5])}
        for r in rows
    ]


async def _investment(db: AsyncSession) -> dict:
    row = (await db.execute(text("SELECT COALESCE(SUM(cost_usd), 0) FROM queries"))).fetchone()
    query_spend = round(_num(row[0]), 2)
    row = (await db.execute(text("SELECT COALESCE(SUM(cost_usd), 0) FROM autopilot_runs"))).fetchone()
    autopilot_spend = round(_num(row[0]), 2)

    return {
        "query_pipeline": query_spend,
        "autopilot": autopilot_spend,
        "total": round(query_spend + autopilot_spend, 2),
    }


async def _neuron_quality_correlation(db: AsyncSession) -> dict:
    rows = (await db.execute(text("""
        SELECT
            CASE
                WHEN json_array_length(q.selected_neuron_ids::json) <= 5 THEN '1-5'
                WHEN json_array_length(q.selected_neuron_ids::json) <= 15 THEN '6-15'
                WHEN json_array_length(q.selected_neuron_ids::json) <= 30 THEN '16-30'
                ELSE '31+'
            END as bucket,
            COUNT(DISTINCT q.id) as queries,
            ROUND(AVG(e.overall)::numeric, 2) as avg_score
        FROM queries q
        JOIN eval_scores e ON e.query_id = q.id
        WHERE e.answer_mode = 'haiku_neuron'
        GROUP BY bucket
        ORDER BY MIN(json_array_length(q.selected_neuron_ids::json))
    """))).fetchall()

    return [{"bucket": r[0], "queries": _num(r[1]), "avg_score": _num(r[2])} for r in rows]


async def _query_timeline(db: AsyncSession) -> dict:
    rows = (await db.execute(text("""
        SELECT q.id, ROUND(q.cost_usd::numeric, 4) as cost,
            json_array_length(q.selected_neuron_ids::json) as neurons,
            (SELECT ROUND(AVG(overall)::numeric, 2) FROM eval_scores WHERE query_id = q.id AND answer_mode = 'haiku_neuron') as score,
            q.created_at
        FROM queries q ORDER BY q.id
    """))).fetchall()

    return [
        {"id": r[0], "cost": _num(r[1]), "neurons": _num(r[2]), "score": _num(r[3]), "created_at": r[4]}
        for r in rows
    ]


async def _statistical_tests(db: AsyncSession, total_queries: int) -> dict:
    hn_overall = await _get_scores(db, "haiku_neuron")
    op_overall = await _get_scores(db, "opus_raw")
    hr_overall = await _get_scores(db, "haiku_raw")
    sn_overall = await _get_scores(db, "sonnet_neuron")
    on_overall = await _get_scores(db, "opus_neuron")
    sr_overall = await _get_scores(db, "sonnet_raw")
    hn_comp = await _get_scores(db, "haiku_neuron", "completeness")
    op_comp = await _get_scores(db, "opus_raw", "completeness")

    stat_tests = []

    # Test 1: Haiku+Neurons vs Opus Raw (overall)
    if len(hn_overall) >= 3 and len(op_overall) >= 3:
        stat_tests.append(_test_quality_gap(hn_overall, op_overall))

    # Test 2: Haiku+Neurons vs Haiku Raw (neuron value-add)
    if len(hn_overall) >= 3 and len(hr_overall) >= 3:
        stat_tests.append(_test_neuron_value(hn_overall, hr_overall))

    # Test 3: All enriched vs all raw (pooled)
    enriched_all = hn_overall + sn_overall + on_overall
    raw_all = op_overall + sr_overall + hr_overall
    if len(enriched_all) >= 3 and len(raw_all) >= 3:
        stat_tests.append(_test_enriched_vs_raw(enriched_all, raw_all))

    # Test 4: Quality trend (early vs late)
    median_id = total_queries // 2
    trend_test = await _test_quality_trend(db, median_id, total_queries)
    if trend_test is not None:
        stat_tests.append(trend_test)

    # Test 5: Reliability binomial CI
    if len(hn_overall) >= 3:
        stat_tests.append(_test_reliability(hn_overall))

    # Test 6: Completeness H+N > Opus Raw
    if len(hn_comp) >= 3 and len(op_comp) >= 3:
        stat_tests.append(_test_completeness(hn_comp, op_comp))

    # Benjamini-Hochberg FDR correction
    fdr_summary = _apply_fdr(stat_tests)

    return {"stat_tests": stat_tests, "fdr_correction": fdr_summary}


def _test_quality_gap(hn_overall: list[float], op_overall: list[float]) -> dict:
    hn_arr = np.array(hn_overall)
    op_arr = np.array(op_overall)
    t_stat, t_p = sp_stats.ttest_ind(hn_arr, op_arr, equal_var=False)
    u_stat, mw_p = sp_stats.mannwhitneyu(hn_arr, op_arr, alternative='two-sided')
    d = _cohens_d(hn_arr, op_arr)
    diff = float(hn_arr.mean() - op_arr.mean())
    se = np.sqrt(hn_arr.std(ddof=1)**2/len(hn_arr) + op_arr.std(ddof=1)**2/len(op_arr))
    n_need = _power_n(d)
    return {
        "id": "quality_gap",
        "title": "Haiku+Neurons vs Opus Raw — Overall Quality",
        "description": "Tests whether the quality difference between cheap enriched queries and expensive raw queries is statistically real.",
        "group_a": {"label": "Haiku+Neurons", "n": len(hn_arr), "mean": round(float(hn_arr.mean()), 3), "std": round(float(hn_arr.std(ddof=1)), 3)},
        "group_b": {"label": "Opus Raw", "n": len(op_arr), "mean": round(float(op_arr.mean()), 3), "std": round(float(op_arr.std(ddof=1)), 3)},
        "welch_t": round(float(t_stat), 4), "welch_p": round(float(t_p), 6),
        "mann_whitney_u": round(float(u_stat), 1), "mann_whitney_p": round(float(mw_p), 6),
        "cohens_d": round(d, 3), "effect_size": _effect_label(d),
        "mean_diff": round(diff, 3),
        "ci_95": [round(diff - 1.96 * float(se), 3), round(diff + 1.96 * float(se), 3)],
        "n_needed_80pct_power": n_need,
        "adequately_powered": min(len(hn_arr), len(op_arr)) >= n_need,
        "significant_welch": float(t_p) < 0.05,
        "significant_mw": float(mw_p) < 0.05,
    }


def _test_neuron_value(hn_overall: list[float], hr_overall: list[float]) -> dict:
    hn_arr = np.array(hn_overall)
    hr_arr = np.array(hr_overall)
    u2, mw2_p = sp_stats.mannwhitneyu(hn_arr, hr_arr, alternative='greater')
    d2 = _cohens_d(hn_arr, hr_arr)
    return {
        "id": "neuron_value",
        "title": "Haiku+Neurons vs Haiku Raw — Neuron Value-Add",
        "description": "Tests whether adding neuron context measurably improves Haiku's answer quality.",
        "group_a": {"label": "Haiku+Neurons", "n": len(hn_arr), "mean": round(float(hn_arr.mean()), 3)},
        "group_b": {"label": "Haiku Raw", "n": len(hr_arr), "mean": round(float(hr_arr.mean()), 3)},
        "mann_whitney_u": round(float(u2), 1), "mann_whitney_p": round(float(mw2_p), 6),
        "cohens_d": round(d2, 3), "effect_size": _effect_label(d2),
        "one_sided": True,
        "significant_mw": float(mw2_p) < 0.05,
        "warning": f"n={len(hr_arr)} for Haiku Raw is critically small" if len(hr_arr) < 10 else None,
    }


def _test_enriched_vs_raw(enriched_all: list[float], raw_all: list[float]) -> dict:
    en_arr = np.array(enriched_all)
    ra_arr = np.array(raw_all)
    t3, p3 = sp_stats.ttest_ind(en_arr, ra_arr, equal_var=False)
    u3, mw3 = sp_stats.mannwhitneyu(en_arr, ra_arr, alternative='two-sided')
    d3 = _cohens_d(en_arr, ra_arr)
    n3 = _power_n(d3)
    return {
        "id": "enriched_vs_raw",
        "title": "All Enriched vs All Raw (Pooled)",
        "description": "Pools all neuron-enriched evaluations against all raw evaluations across models.",
        "group_a": {"label": "All Enriched", "n": len(en_arr), "mean": round(float(en_arr.mean()), 3), "std": round(float(en_arr.std(ddof=1)), 3)},
        "group_b": {"label": "All Raw", "n": len(ra_arr), "mean": round(float(ra_arr.mean()), 3), "std": round(float(ra_arr.std(ddof=1)), 3)},
        "welch_t": round(float(t3), 4), "welch_p": round(float(p3), 6),
        "mann_whitney_u": round(float(u3), 1), "mann_whitney_p": round(float(mw3), 6),
        "cohens_d": round(d3, 3), "effect_size": _effect_label(d3),
        "n_needed_80pct_power": n3,
        "adequately_powered": min(len(en_arr), len(ra_arr)) >= n3,
        "significant_welch": float(p3) < 0.05,
        "significant_mw": float(mw3) < 0.05,
    }


async def _test_quality_trend(db: AsyncSession, median_id: int, total_queries: int) -> dict | None:
    early_scores = [float(r[0]) for r in (await db.execute(text(
        f"SELECT overall FROM eval_scores WHERE answer_mode='haiku_neuron' AND query_id <= {median_id}"
    ))).fetchall()]
    late_scores = [float(r[0]) for r in (await db.execute(text(
        f"SELECT overall FROM eval_scores WHERE answer_mode='haiku_neuron' AND query_id > {median_id}"
    ))).fetchall()]
    if len(early_scores) < 3 or len(late_scores) < 3:
        return None
    ea = np.array(early_scores)
    la = np.array(late_scores)
    t4, p4 = sp_stats.ttest_ind(la, ea, equal_var=False, alternative='greater')
    u4, mw4 = sp_stats.mannwhitneyu(la, ea, alternative='greater')
    return {
        "id": "quality_trend",
        "title": "Quality Trend — Early vs Late",
        "description": "Tests whether Haiku+Neuron quality improved as the graph grew over time.",
        "group_a": {"label": f"Late (Q{median_id+1}-{total_queries})", "n": len(la), "mean": round(float(la.mean()), 3), "std": round(float(la.std(ddof=1)), 3)},
        "group_b": {"label": f"Early (Q1-{median_id})", "n": len(ea), "mean": round(float(ea.mean()), 3), "std": round(float(ea.std(ddof=1)), 3)},
        "welch_t": round(float(t4), 4), "welch_p": round(float(p4), 6),
        "mann_whitney_u": round(float(u4), 1), "mann_whitney_p": round(float(mw4), 6),
        "one_sided": True,
        "significant_welch": float(p4) < 0.05,
        "significant_mw": float(mw4) < 0.05,
    }


def _test_reliability(hn_overall: list[float]) -> dict:
    n_tot = len(hn_overall)
    n_good = sum(1 for s in hn_overall if s >= 4)
    p_hat = n_good / n_tot
    z = 1.96
    denom = 1 + z**2 / n_tot
    center = (p_hat + z**2 / (2 * n_tot)) / denom
    margin = z * math.sqrt(p_hat * (1 - p_hat) / n_tot + z**2 / (4 * n_tot**2)) / denom

    bt75 = sp_stats.binomtest(n_good, n_tot, 0.75, alternative='greater')
    bt70 = sp_stats.binomtest(n_good, n_tot, 0.70, alternative='greater')

    return {
        "id": "reliability",
        "title": "Reliability — Score >= 4 Rate",
        "description": "Wilson confidence interval and binomial tests for the proportion of queries scoring 4 or above.",
        "n_total": n_tot, "n_good": n_good, "observed_rate": round(p_hat * 100, 1),
        "wilson_ci_95": [round((center - margin) * 100, 1), round((center + margin) * 100, 1)],
        "binomial_75": {"p": round(float(bt75.pvalue), 6), "significant": float(bt75.pvalue) < 0.05,
                        "claim": "Can claim >75% reliability" if float(bt75.pvalue) < 0.05 else "Cannot claim >75% reliability"},
        "binomial_70": {"p": round(float(bt70.pvalue), 6), "significant": float(bt70.pvalue) < 0.05,
                        "claim": "Can claim >70% reliability" if float(bt70.pvalue) < 0.05 else "Cannot claim >70% reliability"},
    }


def _test_completeness(hn_comp: list[float], op_comp: list[float]) -> dict:
    hnc = np.array(hn_comp)
    opc = np.array(op_comp)
    u6, mw6 = sp_stats.mannwhitneyu(hnc, opc, alternative='greater')
    d6 = _cohens_d(hnc, opc)
    n6 = _power_n(d6)
    return {
        "id": "completeness",
        "title": "Completeness — Haiku+Neurons vs Opus Raw",
        "description": "Tests whether neuron context gives Haiku better completeness than Opus achieves natively.",
        "group_a": {"label": "Haiku+Neurons", "n": len(hnc), "mean": round(float(hnc.mean()), 3)},
        "group_b": {"label": "Opus Raw", "n": len(opc), "mean": round(float(opc.mean()), 3)},
        "mann_whitney_u": round(float(u6), 1), "mann_whitney_p": round(float(mw6), 6),
        "cohens_d": round(d6, 3), "effect_size": _effect_label(d6),
        "one_sided": True,
        "n_needed_80pct_power": n6,
        "adequately_powered": min(len(hnc), len(opc)) >= n6,
        "significant_mw": float(mw6) < 0.05,
    }


def _apply_fdr(stat_tests: list[dict]) -> dict | None:
    raw_pvals: list[tuple[int, str, float]] = []
    for i, t in enumerate(stat_tests):
        if "mann_whitney_p" in t:
            raw_pvals.append((i, "mann_whitney", t["mann_whitney_p"]))
        if "welch_p" in t:
            raw_pvals.append((i, "welch", t["welch_p"]))
        if "binomial_75" in t:
            raw_pvals.append((i, "binom75", t["binomial_75"]["p"]))
        if "binomial_70" in t:
            raw_pvals.append((i, "binom70", t["binomial_70"]["p"]))

    if not raw_pvals:
        return None

    m = len(raw_pvals)
    sorted_pvals = sorted(raw_pvals, key=lambda x: x[2])
    adjusted = [0.0] * m
    for rank_idx in range(m - 1, -1, -1):
        rank = rank_idx + 1
        raw_p = sorted_pvals[rank_idx][2]
        bh_p = raw_p * m / rank
        if rank_idx < m - 1:
            bh_p = min(bh_p, adjusted[rank_idx + 1])
        adjusted[rank_idx] = min(bh_p, 1.0)

    for rank_idx, (test_i, field, _raw_p) in enumerate(sorted_pvals):
        adj_p = round(adjusted[rank_idx], 6)
        t = stat_tests[test_i]
        if field == "mann_whitney":
            t["mann_whitney_p_adj"] = adj_p
            t["significant_mw_fdr"] = adj_p < 0.05
        elif field == "welch":
            t["welch_p_adj"] = adj_p
            t["significant_welch_fdr"] = adj_p < 0.05
        elif field == "binom75":
            t["binomial_75"]["p_adj"] = adj_p
            t["binomial_75"]["significant_fdr"] = adj_p < 0.05
        elif field == "binom70":
            t["binomial_70"]["p_adj"] = adj_p
            t["binomial_70"]["significant_fdr"] = adj_p < 0.05

    return {
        "method": "Benjamini-Hochberg",
        "total_tests": m,
        "alpha": 0.05,
        "description": "Controls false discovery rate — the expected proportion of false positives among rejected hypotheses.",
    }


@router.get("/performance")
async def performance_report(db: AsyncSession = Depends(get_db)):
    """Compute all performance analytics from database. No LLM calls."""

    result = await db.execute(text("SELECT COUNT(*) FROM queries"))
    total_queries = result.scalar() or 0
    if total_queries == 0:
        return {"error": "No queries to analyze"}

    cost_summary = await _cost_summary(db)
    cost_modeling = await _cost_modeling(db)
    cost_by_activity = await _cost_by_activity(db)
    quality_data = await _quality_by_mode(db)
    reliability = await _reliability(db)
    quality_trend = await _quality_trend(db, total_queries)
    neuron_stats = await _neuron_stats(db)
    refinement_impact = await _refinement_impact(db)
    autopilot = await _autopilot_stats(db)
    investment = await _investment(db)
    neuron_quality_corr = await _neuron_quality_correlation(db)
    query_timeline = await _query_timeline(db)
    stats_data = await _statistical_tests(db, total_queries)

    return {
        "stat_tests": stats_data["stat_tests"],
        "fdr_correction": stats_data["fdr_correction"],
        "cost_summary": cost_summary,
        "cost_modeling": cost_modeling,
        "cost_by_activity": cost_by_activity,
        "quality_by_mode": quality_data["quality_by_mode"],
        "quality_ratio": quality_data["quality_ratio"],
        "reliability": reliability,
        "quality_trend": quality_trend,
        "neuron_stats": neuron_stats,
        "refinement_impact": refinement_impact,
        "autopilot": autopilot,
        "investment": investment,
        "neuron_quality_correlation": neuron_quality_corr,
        "query_timeline": query_timeline,
    }


# Design latency targets, pipeline order, and human-readable labels. The
# stage-telemetry report surfaces measured p50 against these targets so drift
# between the intended cheap-recall path and reality is visible.
STAGE_META = MappingProxyType({
    "structural_resolve": {"label": "Structural fast path",          "order": 0, "estimate_ms": 0.1},
    # CheapClassifyStage embeds locally then classifies from nearest-neighbor
    # tags. It never invokes an LLM; 150ms is the cheap-recall design target.
    "classify":           {"label": "Semantic encode + neighbor vote", "order": 1, "estimate_ms": 150.0},
    "prefilter_score":    {"label": "Retrieve + score candidates",    "order": 2, "estimate_ms": 3.0},
    "continuity_boost":   {"label": "Conversation continuity",        "order": 3, "estimate_ms": 1.0},
    "spread_activation":  {"label": "Graph spread activation",        "order": 4, "estimate_ms": 1.0},
    "inhibitory":         {"label": "Redundancy inhibition",          "order": 5, "estimate_ms": 5.0},
    "engram_edge_boost":  {"label": "Engram association boost",       "order": 6, "estimate_ms": 1.0},
    "regulatory_resolve": {"label": "Regulatory context resolve",     "order": 7, "estimate_ms": 50.0},
    "assemble_prompt":    {"label": "Assemble memory context",        "order": 8, "estimate_ms": 10.0},
})

# A pipeline observation is an incident, not a representative recall sample,
# when a core shared-prefix stage stalls well beyond normal scheduling jitter.
# Keep its raw Query.stage_telemetry_json for forensics; exclude the *entire*
# pipeline observation from the performance dashboard so one overloaded host
# does not contaminate any of its stage distributions.
PERFORMANCE_INCIDENT_FILTER = """
NOT EXISTS (
    SELECT 1 FROM jsonb_array_elements(q.stage_telemetry_json) incident
    WHERE (incident->>'stage' = 'classify'
           AND (incident->>'duration_ms')::float > 5000)
       OR (incident->>'stage' = 'prefilter_score'
           AND (incident->>'duration_ms')::float > 1000)
)
"""


async def _stage_stats(db: AsyncSession) -> list[dict]:
    """Per-stage latency distribution across non-incident pipeline observations."""
    rows = (await db.execute(text(
        "SELECT stage, COUNT(*) n, AVG(d) mean, COALESCE(STDDEV_SAMP(d),0) sd, "
        "percentile_cont(0.5)  WITHIN GROUP (ORDER BY d) p50, "
        "percentile_cont(0.9)  WITHIN GROUP (ORDER BY d) p90, "
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY d) p95, "
        "percentile_cont(0.99) WITHIN GROUP (ORDER BY d) p99, "
        "MIN(d) mn, MAX(d) mx FROM ("
        "  SELECT t->>'stage' AS stage, (t->>'duration_ms')::float AS d "
        "  FROM queries q, jsonb_array_elements(q.stage_telemetry_json) t "
        "  WHERE q.stage_telemetry_json IS NOT NULL AND (t->>'duration_ms') IS NOT NULL "
        f"AND {PERFORMANCE_INCIDENT_FILTER}"
        ") s GROUP BY stage"
    ))).all()
    out = []
    for r in rows:
        m = STAGE_META.get(r.stage, {"label": r.stage, "order": 99, "estimate_ms": None})
        mean = _num(r.mean); sd = _num(r.sd); p50 = _num(r.p50); est = m["estimate_ms"]
        out.append({
            "stage": r.stage, "label": m["label"], "order": m["order"], "n": int(r.n),
            "mean": round(mean, 2), "stddev": round(sd, 2),
            "cov": round(sd / mean, 3) if mean else 0.0,
            "p50": round(p50, 2), "p90": round(_num(r.p90), 2),
            "p95": round(_num(r.p95), 2), "p99": round(_num(r.p99), 2),
            "min": round(_num(r.mn), 2), "max": round(_num(r.mx), 2),
            "estimate_ms": est,
            "ratio_p50_vs_estimate": round(p50 / est, 1) if est else None,
        })
    out.sort(key=lambda s: s["order"])
    return out


async def _stage_trend(db: AsyncSession) -> list[dict]:
    """Per-day p50/p95 per stage — the drift series for tuning over time."""
    rows = (await db.execute(text(
        "SELECT to_char(date_trunc('day', q.created_at), 'YYYY-MM-DD') bucket, "
        "t->>'stage' stage, COUNT(*) n, "
        "percentile_cont(0.5)  WITHIN GROUP (ORDER BY (t->>'duration_ms')::float) p50, "
        "percentile_cont(0.95) WITHIN GROUP (ORDER BY (t->>'duration_ms')::float) p95 "
        "FROM queries q, jsonb_array_elements(q.stage_telemetry_json) t "
        "WHERE q.stage_telemetry_json IS NOT NULL AND (t->>'duration_ms') IS NOT NULL "
        f"AND {PERFORMANCE_INCIDENT_FILTER} "
        "AND q.created_at IS NOT NULL GROUP BY bucket, stage ORDER BY bucket"
    ))).all()
    return [{"bucket": r.bucket, "stage": r.stage, "n": int(r.n),
             "p50": round(_num(r.p50), 2), "p95": round(_num(r.p95), 2)} for r in rows]


@router.get("/performance/stage-telemetry")
async def stage_telemetry_report(db: AsyncSession = Depends(get_db)):
    """Per-stage pipeline-latency statistics from Query.stage_telemetry_json.

    Aggregates every recorded stage duration: distribution (mean/stddev/CoV,
    p50/p90/p95/p99, min/max), the share of total pipeline latency, design
    target vs. measured p50, and a per-day drift series. Pipeline observations
    with a core-stage incident are excluded while their raw telemetry is retained.
    Pure SQL, no LLM calls.
    """
    n_q = (await db.execute(text(
        "SELECT COUNT(*) FROM queries q WHERE q.stage_telemetry_json IS NOT NULL "
        f"AND {PERFORMANCE_INCIDENT_FILTER}"
    ))).scalar() or 0
    if n_q == 0:
        return {"error": "No stage telemetry recorded yet"}

    stats = await _stage_stats(db)
    trend = await _stage_trend(db)
    total_p50 = sum(s["p50"] for s in stats) or 1.0
    for s in stats:
        s["share_pct"] = round(100.0 * s["p50"] / total_p50, 1)
    n_incident = (await db.execute(text(
        "SELECT COUNT(*) FROM queries q WHERE q.stage_telemetry_json IS NOT NULL "
        f"AND NOT ({PERFORMANCE_INCIDENT_FILTER})"
    ))).scalar() or 0
    rng = (await db.execute(text(
        "SELECT MIN(created_at), MAX(created_at) FROM queries q "
        "WHERE q.stage_telemetry_json IS NOT NULL "
        f"AND {PERFORMANCE_INCIDENT_FILTER}"
    ))).first()
    return {
        "meta": {
            "queries_with_telemetry": int(n_q),
            "excluded_incident_queries": int(n_incident),
            "total_samples": sum(s["n"] for s in stats),
            "pipeline_total_p50_ms": round(total_p50, 2),
            "date_range": [rng[0].isoformat() if rng and rng[0] else None,
                           rng[1].isoformat() if rng and rng[1] else None],
        },
        "stages": stats,
        "trend": trend,
    }
