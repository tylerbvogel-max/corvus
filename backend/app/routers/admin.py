"""Admin console endpoints: lifecycle, cost, queues, scoring health, alerts.

Record 04c (``durability-file-size-seams``) split three coherent jobs out of
this file, which held fourteen: source ingest went to ``admin_ingest``,
compliance and governance reporting to ``admin_compliance``, and the graph
maintenance sweeps — including this module's former DELETE path — to
``admin_graph_maintenance``. What stays is the operator console proper: seed
and reset, retention, checkpoints, cost, the emergent queue, citation
reference scanning, scoring health, health checks and alerts, concept
neurons, and bootstrap firings.

This module no longer deletes graph rows, and its entry in
``architecture/graph_writers.json`` was removed in the same change that
registered its replacement.
"""

import asyncio
import json
import math
import os
from datetime import datetime
from types import MappingProxyType

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    Neuron, Query, NeuronFiring, NeuronEdge, PropagationLog, IntentNeuronMap,
    SystemState, EmergentQueue, SystemAlert, EvalScore,
)
from app.schemas import (
    SeedResponse, ResetResponse, CostReportResponse, CheckpointResponse,
)
from app.seed.loader import load_seed
from app.middleware.rbac import UserIdentity, require_role

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/seed", response_model=SeedResponse)
async def seed_database(force: bool = False, db: AsyncSession = Depends(get_db)):
    """Seed the neuron graph with initial data; pass force=true to re-seed."""
    result = await load_seed(db, force=force)
    return SeedResponse(**result)


@router.post("/reset", response_model=ResetResponse)
async def reset_firings(db: AsyncSession = Depends(get_db)):
    """Clear firing history, co-firing edges, and query data. Keep neuron definitions."""
    await db.execute(delete(PropagationLog))
    await db.execute(delete(NeuronFiring))
    await db.execute(delete(NeuronEdge))
    await db.execute(delete(IntentNeuronMap))
    await db.execute(delete(Query))

    # Reset system state
    state = (await db.execute(select(SystemState).where(SystemState.id == 1))).scalar_one_or_none()
    if state:
        state.global_token_counter = 0
        state.total_queries = 0

    # Reset neuron invocations, utility, and weak edges
    neurons = await db.execute(select(Neuron))
    for neuron in neurons.scalars():
        neuron.invocations = 0
        neuron.avg_utility = 0.5
        neuron.is_active = True
        neuron.weak_edges = None

    await db.commit()

    # Invalidate caches since edges and firings were cleared
    from app.services.adjacency_cache import invalidate_adjacency_cache
    invalidate_adjacency_cache()

    return ResetResponse(status="reset_complete")


@router.post("/retention/purge")
async def retention_purge_now(db: AsyncSession = Depends(get_db)):
    """Run the query-telemetry retention purge now (fwd-tier1, FedRAMP AU-11).

    Honors settings.query_retention_days — returns status "disabled" (and
    purges nothing) when the retention window is 0/unset. Audit-bearing
    artifacts are detached, never deleted; see services/retention.py.
    """
    from app.services.retention import run_retention_purge
    return await run_retention_purge(db)


