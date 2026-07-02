"""Horizontal reconciler (plat-reconciler) — the cross-region integrity loop.

The differentiator: a matrix org's silos coordinate through pyramidal
(cross-region) edges, and discrepancies live at exactly those seams. The
reconciler is a specialized deterministic loop — NOT a harness, NOT an
inter-agent message bus (the shared graph IS the coordination medium) —
that hunts four seam pathologies:

1. cross-region contradictions  — similar content, different regions,
   incompatible assertions (bounded LLM verdict on a shortlist)
2. staleness divergence         — a pyramidal edge whose endpoints'
   verification dates diverged (one silo updated, the other still cites it)
3. homonyms vs synonyms         — the failure mode of one shared embedding
   space: "tolerance" means three things to Design / Mfg / HR. Same concept
   -> link (good coordination); false friend -> keep separate (else spread
   activation pollutes recall)
4. seam coverage gaps           — regions that co-fire constantly with no
   shared coordination node between them

Governance-safe by construction: the reconciler runs with privileged
internal read, but its OUTPUT is findings routed to the owning region's
controller (IntegrityFinding.region). It never auto-edits authoritative
knowledge — resolutions convert to proposals through the existing integrity
proposal path, which obeys the tiered write gate.
"""

import json
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import IntegrityFinding, IntegrityScan
from app.services.integrity import IntegrityFindingData, IntegrityScanResult
from app.services.integrity.similarity import (
    SimilarPair, compute_pairwise_similarity, extract_pairs_above_threshold,
    extract_pairs_in_range, load_neuron_embeddings,
)

# Homonym/synonym judge: intent + output contract documented per NPR-9.
_HOMONYM_SYSTEM_PROMPT = """You are disambiguating pairs of knowledge base entries that live in \
DIFFERENT organizational regions but are close in embedding space.
For each pair, classify the relationship as one of:
- "synonym": both entries describe the SAME real-world concept (linking them helps coordination)
- "homonym": the entries use similar words for DIFFERENT concepts (a false friend — linking them would pollute recall)
- "distinct": related but different topics; no action needed

Return a JSON array of objects, one per pair:
[{"pair_index": 0, "relation": "synonym|homonym|distinct", "reasoning": "brief explanation"}]

Judge by MEANING in each region's context, not surface wording."""


def _pair_regions(pair: SimilarPair) -> tuple[str, str]:
    return pair.a_department or "", pair.b_department or ""


_JUDGE_BATCH_SIZE = 6


