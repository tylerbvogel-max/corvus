"""Compliance audit and governance reporting.

Extracted from ``admin.py`` by roadmap record ``durability-file-size-seams``
(04c). One job, read-only: measure the graph against the compliance and
governance questions an operator has to answer — PII exposure, department
coverage, bias, scoring baselines, provenance, validity/reliability,
remediation, fairness, and the cost/parity rollup that sits on top of them.

Nothing here writes. Nothing here owns a gate. That is precisely why it was
the safe half of admin.py to move: the classification pass found no delete
path, no create path, and no protected-column assignment anywhere in this
block, so relocating it cannot widen a gate in either direction. The
endpoints keep the ``/admin`` prefix and their exact paths.
"""

import json
import math
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    Neuron, Query, EvalScore, AutopilotRun, SystemAlert, NeuronRefinement,
)
from app.services.scoring_engine import SCORING_SIGNALS as _SCORING_SIGNALS

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Compliance Audit: PII scan, bias assessment, scoring baselines, provenance audit ──

# PII patterns for neuron content scanning.
# Aerospace content has FAR/DFARS clause numbers (e.g. 52.246-2, 252.204-7012)
# and example/placeholder emails in technical neurons — these are excluded.
_DFARS_PATTERN = re.compile(r"\b\d{2,3}\.\d{3}-\d{4}\b")  # matches 52.246-2102, 252.204-7012
_EXAMPLE_EMAIL = re.compile(r"@(example|placeholder|test|acme)\.", re.I)
_PII_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "SSN"),  # SSN requires dashes (123-45-6789)
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "email"),
    (re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"), "credit_card"),
    (re.compile(r"(?<!\d\.)\b\d{3}[-\s]\d{3}[-\s]\d{4}\b"), "phone"),  # phone, not preceded by digit.
]


def _is_false_positive(text: str, match_str: str, pii_type: str) -> bool:
    """Filter out known false positives in aerospace content."""
    if pii_type == "phone" and _DFARS_PATTERN.search(match_str):
        return True
    if pii_type == "phone":
        # Check if this match is part of a DFARS/FAR clause number
        idx = text.find(match_str)
        if idx > 0 and text[idx - 1] == '.':
            return True  # preceded by dot = clause number like 252.204-7012
    if pii_type == "email" and _EXAMPLE_EMAIL.search(match_str):
        return True
    return False


def _audit_pii_exposure(neurons) -> dict:
    """Scan neurons for PII patterns (MET-3). Returns pii_scan section."""
    pii_findings: list[dict] = []
    for neuron in neurons:
        for field_name, text_val in [("content", neuron.content), ("summary", neuron.summary), ("label", neuron.label)]:
            if not text_val:
                continue
            for pattern, pii_type in _PII_PATTERNS:
                matches = pattern.findall(text_val)
                real_matches = [m for m in matches if not _is_false_positive(text_val, m, pii_type)]
                if real_matches:
                    pii_findings.append({
                        "neuron_id": neuron.id,
                        "neuron_label": neuron.label[:80],
                        "department": neuron.department,
                        "field": field_name,
                        "pii_type": pii_type,
                        "match_count": len(real_matches),
                        "excerpt": real_matches[0][:20] + "..." if len(real_matches[0]) > 20 else real_matches[0],
                    })
    return {
        "findings": pii_findings,
        "total_findings": len(pii_findings),
        "neurons_with_pii": len(set(f["neuron_id"] for f in pii_findings)),
        "clean": len(pii_findings) == 0,
    }