@router.post("/checkpoint", response_model=CheckpointResponse)
async def create_checkpoint(db: AsyncSession = Depends(get_db)):
    """Export all neurons to a JSON checkpoint file and commit it."""
    result = await db.execute(select(Neuron).order_by(Neuron.id))
    neurons = result.scalars().all()

    data = [
        {
            "id": n.id,
            "parent_id": n.parent_id,
            "layer": n.layer,
            "node_type": n.node_type,
            "label": n.label,
            "content": n.content,
            "summary": n.summary,
            "department": n.department,
            "role_key": n.role_key,
            "invocations": n.invocations,
            "avg_utility": n.avg_utility,
            "is_active": n.is_active,
            "created_at_query_count": n.created_at_query_count,
        }
        for n in neurons
    ]

    # Write checkpoint file
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    checkpoint_dir = os.path.join(backend_dir, "checkpoints")
    os.makedirs(checkpoint_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"neurons_{timestamp}.json"
    filepath = os.path.join(checkpoint_dir, filename)

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)

    # Git add + commit from the backend directory
    proc = await asyncio.create_subprocess_exec(
        "git", "add", "checkpoints/",
        cwd=backend_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    proc = await asyncio.create_subprocess_exec(
        "git", "commit", "-m", f"checkpoint: {filename} ({len(data)} neurons)",
        cwd=backend_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    # Get commit SHA
    proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "HEAD",
        cwd=backend_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    commit_sha = stdout.decode().strip()

    return CheckpointResponse(
        status="ok",
        filename=filename,
        neuron_count=len(data),
        commit_sha=commit_sha,
    )


@router.get("/cost-report", response_model=CostReportResponse)
async def cost_report(db: AsyncSession = Depends(get_db)):
    """Return aggregate token usage and cost statistics across all queries."""
    total_queries = (await db.execute(select(func.count(Query.id)))).scalar() or 0
    total_cost = (await db.execute(select(func.sum(Query.cost_usd)))).scalar() or 0.0
    total_input = (await db.execute(
        select(
            func.sum(Query.classify_input_tokens) + func.sum(Query.execute_input_tokens)
        )
    )).scalar() or 0
    total_output = (await db.execute(
        select(
            func.sum(Query.classify_output_tokens) + func.sum(Query.execute_output_tokens)
        )
    )).scalar() or 0

    from app.services.model_usage_ledger import maintenance_cost_breakdown
    maintenance = maintenance_cost_breakdown()
    maint_total = maintenance["total_equivalent_usd"]

    return CostReportResponse(
        total_queries=total_queries,
        total_cost_usd=round(total_cost, 6),
        avg_cost_per_query=round(total_cost / total_queries, 6) if total_queries > 0 else 0.0,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        maintenance_cost_usd=maint_total,
        maintenance_per_query_usd=round(maint_total / total_queries, 6) if total_queries > 0 else 0.0,
        maintenance_by_workload=maintenance["by_workload"],
        maintenance_since=maintenance["ledger_started_at"],
    )


@router.get("/emergent-queue")
async def get_emergent_queue(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Get emergent queue entries, optionally filtered by status."""
    stmt = select(EmergentQueue).order_by(EmergentQueue.detection_count.desc())
    if status:
        stmt = stmt.where(EmergentQueue.status == status)
    result = await db.execute(stmt)
    entries = result.scalars().all()

    return {
        "total": len(entries),
        "entries": [
            {
                "id": e.id,
                "citation_pattern": e.citation_pattern,
                "domain": e.domain,
                "family": e.family,
                "detection_count": e.detection_count,
                "first_detected_at": e.first_detected_at.isoformat() if e.first_detected_at else None,
                "last_detected_at": e.last_detected_at.isoformat() if e.last_detected_at else None,
                "detected_in_neuron_ids": json.loads(e.detected_in_neuron_ids or "[]"),
                "detected_in_query_ids": json.loads(e.detected_in_query_ids or "[]"),
                "status": e.status,
                "resolved_neuron_id": e.resolved_neuron_id,
                "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
                "notes": e.notes,
            }
            for e in entries
        ],
    }


@router.post("/emergent-queue/{entry_id}/dismiss")
async def dismiss_queue_entry(
    entry_id: int,
    notes: str = "",
    db: AsyncSession = Depends(get_db),
):
    """Dismiss an emergent queue entry with a reason."""
    entry = await db.get(EmergentQueue, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Queue entry not found")
    entry.status = "dismissed"
    entry.notes = notes
    await db.commit()
    return {"status": "dismissed", "id": entry_id}


def _build_citation_lookup(neurons) -> dict[str, int]:
    citation_lookup: dict[str, int] = {}
    for n in neurons:
        if n.citation and n.source_type in ("regulatory_primary", "technical_primary"):
            citation_lookup[n.citation] = n.id
    return citation_lookup


def _resolve_reference(ref: dict, citation_lookup: dict[str, int]) -> int | None:
    for cit, nid in citation_lookup.items():
        if ref["pattern"] in cit or cit in ref["pattern"]:
            return nid
    return None


async def _enqueue_unresolved_reference(
    db: AsyncSession, ref: dict, neuron_id: int,
) -> tuple[int, int]:
    existing = (await db.execute(
        select(EmergentQueue).where(EmergentQueue.citation_pattern == ref["pattern"])
    )).scalar_one_or_none()

    if existing:
        if existing.status != "resolved":
            existing.detection_count += 1
            existing.last_detected_at = datetime.now()
            ids = json.loads(existing.detected_in_neuron_ids or "[]")
            if neuron_id not in ids:
                ids.append(neuron_id)
                existing.detected_in_neuron_ids = json.dumps(ids)
            return 0, 1
        return 0, 0

    db.add(EmergentQueue(
        citation_pattern=ref["pattern"],
        domain=ref["domain"],
        family=ref["family"],
        detection_count=1,
        detected_in_neuron_ids=json.dumps([neuron_id]),
    ))
    return 1, 0


async def _process_neuron_references(
    db: AsyncSession, neuron, refs: list[dict], citation_lookup: dict[str, int],
    family_counts: dict[str, int],
) -> tuple[int, int, int, int]:
    resolved = 0
    unresolved = 0
    new_queue = 0
    incremented_queue = 0

    for ref in refs:
        matched_id = _resolve_reference(ref, citation_lookup)
        if matched_id:
            ref["resolved_neuron_id"] = matched_id
            ref["resolved_at"] = datetime.now().isoformat()
            resolved += 1
        else:
            unresolved += 1
            family_counts[ref["family"]] = family_counts.get(ref["family"], 0) + 1
            nq, iq = await _enqueue_unresolved_reference(db, ref, neuron.id)
            new_queue += nq
            incremented_queue += iq

    neuron.external_references = json.dumps(refs)
    return resolved, unresolved, new_queue, incremented_queue


@router.post("/scan-references")
async def scan_references(db: AsyncSession = Depends(get_db)):
    """Retroactive scan: detect external references in all neurons and seed the emergent queue."""
    from app.services.reference_detector import detect_neuron_references

    result = await db.execute(select(Neuron).where(Neuron.is_active == True))
    neurons = result.scalars().all()

    neurons_scanned = 0
    neurons_with_refs = 0
    total_refs = 0
    resolved = 0
    unresolved = 0
    new_queue = 0
    incremented_queue = 0
    family_counts: dict[str, int] = {}
    citation_lookup = _build_citation_lookup(neurons)

    for neuron in neurons:
        neurons_scanned += 1
        refs = detect_neuron_references(neuron.content, neuron.summary)
        if not refs:
            neuron.external_references = None
            continue

        neurons_with_refs += 1
        total_refs += len(refs)
        r, u, nq, iq = await _process_neuron_references(
            db, neuron, refs, citation_lookup, family_counts,
        )
        resolved += r
        unresolved += u
        new_queue += nq
        incremented_queue += iq

    await db.commit()

    top_families = sorted(family_counts.items(), key=lambda x: -x[1])[:10]

    return {
        "neurons_scanned": neurons_scanned,
        "neurons_with_references": neurons_with_refs,
        "total_references_found": total_refs,
        "resolved": resolved,
        "unresolved": unresolved,
        "new_queue_entries": new_queue,
        "existing_queue_entries_incremented": incremented_queue,
        "top_unresolved_families": [
            {"family": f, "count": c} for f, c in top_families
        ],
    }




from app.services.scoring_engine import SCORING_SIGNALS as _SCORING_SIGNALS


def _stats(values: list[float]) -> dict:
    """Compute mean, stddev, min, max, count for a list of floats."""
    if not values:
        return {"mean": 0, "stddev": 0, "min": 0, "max": 0, "count": 0}
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / max(1, n - 1)
    return {
        "mean": round(mean, 4),
        "stddev": round(math.sqrt(variance), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "count": n,
    }


def _aggregate_signal(queries: list[dict], signal: str) -> list[float]:
    """Collect all neuron-level values for a signal across queries."""
    vals: list[float] = []
    for q in queries:
        vals.extend(q["signals"].get(signal, []))
    return vals


def _query_means(queries: list[dict], signal: str) -> list[float]:
    """Per-query mean for a signal."""
    means: list[float] = []
    for q in queries:
        vals = q["signals"].get(signal, [])
        if vals:
            means.append(sum(vals) / len(vals))
    return means


async def _reference_neuron_ids(db: AsyncSession) -> set[int]:
    """Ids of reference-class neurons (mind-reference-class), matched on
    all three class axes like reference_exclusion_filters. Scoring-health
    drift assumes ORGANIC corpus growth; bulk document ingest (and bulk
    revocation) violate that assumption, so reference-class neurons are
    segmented out of the headline signal population and reported in
    their own Library block instead."""
    from sqlalchemy import or_
    rows = (await db.execute(
        select(Neuron.id).where(or_(
            Neuron.source_origin == "document",
            Neuron.node_type.in_(("reference", "document")),
            Neuron.department == "Library",
        ))
    )).scalars().all()
    return set(rows)


def _parse_query_scores(
    rows, signals: list[str], neuron_ids: set[int] | None = None,
    exclude: bool = True,
) -> list[dict]:
    """Parse raw query rows into structured score dicts with per-signal values.

    neuron_ids segments by population: exclude=True keeps neurons NOT in
    the set (experiential headline); exclude=False keeps ONLY the set
    (the Library block). None = no filtering (legacy behavior).
    """
    query_scores: list[dict] = []
    for qid, scores_json, created_at in rows:
        try:
            scores = json.loads(scores_json) if scores_json else []
        except json.JSONDecodeError:
            continue
        if not scores:
            continue
        signal_values: dict[str, list[float]] = {s: [] for s in signals}
        for neuron_score in scores:
            if neuron_ids is not None:
                member = neuron_score.get("neuron_id") in neuron_ids
                if member == exclude:
                    continue
            for s in signals:
                val = neuron_score.get(s)
                if val is not None:
                    signal_values[s].append(float(val))
        if not any(signal_values[s] for s in signals):
            continue  # a query with no surviving population tells us nothing
        query_scores.append({
            "query_id": qid,
            "created_at": created_at.isoformat() if created_at else None,
            "signals": signal_values,
        })
    return query_scores


def _scoring_distribution(
    query_scores: list[dict],
    recent_window: int,
    baseline_window: int,
    drift_threshold: float,
) -> tuple[dict, list[dict]]:
    """Build per-signal distribution stats and detect drift. Returns (signals_report, drift_alerts)."""
    total = len(query_scores)
    if total <= recent_window:
        baseline_qs = query_scores
        recent_qs = query_scores
        can_detect_drift = False
    else:
        recent_qs = query_scores[-recent_window:]
        baseline_start = max(0, total - recent_window - baseline_window)
        baseline_qs = query_scores[baseline_start:total - recent_window]
        can_detect_drift = len(baseline_qs) >= 5

    signals_report: dict = {}
    drift_alerts: list[dict] = []

    for sig in _SCORING_SIGNALS:
        baseline_vals = _aggregate_signal(baseline_qs, sig)
        recent_vals = _aggregate_signal(recent_qs, sig)
        baseline_means = _query_means(baseline_qs, sig)
        recent_means = _query_means(recent_qs, sig)

        b_stats = _stats(baseline_vals)
        r_stats = _stats(recent_vals)
        bm_stats = _stats(baseline_means)
        rm_stats = _stats(recent_means)

        drifted = False
        z_score = 0.0
        if can_detect_drift and bm_stats["stddev"] > 0.001 and len(recent_means) >= 3:
            z_score = (rm_stats["mean"] - bm_stats["mean"]) / bm_stats["stddev"]
            drifted = abs(z_score) > drift_threshold

        signals_report[sig] = {
            "baseline": b_stats, "recent": r_stats,
            "baseline_query_means": bm_stats, "recent_query_means": rm_stats,
            "z_score": round(z_score, 3), "drifted": drifted,
        }

        if drifted:
            direction = "increased" if z_score > 0 else "decreased"
            drift_alerts.append({
                "signal": sig, "direction": direction, "z_score": round(z_score, 3),
                "baseline_mean": bm_stats["mean"], "recent_mean": rm_stats["mean"],
                "message": f"{sig} has {direction} significantly (z={z_score:.1f}): "
                           f"baseline \u03bc={bm_stats['mean']:.3f} \u2192 recent \u03bc={rm_stats['mean']:.3f}",
            })

    return signals_report, drift_alerts, can_detect_drift


def _scoring_timeline(query_scores: list[dict]) -> list[dict]:
    """Build per-query timeline for charting (last 50 queries)."""
    timeline_qs = query_scores[-50:]
    per_query_timeline: list[dict] = []
    for q in timeline_qs:
        entry: dict = {"query_id": q["query_id"], "created_at": q["created_at"]}
        for sig in _SCORING_SIGNALS:
            vals = q["signals"].get(sig, [])
            entry[sig] = round(sum(vals) / len(vals), 4) if vals else 0
        per_query_timeline.append(entry)
    return per_query_timeline


@router.get("/scoring-health")
async def scoring_health(
    baseline_window: int = 50,
    recent_window: int = 20,
    drift_threshold: float = 2.0,
    db: AsyncSession = Depends(get_db),
):
    """Compute per-signal scoring distribution stats and detect drift.

    Compares the most recent `recent_window` queries against a trailing
    `baseline_window` (the queries just before the recent window).
    Drift is flagged when the recent mean deviates by more than
    `drift_threshold` standard deviations from the baseline mean.
    """
    needed = baseline_window + recent_window
    result = await db.execute(
        select(Query.id, Query.neuron_scores_json, Query.created_at)
        .where(Query.neuron_scores_json.isnot(None))
        .order_by(Query.id.desc())
        .limit(needed)
    )
    rows = result.all()

    if len(rows) < 5:
        return {
            "status": "insufficient_data",
            "queries_available": len(rows),
            "minimum_required": 5,
            "signals": {},
            "drift_alerts": [],
            "per_query_timeline": [],
        }

    # Segment by origination (mind-reference-class): the headline signals
    # and drift alerts cover the EXPERIENTIAL population only — organic
    # growth is what the z-test assumes. Reference-class (Library) neurons
    # arrive and leave in bulk (document ingest / revocation), so they get
    # their own stats block and never trip drift alerts.
    ref_ids = await _reference_neuron_ids(db)
    query_scores = _parse_query_scores(rows, _SCORING_SIGNALS, ref_ids or None)
    query_scores.reverse()  # chronological order (oldest first)
    total = len(query_scores)

    signals_report, drift_alerts, can_detect_drift = _scoring_distribution(
        query_scores, recent_window, baseline_window, drift_threshold,
    )

    library_block = None
    if ref_ids:
        lib_scores = _parse_query_scores(
            rows, _SCORING_SIGNALS, ref_ids, exclude=False)
        lib_scores.reverse()
        if lib_scores:
            lib_signals, _lib_alerts, _ = _scoring_distribution(
                lib_scores, recent_window, baseline_window, drift_threshold)
            library_block = {
                "queries_with_reference_hits": len(lib_scores),
                "signals": lib_signals,
                "note": ("reference-class population (document-ingested); "
                         "growth is curated, not organic — informational "
                         "only, never drift-alerted"),
            }

    return {
        "status": "ok",
        "queries_analyzed": total,
        "baseline_window": min(baseline_window, total),
        "recent_window": min(recent_window, total),
        "can_detect_drift": can_detect_drift,
        "drift_threshold": drift_threshold,
        "signals": signals_report,
        "drift_alerts": drift_alerts,
        "per_query_timeline": _scoring_timeline(query_scores),
        "segmentation": {"reference_neurons": len(ref_ids),
                         "library": library_block},
    }


# ── Health Check: automated drift alerting + circuit breaker + quality monitoring ──

# Go/no-go thresholds (configurable)
CIRCUIT_BREAKER_THRESHOLDS = MappingProxyType({
    "min_avg_eval_overall": 2.5,       # Below this → circuit breaker trips
    "min_avg_user_rating": 0.3,        # Below this → circuit breaker trips
    "max_zero_hit_pct": 0.40,          # >40% zero-hit queries → warning
    "drift_z_threshold": 2.0,          # Z-score threshold for drift alerts
    "eval_window": 20,                 # Number of recent queries to evaluate
})


async def _maybe_create_alert(
    db: AsyncSession, alert_type: str, severity: str, message: str,
    detail_json: str, signal: str | None = None,
) -> dict | None:
    """Create a SystemAlert if no unacknowledged alert of this type (+signal) exists."""
    filters = [SystemAlert.alert_type == alert_type, SystemAlert.acknowledged == False]
    if signal is not None:
        filters.append(SystemAlert.signal == signal)
    existing = await db.execute(select(SystemAlert).where(*filters))
    if existing.scalar_one_or_none():
        return None
    alert = SystemAlert(
        alert_type=alert_type, severity=severity, signal=signal,
        message=message, detail_json=detail_json,
    )
    db.add(alert)
    result = {"type": alert_type, "message": message}
    if signal is not None:
        result["signal"] = signal
    return result


async def _health_database(db: AsyncSession, window: int) -> dict:
    """Check eval quality and user ratings against circuit breaker thresholds."""
    eval_result = await db.execute(
        select(EvalScore.overall).order_by(EvalScore.id.desc()).limit(int(window))
    )
    eval_scores = [row[0] for row in eval_result.all()]
    avg_eval = sum(eval_scores) / len(eval_scores) if eval_scores else None

    rating_result = await db.execute(
        select(Query.user_rating).where(Query.user_rating.isnot(None))
        .order_by(Query.id.desc()).limit(int(window))
    )
    ratings = [row[0] for row in rating_result.all()]
    avg_rating = sum(ratings) / len(ratings) if ratings else None

    tripped = False
    reasons: list[str] = []
    alerts: list[dict] = []

    if avg_eval is not None and avg_eval < CIRCUIT_BREAKER_THRESHOLDS["min_avg_eval_overall"]:
        tripped = True
        reasons.append(f"avg eval overall {avg_eval:.2f} < {CIRCUIT_BREAKER_THRESHOLDS['min_avg_eval_overall']}")
        created = await _maybe_create_alert(
            db, "quality_drop", "critical",
            f"Average eval overall dropped to {avg_eval:.2f} (threshold: {CIRCUIT_BREAKER_THRESHOLDS['min_avg_eval_overall']})",
            json.dumps({"avg_eval": avg_eval, "window": len(eval_scores)}),
        )
        if created:
            alerts.append(created)

    if avg_rating is not None and avg_rating < CIRCUIT_BREAKER_THRESHOLDS["min_avg_user_rating"]:
        tripped = True
        reasons.append(f"avg user rating {avg_rating:.2f} < {CIRCUIT_BREAKER_THRESHOLDS['min_avg_user_rating']}")

    return {
        "tripped": tripped, "reasons": reasons, "alerts": alerts,
        "avg_eval": avg_eval, "eval_count": len(eval_scores),
        "avg_rating": avg_rating, "rating_count": len(ratings),
    }


async def _health_pipeline(db: AsyncSession, window: int) -> dict:
    """Check zero-hit rate and API model version changes."""
    recent_queries = await db.execute(
        select(Query.selected_neuron_ids).order_by(Query.id.desc()).limit(int(window))
    )
    rows = recent_queries.all()
    reasons: list[str] = []
    if rows:
        zero_hits = sum(1 for (ids,) in rows if not ids or ids == "[]")
        zero_pct = zero_hits / len(rows)
        if zero_pct > CIRCUIT_BREAKER_THRESHOLDS["max_zero_hit_pct"]:
            reasons.append(f"zero-hit rate {zero_pct:.0%} > {CIRCUIT_BREAKER_THRESHOLDS['max_zero_hit_pct']:.0%}")

    version_result = await db.execute(
        select(Query.model_version).where(Query.model_version.isnot(None))
        .order_by(Query.id.desc()).limit(int(window))
    )
    versions = [row[0] for row in version_result.all()]
    unique_versions = list(set(versions)) if versions else []
    model_version_changed = len(unique_versions) > 1

    alerts: list[dict] = []
    if model_version_changed:
        created = await _maybe_create_alert(
            db, "api_change", "info",
            f"Multiple model versions detected in recent queries: {', '.join(unique_versions)}",
            json.dumps({"versions": unique_versions}),
        )
        if created:
            alerts.append(created)

    return {
        "reasons": reasons, "alerts": alerts,
        "model_versions": unique_versions, "model_version_changed": model_version_changed,
    }


async def _health_graph(db: AsyncSession) -> tuple[list[dict], dict]:
    """Run drift detection via scoring_health and create alerts for drifted signals."""
    drift_result = await scoring_health(db=db)
    alerts: list[dict] = []
    if drift_result.get("status") == "ok" and drift_result.get("drift_alerts"):
        for da in drift_result["drift_alerts"]:
            created = await _maybe_create_alert(
                db, "drift", "warning", da["message"],
                json.dumps(da), signal=da["signal"],
            )
            if created:
                alerts.append(created)
    return alerts, drift_result


async def _fetch_active_alerts(db: AsyncSession) -> list[dict]:
    """Fetch all unacknowledged system alerts."""
    result = await db.execute(
        select(SystemAlert).where(SystemAlert.acknowledged == False)
        .order_by(SystemAlert.created_at.desc())
    )
    return [
        {
            "id": a.id, "type": a.alert_type, "severity": a.severity,
            "signal": a.signal, "message": a.message,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in result.scalars().all()
    ]


@router.get("/health-check")
async def health_check(db: AsyncSession = Depends(get_db)):
    """Production health check: drift detection, quality monitoring, circuit breaker.

    Runs the scoring drift computation, checks eval quality and user ratings,
    detects API model version changes, and persists alerts to system_alerts.
    Returns go/no-go status.
    """
    window = CIRCUIT_BREAKER_THRESHOLDS["eval_window"]
    alerts_created: list[dict] = []

    graph_alerts, drift_result = await _health_graph(db)
    alerts_created.extend(graph_alerts)

    db_health = await _health_database(db, window)
    alerts_created.extend(db_health["alerts"])

    pipeline_health = await _health_pipeline(db, window)
    alerts_created.extend(pipeline_health["alerts"])

    reasons = db_health["reasons"] + pipeline_health["reasons"]
    circuit_breaker_tripped = db_health["tripped"] or bool(pipeline_health["reasons"])

    if circuit_breaker_tripped:
        created = await _maybe_create_alert(
            db, "circuit_breaker", "critical",
            f"Circuit breaker tripped: {'; '.join(reasons)}",
            json.dumps({"reasons": reasons}),
        )
        if created:
            alerts_created.append(created)

    await db.commit()
    active_alerts = await _fetch_active_alerts(db)

    return {
        "status": "tripped" if circuit_breaker_tripped else "ok",
        "circuit_breaker_tripped": circuit_breaker_tripped,
        "reasons": reasons,
        "avg_eval_overall": round(db_health["avg_eval"], 3) if db_health["avg_eval"] is not None else None,
        "avg_user_rating": round(db_health["avg_rating"], 3) if db_health["avg_rating"] is not None else None,
        "eval_count": db_health["eval_count"],
        "rating_count": db_health["rating_count"],
        "model_versions": pipeline_health["model_versions"],
        "model_version_changed": pipeline_health["model_version_changed"],
        "drift_alerts_count": len(drift_result.get("drift_alerts", [])),
        "active_alerts": active_alerts,
        "new_alerts": alerts_created,
        "thresholds": CIRCUIT_BREAKER_THRESHOLDS,
    }


@router.get("/alerts")
async def get_alerts(
    include_acknowledged: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Get system alerts, optionally including acknowledged ones."""
    q = select(SystemAlert).order_by(SystemAlert.created_at.desc())
    if not include_acknowledged:
        q = q.where(SystemAlert.acknowledged == False)
    result = await db.execute(q.limit(100))
    return [
        {
            "id": a.id,
            "type": a.alert_type,
            "severity": a.severity,
            "signal": a.signal,
            "message": a.message,
            "detail": json.loads(a.detail_json) if a.detail_json else None,
            "acknowledged": a.acknowledged,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in result.scalars().all()
    ]


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: int, db: AsyncSession = Depends(get_db)):
    """Acknowledge (dismiss) a system alert."""
    alert = await db.get(SystemAlert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.acknowledged = True
    alert.acknowledged_at = datetime.utcnow()
    await db.commit()
    return {"status": "acknowledged", "alert_id": alert_id}


@router.post("/alerts/acknowledge-all")
async def acknowledge_all_alerts(db: AsyncSession = Depends(get_db)):
    """Acknowledge all active alerts."""
    result = await db.execute(
        select(SystemAlert).where(SystemAlert.acknowledged == False)
    )
    count = 0
    for alert in result.scalars().all():
        alert.acknowledged = True
        alert.acknowledged_at = datetime.utcnow()
        count += 1
    await db.commit()
    return {"status": "acknowledged", "count": count}


@router.post("/seed-inhibitory-regulators")
async def seed_inhibitory_regulators_endpoint(db: AsyncSession = Depends(get_db)):
    """Create inhibitory regulators (GABAergic interneurons) for each department and high-volume role."""
    from app.services.inhibitory_service import seed_inhibitory_regulators
    created = await seed_inhibitory_regulators(db)
    await db.commit()
    return {"created": created, "message": f"Seeded {created} inhibitory regulators"}


@router.get("/inhibitory-regulators")
async def list_inhibitory_regulators(db: AsyncSession = Depends(get_db)):
    """List all inhibitory regulators with their stats."""
    from app.models import InhibitoryRegulator
    result = await db.execute(select(InhibitoryRegulator).order_by(InhibitoryRegulator.region_type, InhibitoryRegulator.region_value))
    regulators = result.scalars().all()
    return [
        {
            "id": r.id,
            "region_type": r.region_type,
            "region_value": r.region_value,
            "inhibition_strength": r.inhibition_strength,
            "activation_threshold": r.activation_threshold,
            "max_survivors": r.max_survivors,
            "redundancy_cosine_threshold": r.redundancy_cosine_threshold,
            "total_suppressions": r.total_suppressions,
            "total_activations": r.total_activations,
            "avg_post_suppression_utility": r.avg_post_suppression_utility,
            "is_active": r.is_active,
        }
        for r in regulators
    ]


# ── Concept Neurons ──


@router.get("/concept-neurons")
async def list_concept_neurons(db: AsyncSession = Depends(get_db)):
    """List all concept neurons with their instantiation edge counts."""
    from app.services.concept_service import get_concept_neurons
    return await get_concept_neurons(db)


@router.post("/concept-neurons")
async def create_concept_neuron_endpoint(
    label: str,
    content: str,
    summary: str | None = None,
    db: AsyncSession = Depends(get_db),
    identity: UserIdentity = Depends(require_role("admin")),
):
    """Create a new concept neuron (layer=-1, department-agnostic)."""
    from app.services.concept_service import create_concept_neuron
    from app.services.semantic_prefilter import update_cache_incremental

    neuron = await create_concept_neuron(
        db, label, content, summary,
        actor=identity, actor_type="user",
    )
    await db.commit()
    await update_cache_incremental(db, [neuron.id])

    return {
        "id": neuron.id,
        "label": neuron.label,
        "node_type": neuron.node_type,
        "layer": neuron.layer,
        "message": f"Created concept neuron #{neuron.id}: {neuron.label}",
    }


@router.post("/concept-neurons/{concept_id}/link")
async def link_concept_neuron(
    concept_id: int,
    target_ids: list[int],
    weight: float = 0.5,
    db: AsyncSession = Depends(get_db),
    identity: UserIdentity = Depends(require_role("admin")),
):
    """Create instantiation edges from a concept neuron to target neurons."""
    from app.services.concept_service import link_concept_to_neurons

    # Verify concept neuron exists
    concept = await db.get(Neuron, concept_id)
    if not concept or concept.node_type != "concept":
        raise HTTPException(status_code=404, detail=f"Concept neuron #{concept_id} not found")

    count = await link_concept_to_neurons(
        db, concept_id, target_ids, weight,
        concept_label=concept.label,
        actor=identity, actor_type="user",
    )
    await db.commit()
    from app.services.adjacency_cache import invalidate_adjacency_cache
    invalidate_adjacency_cache()

    return {
        "concept_id": concept_id,
        "edges_created": count,
        "message": f"Linked {count} neurons to concept #{concept_id}",
    }


@router.post("/concept-neurons/seed-three-horizons")
async def seed_three_horizons_endpoint(db: AsyncSession = Depends(get_db)):
    """Seed the Three Horizons framework as a concept neuron with instantiation edges."""
    from app.services.concept_service import seed_three_horizons
    return await seed_three_horizons(db)


@router.post("/concept-neurons/seed-all")
async def seed_all_concepts_endpoint(db: AsyncSession = Depends(get_db)):
    """Seed all concept neurons from the built-in registry (idempotent — skips existing)."""
    from app.services.concept_service import seed_all_concepts
    return await seed_all_concepts(db)


@router.post("/concept-neurons/relink")
async def relink_concepts_endpoint(db: AsyncSession = Depends(get_db)):
    """Re-run pattern matching for all existing concept neurons.

    Catches neurons missed by original seeding (e.g., label-only matches).
    Uses upsert — existing edges keep their weight if higher.
    """
    from app.services.concept_service import relink_existing_concepts
    return await relink_existing_concepts(db)


@router.post("/bootstrap-firings")
async def bootstrap_firings_endpoint(dry_run: bool = False, db: AsyncSession = Depends(get_db)):
    """Pre-seed co-firing edges and invocation estimates based on structural/semantic priors.

    All edges tagged source='bootstrap' for traceability.
    Pass dry_run=true to preview without writing.
    """
    from app.services.bootstrap_service import bootstrap_firings
    return await bootstrap_firings(db, dry_run=dry_run)


@router.get("/bootstrap-stats")
async def bootstrap_stats_endpoint(db: AsyncSession = Depends(get_db)):
    """Return statistics about bootstrap vs organic edge provenance."""
    from app.services.bootstrap_service import get_bootstrap_stats
    return await get_bootstrap_stats(db)


@router.post("/purge-bootstrap")
async def purge_bootstrap_endpoint(db: AsyncSession = Depends(get_db)):
    """Remove all bootstrap-sourced edges and reset bootstrap-only invocations.

    Use if bootstrap priors are causing unwanted bias.
    """
    from app.services.bootstrap_service import purge_bootstrap
    return await purge_bootstrap(db)


@router.post("/intra-department-bridge")
async def intra_department_bridge_endpoint(
    similarity_threshold: float = 0.45,
    dry_run: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Bridge role clusters within the same department using embedding similarity.

    Creates cross-role edges where neurons are semantically similar but lack
    connections due to role boundary isolation. Fixes intra-department
    fragmentation (e.g., Finance/cost_estimator isolated from Finance/financial_analyst).

    Args:
        similarity_threshold: Cosine similarity cutoff (0-1). Lower = more edges. Default 0.45.
        dry_run: Preview without writing.
    """
    from app.services.bootstrap_service import intra_department_bridge
    return await intra_department_bridge(db, similarity_threshold=similarity_threshold, dry_run=dry_run)