async def _already_judged_pairs(
    db: AsyncSession, finding_type: str,
) -> set[frozenset[int]]:
    """Neuron pairs already covered by findings of this type (any status).

    Makes sweeps INCREMENTAL: each pass judges the next-most-similar
    unjudged pairs instead of re-judging the same top-N forever, so the
    whole similarity zone gets covered over successive sweeps.
    """
    rows = (await db.execute(text(
        "SELECT neuron_ids_json FROM integrity_findings WHERE finding_type = :ft"
    ), {"ft": finding_type})).all()
    judged: set[frozenset[int]] = set()
    for (ids_json,) in rows:
        try:
            ids = json.loads(ids_json or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        if len(ids) >= 2:
            judged.add(frozenset(ids[:2]))
    return judged


def _filter_unjudged(
    pairs: list[SimilarPair], judged: set[frozenset[int]],
) -> list[SimilarPair]:
    return [
        p for p in pairs
        if frozenset((p.neuron_a_id, p.neuron_b_id)) not in judged
    ]


def _cross_region_pairs(pairs: list[SimilarPair]) -> list[SimilarPair]:
    """Keep only pairs whose endpoints live in different, known regions."""
    return [
        p for p in pairs
        if p.a_department and p.b_department and p.a_department != p.b_department
    ]


def _persist_findings(
    db: AsyncSession, scan: IntegrityScan,
    findings_data: list[IntegrityFindingData],
    regions: list[str | None],
) -> None:
    """Persist findings with owning-region routing."""
    assert scan.id is not None, "Scan must be flushed"
    assert len(findings_data) == len(regions), "regions must align with findings"
    for fd, region in zip(findings_data, regions):
        db.add(IntegrityFinding(
            scan_id=scan.id, finding_type=fd.finding_type,
            region=region,
            severity=fd.severity, priority_score=fd.priority_score,
            description=fd.description, detail_json=fd.detail_json,
            neuron_ids_json=json.dumps(fd.neuron_ids),
        ))


async def _open_scan(
    db: AsyncSession, scan_type: str, params: dict, initiated_by: str | None,
) -> IntegrityScan:
    scan = IntegrityScan(
        scan_type=scan_type, scope="cross_region", status="running",
        parameters_json=json.dumps(params), initiated_by=initiated_by,
    )
    db.add(scan)
    await db.flush()
    return scan


async def _close_scan(db: AsyncSession, scan: IntegrityScan, count: int) -> None:
    scan.status = "completed"
    scan.completed_at = datetime.utcnow()
    scan.findings_count = count
    await db.commit()


# ── 1. Cross-region contradictions ───────────────────────────────────

def _contradiction_findings_from_verdicts(
    candidates: list[SimilarPair], verdicts: list[dict],
) -> tuple[list[IntegrityFindingData], list[str | None]]:
    """Convert judge verdicts into region-routed contradiction findings."""
    from app.services.integrity.conflict_monitor import _build_conflict_finding

    findings: list[IntegrityFindingData] = []
    regions: list[str | None] = []
    for verdict in verdicts:
        idx = verdict.get("pair_index", -1)
        classification = verdict.get("classification", "consistent")
        if not (0 <= idx < len(candidates)) or classification not in ("contradictory", "ambiguous"):
            continue
        pair = candidates[idx]
        finding = _build_conflict_finding(pair, classification, verdict.get("reasoning", ""))
        detail = json.loads(finding.detail_json)
        detail["cross_region"] = True
        detail["owning_regions"] = list(_pair_regions(pair))
        finding.detail_json = json.dumps(detail)
        findings.append(finding)
        regions.append(pair.a_department)
    return findings, regions


async def scan_cross_region_contradictions(
    db: AsyncSession,
    max_pairs: int | None = None,
    judge_model: str | None = None,
    initiated_by: str | None = None,
) -> IntegrityScanResult:
    """Contradiction zone (sim 0.60-0.85) restricted to cross-region pairs,
    with a bounded structured LLM verdict on the shortlist."""
    from app.services.integrity.conflict_monitor import (
        _classify_batch, _load_neuron_content, _build_conflict_finding,
    )

    cap = max_pairs or settings.reconciler_max_pairs
    model = judge_model or settings.reconciler_judge_model
    scan = await _open_scan(
        db, "reconciler_contradiction",
        {"max_pairs": cap, "judge_model": model}, initiated_by,
    )

    metadata, matrix = await load_neuron_embeddings(
        db, scope="global", max_neurons=settings.integrity_max_scan_neurons,
    )
    if matrix.shape[0] < 2:
        await _close_scan(db, scan, 0)
        return IntegrityScanResult(scan_type="reconciler_contradiction", scope="cross_region")

    sim_matrix = compute_pairwise_similarity(matrix)
    in_zone = extract_pairs_in_range(
        sim_matrix, metadata,
        settings.integrity_conflict_sim_min, settings.integrity_conflict_sim_max,
        max_pairs=cap * 16,
    )
    judged = await _already_judged_pairs(db, "contradiction")
    candidates = _filter_unjudged(_cross_region_pairs(in_zone), judged)[:cap]

    findings: list[IntegrityFindingData] = []
    regions: list[str | None] = []
    if candidates:
        content_map = await _load_neuron_content(
            db, [nid for p in candidates for nid in (p.neuron_a_id, p.neuron_b_id)],
        )
        for start in range(0, len(candidates), _JUDGE_BATCH_SIZE):
            batch = candidates[start:start + _JUDGE_BATCH_SIZE]
            verdicts = await _classify_batch(batch, content_map, model=model)
            batch_findings, batch_regions = _contradiction_findings_from_verdicts(batch, verdicts)
            findings.extend(batch_findings)
            regions.extend(batch_regions)

    _persist_findings(db, scan, findings, regions)
    await _close_scan(db, scan, len(findings))
    return IntegrityScanResult(
        scan_type="reconciler_contradiction", scope="cross_region",
        findings=findings,
        extra={"candidates_checked": len(candidates), "judge_model": model},
    )


# ── 2. Staleness divergence ──────────────────────────────────────────

_DIVERGENCE_SQL = """
SELECT e.source_id, e.target_id, e.weight,
       a.label AS a_label, a.department AS a_region,
       COALESCE(a.last_verified, a.created_at) AS a_stamp,
       b.label AS b_label, b.department AS b_region,
       COALESCE(b.last_verified, b.created_at) AS b_stamp
FROM neuron_edges e
JOIN neurons a ON a.id = e.source_id AND a.is_active = true
JOIN neurons b ON b.id = e.target_id AND b.is_active = true
WHERE e.edge_type = 'pyramidal'
  AND e.weight >= :min_weight
  AND a.department IS NOT NULL AND b.department IS NOT NULL
  AND a.department != b.department
  AND ABS(EXTRACT(EPOCH FROM (
        COALESCE(a.last_verified, a.created_at)
        - COALESCE(b.last_verified, b.created_at)
      )) / 86400.0) > :divergence_days
ORDER BY ABS(EXTRACT(EPOCH FROM (
        COALESCE(a.last_verified, a.created_at)
        - COALESCE(b.last_verified, b.created_at)
      ))) DESC
LIMIT :lim
"""


async def scan_staleness_divergence(
    db: AsyncSession,
    divergence_days: int | None = None,
    max_findings: int = 25,
    initiated_by: str | None = None,
) -> IntegrityScanResult:
    """Pyramidal edges whose endpoints' verification dates diverged: one
    silo updated its side while the other still cites the old state."""
    days = divergence_days or settings.reconciler_staleness_divergence_days
    scan = await _open_scan(
        db, "reconciler_staleness",
        {"divergence_days": days, "max_findings": max_findings}, initiated_by,
    )

    rows = (await db.execute(text(_DIVERGENCE_SQL), {
        "min_weight": settings.spread_min_edge_weight,
        "divergence_days": days,
        "lim": max_findings,
    })).all()

    findings: list[IntegrityFindingData] = []
    regions: list[str | None] = []
    for r in rows:
        stale_is_a = r.a_stamp < r.b_stamp
        stale_label = r.a_label if stale_is_a else r.b_label
        stale_region = r.a_region if stale_is_a else r.b_region
        fresh_label = r.b_label if stale_is_a else r.a_label
        fresh_region = r.b_region if stale_is_a else r.a_region
        delta_days = abs((r.a_stamp - r.b_stamp).days)
        findings.append(IntegrityFindingData(
            finding_type="staleness_divergence",
            severity="warning" if delta_days > days * 2 else "info",
            priority_score=min(0.9, 0.4 + delta_days / (days * 4)),
            description=(
                f"'{stale_label}' ({stale_region}) is {delta_days}d behind "
                f"linked '{fresh_label}' ({fresh_region}) — the fresh side "
                f"changed; the stale side may still cite the old state."
            ),
            detail_json=json.dumps({
                "edge": {"source_id": r.source_id, "target_id": r.target_id,
                         "weight": float(r.weight)},
                "stale": {"label": stale_label, "region": stale_region},
                "fresh": {"label": fresh_label, "region": fresh_region},
                "divergence_days": delta_days,
                "owning_regions": [stale_region],
            }),
            neuron_ids=[r.source_id, r.target_id],
        ))
        regions.append(stale_region)

    _persist_findings(db, scan, findings, regions)
    await _close_scan(db, scan, len(findings))
    return IntegrityScanResult(
        scan_type="reconciler_staleness", scope="cross_region",
        findings=findings, extra={"edges_flagged": len(findings)},
    )


# ── 3. Homonyms vs synonyms ──────────────────────────────────────────

async def _judge_homonym_batch(
    pairs: list[SimilarPair],
    content_map: dict[int, tuple[str, str]],
    model: str,
) -> list[dict]:
    """Bounded structured verdict: synonym | homonym | distinct."""
    from app.services.integrity.conflict_monitor import _format_pair_prompt
    from app.services.llm_provider import llm_chat

    result = await llm_chat(
        system_prompt=_HOMONYM_SYSTEM_PROMPT,
        user_message=_format_pair_prompt(pairs, content_map),
        model=model, max_tokens=1024,
    )
    raw = result.get("text", "")
    try:
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        verdicts = json.loads(raw.strip())
        assert isinstance(verdicts, list), "judge must return a list"
    except (json.JSONDecodeError, IndexError, AssertionError):
        verdicts = []
    return verdicts


def _build_homonym_finding(pair: SimilarPair, relation: str, reasoning: str) -> IntegrityFindingData:
    action = "link/merge (same concept)" if relation == "synonym" else \
        "keep separate — false friend; linking would pollute spread activation"
    return IntegrityFindingData(
        finding_type="homonym_synonym",
        severity="info" if relation == "synonym" else "warning",
        priority_score=0.6 if relation == "homonym" else 0.5,
        description=(
            f"{relation.title()}: '{pair.a_label}' ({pair.a_department}) ~ "
            f"'{pair.b_label}' ({pair.b_department}) — {action}"
        ),
        detail_json=json.dumps({
            "neuron_a": {"id": pair.neuron_a_id, "label": pair.a_label,
                         "region": pair.a_department},
            "neuron_b": {"id": pair.neuron_b_id, "label": pair.b_label,
                         "region": pair.b_department},
            "cosine_similarity": round(pair.similarity, 4),
            "relation": relation,
            "llm_reasoning": reasoning,
            "suggested_resolution": "linked" if relation == "synonym" else "differentiated",
            "owning_regions": list(_pair_regions(pair)),
        }),
        neuron_ids=[pair.neuron_a_id, pair.neuron_b_id],
    )


async def scan_homonym_synonym(
    db: AsyncSession,
    max_pairs: int | None = None,
    judge_model: str | None = None,
    initiated_by: str | None = None,
) -> IntegrityScanResult:
    """Cross-region near-duplicates disambiguated: same concept -> propose
    link; false friend -> keep separate/namespace."""
    from app.services.integrity.conflict_monitor import _load_neuron_content

    cap = max_pairs or settings.reconciler_max_pairs
    model = judge_model or settings.reconciler_judge_model
    scan = await _open_scan(
        db, "reconciler_homonym",
        {"max_pairs": cap, "judge_model": model,
         "sim_threshold": settings.reconciler_homonym_sim_threshold},
        initiated_by,
    )

    metadata, matrix = await load_neuron_embeddings(
        db, scope="global", max_neurons=settings.integrity_max_scan_neurons,
    )
    if matrix.shape[0] < 2:
        await _close_scan(db, scan, 0)
        return IntegrityScanResult(scan_type="reconciler_homonym", scope="cross_region")

    sim_matrix = compute_pairwise_similarity(matrix)
    near_dupes = extract_pairs_above_threshold(
        sim_matrix, metadata, settings.reconciler_homonym_sim_threshold,
        max_pairs=cap * 16,
    )
    judged = await _already_judged_pairs(db, "homonym_synonym")
    candidates = _filter_unjudged(_cross_region_pairs(near_dupes), judged)[:cap]

    findings: list[IntegrityFindingData] = []
    regions: list[str | None] = []
    if candidates:
        content_map = await _load_neuron_content(
            db, [nid for p in candidates for nid in (p.neuron_a_id, p.neuron_b_id)],
        )
        for start in range(0, len(candidates), _JUDGE_BATCH_SIZE):
            batch = candidates[start:start + _JUDGE_BATCH_SIZE]
            verdicts = await _judge_homonym_batch(batch, content_map, model)
            for verdict in verdicts:
                idx = verdict.get("pair_index", -1)
                relation = verdict.get("relation", "distinct")
                if not (0 <= idx < len(batch)) or relation not in ("synonym", "homonym"):
                    continue
                pair = batch[idx]
                findings.append(_build_homonym_finding(pair, relation, verdict.get("reasoning", "")))
                regions.append(pair.a_department)

    _persist_findings(db, scan, findings, regions)
    await _close_scan(db, scan, len(findings))
    return IntegrityScanResult(
        scan_type="reconciler_homonym", scope="cross_region",
        findings=findings,
        extra={"candidates_checked": len(candidates), "judge_model": model},
    )


# ── 4. Seam coverage gaps ────────────────────────────────────────────

def _build_seam_gap_finding(cluster: dict) -> IntegrityFindingData:
    """Finding proposing a coordination node for a cross-region community."""
    cluster_regions = cluster["departments"]
    return IntegrityFindingData(
        finding_type="seam_gap",
        severity="info",
        priority_score=min(0.7, 0.3 + len(cluster["neuron_ids"]) / 40),
        description=(
            f"Regions {', '.join(cluster_regions)} co-fire as a community "
            f"({len(cluster['neuron_ids'])} neurons, topic '{cluster['suggested_label']}') "
            f"with no shared coordination node — propose one."
        ),
        detail_json=json.dumps({
            "cluster_id": cluster["cluster_id"],
            "regions": cluster_regions,
            "suggested_label": cluster["suggested_label"],
            "avg_internal_weight": cluster["avg_internal_weight"],
            "owning_regions": cluster_regions,
        }),
        neuron_ids=cluster["neuron_ids"][:20],
    )


async def scan_seam_gaps(
    db: AsyncSession,
    max_findings: int = 10,
    initiated_by: str | None = None,
) -> IntegrityScanResult:
    """Regions that co-fire as a community with no shared coordination node.

    Leiden clusters spanning >= 2 regions whose members include no
    process-abstraction neuron get a finding proposing one.
    """
    from sqlalchemy import select, or_, and_
    from app.models import Neuron
    from app.services.clustering import find_clusters

    scan = await _open_scan(
        db, "reconciler_seam_gap", {"max_findings": max_findings}, initiated_by,
    )

    clusters = await find_clusters(db, min_weight=0.3, min_size=3, min_departments=2)
    findings: list[IntegrityFindingData] = []
    regions: list[str | None] = []
    for cluster in clusters[:max_findings * 3]:
        if len(findings) >= max_findings:
            break
        coordination = (await db.execute(
            select(Neuron.id).where(
                Neuron.is_active.is_(True),
                Neuron.id.in_(cluster["neuron_ids"]),
                or_(
                    Neuron.abstraction_type == "process",
                    and_(Neuron.abstraction_type.is_(None), Neuron.layer == 2),
                ),
            ).limit(1)
        )).scalar_one_or_none()
        if coordination is not None:
            continue
        findings.append(_build_seam_gap_finding(cluster))
        cluster_regions = cluster["departments"]
        regions.append(cluster_regions[0] if cluster_regions else None)

    _persist_findings(db, scan, findings, regions)
    await _close_scan(db, scan, len(findings))
    return IntegrityScanResult(
        scan_type="reconciler_seam_gap", scope="cross_region",
        findings=findings, extra={"clusters_examined": len(clusters)},
    )


# ── The horizontal loop ──────────────────────────────────────────────

async def run_reconciler_sweep(
    db: AsyncSession,
    initiated_by: str = "reconciler_loop",
    judge_model: str | None = None,
) -> dict:
    """One full horizontal pass: all four cross-region detections.

    Deterministic pipeline (bounded LLM judging on shortlists only).
    Detects and ROUTES; the silos RESOLVE.
    """
    contradiction = await scan_cross_region_contradictions(
        db, judge_model=judge_model, initiated_by=initiated_by,
    )
    staleness = await scan_staleness_divergence(db, initiated_by=initiated_by)
    homonym = await scan_homonym_synonym(
        db, judge_model=judge_model, initiated_by=initiated_by,
    )
    seam = await scan_seam_gaps(db, initiated_by=initiated_by)

    return {
        "status": "completed",
        "contradictions": len(contradiction.findings),
        "staleness_divergences": len(staleness.findings),
        "homonym_synonym": len(homonym.findings),
        "seam_gaps": len(seam.findings),
        "total_findings": (
            len(contradiction.findings) + len(staleness.findings)
            + len(homonym.findings) + len(seam.findings)
        ),
    }