def _audit_neuron_coverage(neurons, total_neurons: int) -> dict:
    """Compute per-department/layer/source coverage tallies. Returns coverage context dict."""
    dept_counts: dict[str, int] = {}
    dept_invocations: dict[str, int] = {}
    dept_utility: dict[str, list[float]] = {}
    layer_counts: dict[int, int] = {}
    source_type_counts: dict[str, int] = {}

    for neuron in neurons:
        dept = neuron.department or "(none)"
        dept_counts[dept] = dept_counts.get(dept, 0) + 1
        dept_invocations[dept] = dept_invocations.get(dept, 0) + neuron.invocations
        dept_utility.setdefault(dept, []).append(neuron.avg_utility)
        layer_counts[neuron.layer] = layer_counts.get(neuron.layer, 0) + 1
        st = neuron.source_type or "unknown"
        source_type_counts[st] = source_type_counts.get(st, 0) + 1

    dept_values = list(dept_counts.values())
    dept_mean = sum(dept_values) / len(dept_values) if dept_values else 0
    dept_variance = sum((v - dept_mean) ** 2 for v in dept_values) / max(1, len(dept_values) - 1) if len(dept_values) > 1 else 0
    dept_cv = math.sqrt(dept_variance) / dept_mean if dept_mean > 0 else 0

    dept_coverage = sorted([
        {
            "department": dept, "neuron_count": count,
            "pct_of_total": round(count / total_neurons * 100, 1) if total_neurons else 0,
            "total_invocations": dept_invocations.get(dept, 0),
            "avg_utility": round(sum(dept_utility.get(dept, [0.5])) / len(dept_utility.get(dept, [0.5])), 3),
        }
        for dept, count in dept_counts.items()
    ], key=lambda x: -x["neuron_count"])

    return {
        "dept_counts": dept_counts, "dept_invocations": dept_invocations,
        "dept_utility": dept_utility, "layer_counts": layer_counts,
        "source_type_counts": source_type_counts, "dept_cv": dept_cv,
        "dept_coverage": dept_coverage,
    }


async def _audit_bias_assessment(db: AsyncSession, coverage: dict, total_neurons: int) -> dict:
    """Build bias/coverage assessment section (MET-4). Returns bias_assessment dict."""
    eval_by_mode = await db.execute(
        select(
            EvalScore.answer_mode, func.count(EvalScore.id),
            func.avg(EvalScore.accuracy), func.avg(EvalScore.completeness),
            func.avg(EvalScore.clarity), func.avg(EvalScore.faithfulness),
            func.avg(EvalScore.overall),
        ).group_by(EvalScore.answer_mode)
    )
    eval_disaggregation = [
        {
            "mode": row[0], "count": row[1],
            "avg_accuracy": round(float(row[2] or 0), 2),
            "avg_completeness": round(float(row[3] or 0), 2),
            "avg_clarity": round(float(row[4] or 0), 2),
            "avg_faithfulness": round(float(row[5] or 0), 2),
            "avg_overall": round(float(row[6] or 0), 2),
        }
        for row in eval_by_mode.all()
    ]
    return {
        "department_coverage": coverage["dept_coverage"],
        "department_count": len(coverage["dept_counts"]),
        "coverage_cv": round(coverage["dept_cv"], 3),
        "coverage_imbalanced": coverage["dept_cv"] > 0.5,
        "layer_distribution": {f"L{k}": v for k, v in sorted(coverage["layer_counts"].items())},
        "eval_disaggregation": eval_disaggregation,
    }


def _percentile(vals: list[float], p: float) -> float:
    """Compute the p-th percentile of a sorted list using linear interpolation."""
    if not vals:
        return 0.0
    sorted_vals = sorted(vals)
    k = (len(sorted_vals) - 1) * (p / 100)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


async def _audit_scoring_baselines(db: AsyncSession) -> dict:
    """Compute scoring baselines from recent queries (MET-1). Returns scoring_baselines section."""
    scores_result = await db.execute(
        select(Query.neuron_scores_json)
        .where(Query.neuron_scores_json.isnot(None))
        .order_by(Query.id.desc()).limit(200)
    )
    all_signal_values: dict[str, list[float]] = {s: [] for s in _SCORING_SIGNALS}
    queries_parsed = 0
    for (scores_json,) in scores_result.all():
        try:
            scores = json.loads(scores_json) if scores_json else []
        except json.JSONDecodeError:
            continue
        if not scores:
            continue
        queries_parsed += 1
        for ns in scores:
            for s in _SCORING_SIGNALS:
                val = ns.get(s)
                if val is not None:
                    all_signal_values[s].append(float(val))

    scoring_baselines: dict[str, dict] = {}
    for sig in _SCORING_SIGNALS:
        vals = all_signal_values[sig]
        n = len(vals)
        if n == 0:
            scoring_baselines[sig] = {"count": 0, "mean": 0, "stddev": 0, "min": 0, "max": 0, "p25": 0, "p50": 0, "p75": 0, "p95": 0}
            continue
        mean = sum(vals) / n
        variance = sum((v - mean) ** 2 for v in vals) / max(1, n - 1)
        scoring_baselines[sig] = {
            "count": n, "mean": round(mean, 4), "stddev": round(math.sqrt(variance), 4),
            "min": round(min(vals), 4), "max": round(max(vals), 4),
            "p25": round(_percentile(vals, 25), 4), "p50": round(_percentile(vals, 50), 4),
            "p75": round(_percentile(vals, 75), 4), "p95": round(_percentile(vals, 95), 4),
        }

    return {
        "queries_analyzed": queries_parsed,
        "signals": scoring_baselines,
        "all_signal_values": all_signal_values,
        "metric_rationale": {
            "burst": "Recency-weighted firing frequency. Higher = neuron is trending. Prevents stale content from dominating.",
            "impact": "EMA of user feedback ratings. Reflects demonstrated usefulness in past queries.",
            "precision": "Keyword overlap between query and neuron content. Direct relevance signal.",
            "novelty": "Inverse of invocation frequency. Promotes under-used neurons to prevent echo chambers.",
            "recency": "Temporal decay since last firing. Newer content gets a natural boost.",
            "relevance": "LLM-assessed semantic similarity (when available). Highest-fidelity signal but most expensive.",
        },
    }


def _audit_provenance(neurons) -> dict:
    """Check primary-source neurons for missing citations, URLs, staleness (A007)."""
    missing_citation = []
    missing_source_url = []
    stale_neurons = []
    now = datetime.utcnow()

    for neuron in neurons:
        is_primary = neuron.source_type in ("regulatory_primary", "technical_primary")
        if not is_primary:
            continue
        entry = {"neuron_id": neuron.id, "label": neuron.label[:80],
                 "department": neuron.department, "source_type": neuron.source_type}
        if not neuron.citation:
            missing_citation.append(entry)
        if not neuron.source_url:
            missing_source_url.append(entry)
        if neuron.last_verified:
            days_since = (now - neuron.last_verified).days
            if days_since > 365:
                stale_neurons.append({
                    **entry,
                    "last_verified": neuron.last_verified.isoformat(),
                    "days_since_verified": days_since,
                })

    return {
        "missing_citations": missing_citation,
        "missing_citations_count": len(missing_citation),
        "missing_source_urls": missing_source_url,
        "missing_source_urls_count": len(missing_source_url),
        "stale_neurons": stale_neurons,
        "stale_neurons_count": len(stale_neurons),
    }


def _ci95(values: list[float]) -> dict:
    """Compute mean and 95% confidence interval."""
    n = len(values)
    if n < 2:
        return {"mean": round(values[0], 3) if values else 0, "ci_lower": 0, "ci_upper": 0, "n": n, "stderr": 0}
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    stderr = math.sqrt(variance / n)
    t_val = 2.0 if n < 30 else 1.96
    margin = t_val * stderr
    return {
        "mean": round(mean, 3), "ci_lower": round(mean - margin, 3),
        "ci_upper": round(mean + margin, 3), "n": n, "stderr": round(stderr, 4),
    }


def _cross_validate_mode(overall_vals: list[float], mode: str, k_folds: int = 5) -> dict:
    """Run k-fold cross-validation on overall scores for a single mode."""
    import random as _random
    n = len(overall_vals)
    if n < k_folds:
        return {"folds": k_folds, "n": n, "fold_means": [], "fold_cv": 0,
                "stable": True, "message": "Too few samples for cross-validation"}

    shuffled = list(overall_vals)
    _random.Random(hash(mode)).shuffle(shuffled)
    fold_size = n // k_folds
    fold_means = []
    for i in range(k_folds):
        start = i * fold_size
        end = start + fold_size if i < k_folds - 1 else n
        fold_vals = shuffled[start:end]
        fold_means.append(round(sum(fold_vals) / len(fold_vals), 3))

    fm_mean = sum(fold_means) / len(fold_means)
    fm_var = sum((v - fm_mean) ** 2 for v in fold_means) / max(1, len(fold_means) - 1)
    fm_cv = math.sqrt(fm_var) / fm_mean if fm_mean > 0 else 0

    return {
        "folds": k_folds, "n": n, "fold_means": fold_means,
        "fold_cv": round(fm_cv, 4), "stable": fm_cv < 0.10,
        "message": "Stable" if fm_cv < 0.10 else "High variance across folds \u2014 results may not be robust",
    }


async def _audit_validity_reliability(db: AsyncSession, all_signal_values: dict[str, list[float]]) -> dict:
    """Compute confidence intervals, cross-validation, signal robustness (MET-2)."""
    all_evals = await db.execute(
        select(EvalScore.answer_mode, EvalScore.accuracy, EvalScore.completeness,
               EvalScore.clarity, EvalScore.faithfulness, EvalScore.overall)
        .order_by(EvalScore.id)
    )
    eval_rows = all_evals.all()

    modes_data: dict[str, dict[str, list[float]]] = {}
    for mode, acc, comp, clar, faith, overall in eval_rows:
        if mode not in modes_data:
            modes_data[mode] = {"accuracy": [], "completeness": [], "clarity": [], "faithfulness": [], "overall": []}
        modes_data[mode]["accuracy"].append(float(acc))
        modes_data[mode]["completeness"].append(float(comp))
        modes_data[mode]["clarity"].append(float(clar))
        modes_data[mode]["faithfulness"].append(float(faith))
        modes_data[mode]["overall"].append(float(overall))

    confidence_intervals = {mode: {dim: _ci95(vals) for dim, vals in dims.items()} for mode, dims in modes_data.items()}
    cross_validation = {mode: _cross_validate_mode(dims["overall"], mode) for mode, dims in modes_data.items()}

    signal_robustness: dict[str, dict] = {}
    for sig in _SCORING_SIGNALS:
        vals = all_signal_values[sig]
        if not vals:
            signal_robustness[sig] = {"cv": 0, "robust": True, "n": 0}
            continue
        mean = sum(vals) / len(vals)
        variance = sum((v - mean) ** 2 for v in vals) / max(1, len(vals) - 1)
        cv = math.sqrt(variance) / mean if mean > 0 else 0
        signal_robustness[sig] = {"cv": round(cv, 4), "robust": cv < 1.5, "n": len(vals)}

    return {
        "confidence_intervals": confidence_intervals,
        "cross_validation": cross_validation,
        "signal_robustness": signal_robustness,
        "total_evals": len(eval_rows),
    }


async def _audit_dept_eval_quality(db: AsyncSession) -> list[dict]:
    """Query per-department eval quality for fairness analysis."""
    dept_eval_result = await db.execute(text("""
        SELECT n.department, e.answer_mode,
               COUNT(*) as cnt,
               AVG(e.overall) as avg_overall,
               AVG(e.faithfulness) as avg_faith
        FROM eval_scores e
        JOIN queries q ON e.query_id = q.id
        JOIN LATERAL (
            SELECT DISTINCT n2.department
            FROM neurons n2
            WHERE n2.id = ANY(
                SELECT (jsonb_array_elements_text(q.selected_neuron_ids::jsonb))::int
            ) AND n2.department IS NOT NULL
        ) n ON true
        WHERE q.selected_neuron_ids IS NOT NULL
          AND q.selected_neuron_ids != '[]'
        GROUP BY n.department, e.answer_mode
        ORDER BY n.department, e.answer_mode
    """))
    return [
        {
            "department": row[0], "answer_mode": row[1], "eval_count": row[2],
            "avg_overall": round(float(row[3]), 2), "avg_faithfulness": round(float(row[4]), 2),
        }
        for row in dept_eval_result.fetchall()
    ]


def _audit_remediation(
    dept_cv: float, dept_counts: dict, dept_invocations: dict,
    total_neurons: int, dept_eval_quality: list[dict],
) -> list[dict]:
    """Generate automated remediation recommendations for coverage/quality/utilization gaps."""
    items: list[dict] = []

    if dept_cv > 0.5:
        fair_share = total_neurons / len(dept_counts) if dept_counts else 0
        for dept, count in dept_counts.items():
            if count < fair_share * 0.5:
                deficit = round(fair_share - count)
                items.append({
                    "type": "coverage_gap",
                    "severity": "high" if count < fair_share * 0.25 else "medium",
                    "department": dept,
                    "message": f"{dept} has {count} neurons ({round(count/total_neurons*100, 1)}% of total), well below fair share of ~{round(fair_share)}. Consider adding ~{deficit} neurons.",
                    "action": f"Review and ingest evidence targeting {dept} topics to grow coverage.",
                })

    if dept_eval_quality:
        neuron_evals = [d for d in dept_eval_quality if "neuron" in d["answer_mode"]]
        if len(neuron_evals) >= 2:
            avg_all = sum(d["avg_overall"] for d in neuron_evals) / len(neuron_evals)
            for d in neuron_evals:
                if d["avg_overall"] < avg_all - 0.5 and d["eval_count"] >= 3:
                    items.append({
                        "type": "quality_gap", "severity": "medium", "department": d["department"],
                        "message": f"{d['department']} neuron-assisted evals average {d['avg_overall']:.1f} vs system avg {avg_all:.1f} \u2014 content quality may need improvement.",
                        "action": f"Review and refine neuron content in {d['department']} department. Run targeted blind evals to confirm.",
                    })

    inv_values = [v for v in dept_invocations.values() if v > 0]
    if inv_values:
        median_inv = sorted(inv_values)[len(inv_values) // 2]
        for dept, inv in dept_invocations.items():
            if inv < median_inv * 0.1 and dept_counts.get(dept, 0) > 10:
                items.append({
                    "type": "utilization_gap", "severity": "low", "department": dept,
                    "message": f"{dept} has {dept_counts[dept]} neurons but only {inv} invocations (median is {median_inv}). Content may not match real queries.",
                    "action": f"Review {dept} neuron labels and summaries for relevance. Consider sample queries to test activation.",
                })

    return items


def _audit_fairness(coverage: dict, total_neurons: int, dept_eval_quality: list[dict]) -> dict:
    """Assemble fairness analysis section from coverage and eval quality data."""
    dept_invocations = coverage["dept_invocations"]
    dept_utility = coverage["dept_utility"]
    dept_cv = coverage["dept_cv"]

    inv_values = [v for v in dept_invocations.values() if v > 0]
    invocation_disparity = round(max(inv_values) / min(inv_values), 1) if len(inv_values) >= 2 and min(inv_values) > 0 else None

    dept_avg_utilities = [sum(dept_utility[d]) / len(dept_utility[d]) for d in dept_utility if dept_utility[d]]
    utility_range = round(max(dept_avg_utilities) - min(dept_avg_utilities), 4) if len(dept_avg_utilities) >= 2 else 0

    remediation_items = _audit_remediation(
        dept_cv, coverage["dept_counts"], dept_invocations, total_neurons, dept_eval_quality,
    )

    return {
        "department_eval_quality": dept_eval_quality,
        "invocation_disparity_ratio": invocation_disparity,
        "utility_range": utility_range,
        "coverage_cv": round(dept_cv, 3),
        "remediation_plan": remediation_items,
        "remediation_count": len(remediation_items),
        "fairness_pass": dept_cv <= 0.5 and len(remediation_items) == 0,
    }


async def run_compliance_audit(db: AsyncSession) -> dict:
    """Reusable compliance audit logic. Returns the full audit dict.

    Used by both the /compliance-audit endpoint and snapshot creation.
    Orchestrates per-check helpers for PII, bias, scoring, provenance,
    validity, and fairness.
    """
    result = await db.execute(select(Neuron).where(Neuron.is_active == True))
    neurons = result.scalars().all()
    total_neurons = len(neurons)

    pii_scan = _audit_pii_exposure(neurons)
    coverage = _audit_neuron_coverage(neurons, total_neurons)
    bias_assessment = await _audit_bias_assessment(db, coverage, total_neurons)
    scoring = await _audit_scoring_baselines(db)
    provenance = _audit_provenance(neurons)
    provenance["source_type_distribution"] = coverage["source_type_counts"]
    validity = await _audit_validity_reliability(db, scoring["all_signal_values"])
    dept_eval_quality = await _audit_dept_eval_quality(db)
    fairness = _audit_fairness(coverage, total_neurons, dept_eval_quality)

    # Remove internal-only key before returning
    scoring_baselines = {k: v for k, v in scoring.items() if k != "all_signal_values"}

    return {
        "total_neurons": total_neurons,
        "pii_scan": pii_scan,
        "bias_assessment": bias_assessment,
        "scoring_baselines": scoring_baselines,
        "provenance_audit": provenance,
        "validity_reliability": validity,
        "fairness_analysis": fairness,
    }


@router.get("/compliance-audit")
async def compliance_audit(db: AsyncSession = Depends(get_db)):
    """Comprehensive compliance audit covering MET-1 through MET-5, A007."""
    return await run_compliance_audit(db)


# ── Governance Dashboard: live metrics for AI objectives, change log, system health ──

async def _governance_overview(db: AsyncSession) -> dict:
    """Fetch total counts and quality KPIs for governance dashboard."""
    total_neurons = (await db.execute(select(func.count(Neuron.id)).where(Neuron.is_active == True))).scalar() or 0
    total_queries = (await db.execute(select(func.count(Query.id)))).scalar() or 0
    total_evals = (await db.execute(select(func.count(EvalScore.id)))).scalar() or 0
    total_refinements = (await db.execute(select(func.count(NeuronRefinement.id)))).scalar() or 0

    avg_eval = (await db.execute(select(func.avg(EvalScore.overall)))).scalar()
    avg_eval = round(float(avg_eval), 2) if avg_eval else None

    avg_faith = (await db.execute(select(func.avg(EvalScore.faithfulness)))).scalar()
    avg_faith = round(float(avg_faith), 2) if avg_faith else None

    avg_rating_result = await db.execute(
        select(func.avg(Query.user_rating)).where(Query.user_rating.isnot(None))
    )
    avg_rating = avg_rating_result.scalar()
    avg_rating = round(float(avg_rating), 2) if avg_rating else None
    rated_count = (await db.execute(
        select(func.count(Query.id)).where(Query.user_rating.isnot(None))
    )).scalar() or 0

    return {
        "totals": {
            "neurons": total_neurons, "queries": total_queries,
            "evaluations": total_evals, "refinements": total_refinements,
            "rated_queries": rated_count,
        },
        "quality": {
            "avg_eval": avg_eval, "avg_faith": avg_faith, "avg_rating": avg_rating,
        },
        "total_queries": total_queries,
    }


async def _governance_cost_kpis(db: AsyncSession, total_queries: int) -> dict:
    """Compute cost KPIs: total, per-query, per-1M tokens, run vs opus split."""
    total_cost = (await db.execute(select(func.sum(Query.cost_usd)))).scalar() or 0.0
    avg_cost = round(total_cost / total_queries, 6) if total_queries > 0 else 0.0
    total_tokens_result = await db.execute(select(
        func.sum(Query.classify_input_tokens + Query.execute_input_tokens
                 + Query.classify_output_tokens + Query.execute_output_tokens)
    ))
    total_tokens = total_tokens_result.scalar() or 0
    cost_per_1m = round(total_cost / total_tokens * 1_000_000, 2) if total_tokens > 0 else None

    slot_cost_result = await db.execute(text("""
        SELECT
            SUM(CASE WHEN (slot->>'mode') LIKE '%opus%' THEN (slot->>'cost_usd')::float ELSE 0 END) as opus_cost,
            SUM(CASE WHEN (slot->>'mode') LIKE '%opus%'
                THEN (slot->>'input_tokens')::float + (slot->>'output_tokens')::float ELSE 0 END) as opus_tokens,
            SUM(CASE WHEN (slot->>'mode') NOT LIKE '%opus%' THEN (slot->>'cost_usd')::float ELSE 0 END) as run_slot_cost,
            SUM(CASE WHEN (slot->>'mode') NOT LIKE '%opus%'
                THEN (slot->>'input_tokens')::float + (slot->>'output_tokens')::float ELSE 0 END) as run_slot_tokens
        FROM queries, jsonb_array_elements(queries.results_json::jsonb) AS slot
        WHERE queries.results_json IS NOT NULL
    """))
    opus_cost_total, opus_token_total, run_slot_cost, run_slot_tokens = slot_cost_result.one()
    opus_cost_1m = round(float(opus_cost_total) / float(opus_token_total) * 1_000_000, 2) if opus_token_total and opus_token_total > 0 else None

    classify_cost_result = await db.execute(text("""
        SELECT COALESCE(SUM(classify_input_tokens + classify_output_tokens), 0) FROM queries
    """))
    classify_tokens = float(classify_cost_result.scalar() or 0)
    classify_cost_est_result = await db.execute(text("""
        SELECT COALESCE(SUM(
            classify_input_tokens * 1.00 / 1000000.0 +
            classify_output_tokens * 5.00 / 1000000.0
        ), 0) FROM queries
    """))
    classify_cost_est = float(classify_cost_est_result.scalar() or 0)

    run_total_cost = (run_slot_cost or 0) + classify_cost_est
    run_total_tokens = (run_slot_tokens or 0) + classify_tokens
    run_cost_per_1m = round(run_total_cost / run_total_tokens * 1_000_000, 2) if run_total_tokens > 0 else None

    return {
        "avg_cost": avg_cost, "total_cost": round(total_cost, 4),
        "cost_per_1m": cost_per_1m, "run_cost_per_1m": run_cost_per_1m,
        "opus_cost_1m": opus_cost_1m,
    }


async def _governance_parity(db: AsyncSession, run_cost_per_1m, opus_cost_1m) -> dict:
    """Compute parity index and value score KPIs."""
    opus_eval_result = await db.execute(
        select(func.avg(EvalScore.overall)).where(EvalScore.answer_mode.like("opus_%"))
    )
    avg_opus_eval = opus_eval_result.scalar()

    neuron_eval_result = await db.execute(
        select(func.avg(EvalScore.overall)).where(EvalScore.answer_mode.like("%_neuron"))
    )
    avg_neuron_eval = neuron_eval_result.scalar()

    parity_index = round(float(avg_neuron_eval) / float(avg_opus_eval), 3) if avg_neuron_eval and avg_opus_eval else None
    value_score = None
    if avg_neuron_eval and run_cost_per_1m and opus_cost_1m and opus_cost_1m > 0:
        value_score = round((float(avg_neuron_eval) / 5.0) / (run_cost_per_1m / opus_cost_1m), 2)

    return {
        "parity_index": parity_index, "value_score": value_score,
        "avg_opus_eval": round(float(avg_opus_eval), 2) if avg_opus_eval else None,
        "avg_neuron_eval": round(float(avg_neuron_eval), 2) if avg_neuron_eval else None,
    }


async def _governance_compliance(db: AsyncSession) -> dict:
    """Fetch coverage CV, zero-hit rate, change activity, and alert count."""
    dept_count = (await db.execute(
        select(func.count(func.distinct(Neuron.department)))
        .where(Neuron.department.isnot(None), Neuron.is_active == True)
    )).scalar() or 0

    dept_neuron_counts = await db.execute(
        select(Neuron.department, func.count(Neuron.id))
        .where(Neuron.is_active == True, Neuron.department.isnot(None))
        .group_by(Neuron.department)
    )
    dept_vals = [row[1] for row in dept_neuron_counts.all()]
    if len(dept_vals) >= 2:
        d_mean = sum(dept_vals) / len(dept_vals)
        d_var = sum((v - d_mean) ** 2 for v in dept_vals) / (len(dept_vals) - 1)
        coverage_cv = round(math.sqrt(d_var) / d_mean, 3) if d_mean > 0 else 0.0
    else:
        coverage_cv = 0.0

    recent_q = await db.execute(
        select(Query.selected_neuron_ids).order_by(Query.id.desc()).limit(50)
    )
    recent_rows = recent_q.all()
    zero_hits = sum(1 for (ids,) in recent_rows if not ids or ids == "[]") if recent_rows else 0
    zero_hit_pct = round(zero_hits / len(recent_rows) * 100, 1) if recent_rows else 0

    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    recent_refinements = (await db.execute(
        select(func.count(NeuronRefinement.id)).where(NeuronRefinement.created_at >= thirty_days_ago)
    )).scalar() or 0
    recent_autopilot = (await db.execute(
        select(func.count(AutopilotRun.id)).where(AutopilotRun.created_at >= thirty_days_ago)
    )).scalar() or 0

    recent_changes_q = await db.execute(
        select(NeuronRefinement.id, NeuronRefinement.action, NeuronRefinement.field,
               NeuronRefinement.reason, NeuronRefinement.neuron_id, NeuronRefinement.created_at)
        .order_by(NeuronRefinement.created_at.desc()).limit(10)
    )
    recent_changes = [
        {"id": r[0], "action": r[1], "field": r[2], "reason": (r[3] or "")[:120],
         "neuron_id": r[4], "created_at": r[5].isoformat() if r[5] else None}
        for r in recent_changes_q.all()
    ]

    active_alert_count = (await db.execute(
        select(func.count(SystemAlert.id)).where(SystemAlert.acknowledged == False)
    )).scalar() or 0

    return {
        "dept_count": dept_count, "coverage_cv": coverage_cv,
        "zero_hit_pct": zero_hit_pct,
        "change_activity": {
            "refinements_30d": recent_refinements,
            "autopilot_runs_30d": recent_autopilot,
            "recent_changes": recent_changes,
        },
        "active_alerts": active_alert_count,
    }


@router.get("/governance-dashboard")
async def governance_dashboard(db: AsyncSession = Depends(get_db)):
    """Aggregate live governance metrics: AI objectives progress, change activity, quality trends.

    Feeds the Governance page with computed KPI status against defined targets.
    """
    overview = await _governance_overview(db)
    cost = await _governance_cost_kpis(db, overview["total_queries"])
    parity = await _governance_parity(db, cost["run_cost_per_1m"], cost["opus_cost_1m"])
    compliance = await _governance_compliance(db)

    return {
        "totals": {
            **overview["totals"],
            "departments": compliance["dept_count"],
        },
        "kpis": {
            "avg_eval_overall": overview["quality"]["avg_eval"],
            "avg_faithfulness": overview["quality"]["avg_faith"],
            "avg_user_rating": overview["quality"]["avg_rating"],
            "avg_cost_per_query": cost["avg_cost"],
            "total_cost_usd": cost["total_cost"],
            "cost_per_1m_tokens": cost["cost_per_1m"],
            "run_cost_per_1m": cost["run_cost_per_1m"],
            "zero_hit_pct": compliance["zero_hit_pct"],
            "parity_index": parity["parity_index"],
            "value_score": parity["value_score"],
            "avg_opus_eval": parity["avg_opus_eval"],
            "avg_neuron_eval": parity["avg_neuron_eval"],
            "opus_cost_per_1m": round(cost["opus_cost_1m"], 2) if cost["opus_cost_1m"] else None,
            "coverage_cv": compliance["coverage_cv"],
        },
        "change_activity": compliance["change_activity"],
        "active_alerts": compliance["active_alerts"],
    }
