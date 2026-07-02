"""Gap detector — identifies knowledge gaps in the neuron graph.

Two modes:
- detect_gap(): Original first-hit-wins heuristic (backward-compatible).
- detect_gaps_scored(): Multi-signal scoring that runs ALL heuristics plus
  eval-dimension, utilization, coverage, quality-trend, and staleness checks.
  Returns ranked ScoredGap list with full evidence chains.

Heuristic priority (for detect_gap):
1. Emergent queue (unresolved external references detected in neurons)
2. Low-eval queries (past queries where overall eval scored poorly)
3. Thin neurons (active neurons with minimal content)
4. Sparse subtrees (roles/tasks with fewer children than peers)
5. Emergent clusters (cross-department clusters without Task neuron)
"""

import json
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timedelta
from types import MappingProxyType

from sqlalchemy import select, func, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    EmergentQueue, Neuron, Query, EvalScore, AutopilotRun,
    KNOWLEDGE_ABSTRACTIONS,
)


def _knowledge_node_clause():
    """Filter to knowledge-content nodes (vs structural/concept scaffolding).

    Keyed on the abstraction axis when classified; falls back to the legacy
    layer>=2 heuristic for unclassified rows.
    """
    return or_(
        Neuron.abstraction_type.in_(KNOWLEDGE_ABSTRACTIONS),
        and_(Neuron.abstraction_type.is_(None), Neuron.layer >= 2),
    )


def _coordination_node_clause():
    """Filter to process-abstraction nodes that can anchor a cluster.

    Falls back to the legacy layer==2 (Task) heuristic when unclassified.
    """
    return or_(
        Neuron.abstraction_type == "process",
        and_(Neuron.abstraction_type.is_(None), Neuron.layer == 2),
    )


# ── Dataclasses ───────────────────────────────────────────────────────

@dataclass
class GapTarget:
    source: str  # "emergent_queue" | "low_eval" | "thin_neuron" | "sparse_subtree" | "emergent_cluster"
    description: str  # Human-readable gap description for query generation
    context_neuron_ids: list[int]  # Nearby neurons for context
    emergent_queue_id: int | None = None
    query_id: int | None = None  # For low_eval source


@dataclass
class GapEvidence:
    """One piece of evidence supporting a detected gap."""
    signal: str           # e.g. "low_eval_governance", "zero_hit_neuron", "coverage_gap"
    description: str      # Human-readable explanation
    metric_value: float   # The measured value that triggered this
    threshold: float      # The threshold it was compared against
    neuron_ids: list[int] = dc_field(default_factory=list)
    query_ids: list[int] = dc_field(default_factory=list)


@dataclass
class ScoredGap:
    """A gap with scored priority and full evidence chain."""
    source: str            # Same vocabulary as GapTarget.source
    description: str
    context_neuron_ids: list[int]
    evidence: list[GapEvidence]
    priority_score: float  # Weighted composite 0.0–1.0
    emergent_queue_id: int | None = None
    query_id: int | None = None

    def to_gap_target(self) -> GapTarget:
        """Convert to legacy GapTarget for backward compatibility."""
        return GapTarget(
            source=self.source,
            description=self.description,
            context_neuron_ids=self.context_neuron_ids,
            emergent_queue_id=self.emergent_queue_id,
            query_id=self.query_id,
        )


# Scoring weights for priority calculation
DEFAULT_WEIGHTS = MappingProxyType({
    "severity": 0.4,
    "breadth": 0.3,
    "staleness": 0.2,
    "source_authority": 0.1,
})

# Source authority ranking (higher = more authoritative gap source)
SOURCE_AUTHORITY = MappingProxyType({
    "emergent_queue": 0.9,    # External reference — regulatory risk
    "stale_neuron": 0.8,      # Verification lapse — compliance risk
    "low_eval": 0.7,          # Demonstrated failure
    "eval_dimension": 0.6,    # Dimension-specific weakness
    "quality_trend": 0.5,     # Declining performance
    "thin_neuron": 0.4,       # Incomplete content
    "zero_hit_neuron": 0.3,   # Unused content
    "sparse_subtree": 0.3,    # Structural gap
    "coverage_gap": 0.2,      # Imbalanced graph
    "emergent_cluster": 0.2,  # Cross-department opportunity
})


# ── Legacy first-hit-wins API ─────────────────────────────────────────

async def detect_gap(
    db: AsyncSession,
    focus_neuron_id: int | None = None,
) -> GapTarget | None:
    """Find the highest-priority gap to fill. Returns None if no gaps found.

    Backward-compatible wrapper — uses detect_gaps_scored internally and
    returns the top-scored gap as a GapTarget.
    """
    scored = await detect_gaps_scored(db, focus_neuron_id, limit=1)
    if scored:
        return scored[0].to_gap_target()
    return None


# ── Scored multi-signal API ───────────────────────────────────────────

async def detect_gaps_scored(
    db: AsyncSession,
    focus_neuron_id: int | None = None,
    limit: int = 10,
) -> list[ScoredGap]:
    """Run all gap heuristics and return scored gaps sorted by priority.

    Unlike detect_gap(), this runs every heuristic and new metric-based
    checks, collecting evidence for each gap found.
    """
    gaps: list[ScoredGap] = []

    # Existing heuristics (adapted to return ScoredGap)
    gap = await _scored_emergent_queue(db, focus_neuron_id)
    if gap:
        gaps.append(gap)

    gap = await _scored_low_eval(db, focus_neuron_id)
    if gap:
        gaps.append(gap)

    gap = await _scored_thin_neurons(db, focus_neuron_id)
    if gap:
        gaps.append(gap)

    gap = await _scored_sparse_subtrees(db, focus_neuron_id)
    if gap:
        gaps.append(gap)

    gap = await _scored_emergent_clusters(db, focus_neuron_id)
    if gap:
        gaps.append(gap)

    # New metric-based checks
    gap = await _scored_eval_dimensions(db, focus_neuron_id)
    if gap:
        gaps.append(gap)

    gap = await _scored_zero_hit_neurons(db, focus_neuron_id)
    if gap:
        gaps.append(gap)

    gap = await _scored_coverage_gap(db)
    if gap:
        gaps.append(gap)

    gap = await _scored_quality_trend(db)
    if gap:
        gaps.append(gap)

    gap = await _scored_stale_neurons(db, focus_neuron_id)
    if gap:
        gaps.append(gap)

    gaps.sort(key=lambda g: g.priority_score, reverse=True)
    return gaps[:limit]


def _compute_priority(
    severity: float,
    breadth: float,
    staleness: float,
    source: str,
) -> float:
    """Weighted priority score in [0, 1]."""
    authority = SOURCE_AUTHORITY.get(source, 0.1)
    w = DEFAULT_WEIGHTS
    raw = (
        w["severity"] * min(severity, 1.0)
        + w["breadth"] * min(breadth, 1.0)
        + w["staleness"] * min(staleness, 1.0)
        + w["source_authority"] * authority
    )
    return round(min(raw, 1.0), 4)


async def _check_emergent_queue(
    db: AsyncSession, focus_neuron_id: int | None
) -> GapTarget | None:
    """Find highest-priority unresolved reference from the emergent queue."""
    stmt = (
        select(EmergentQueue)
        .where(EmergentQueue.status == "pending")
        .order_by(EmergentQueue.detection_count.desc())
    )
    result = await db.execute(stmt)
    entries = result.scalars().all()

    for entry in entries:
        # If focused, only pick entries detected in neurons under the focus subtree
        if focus_neuron_id:
            neuron_ids = json.loads(entry.detected_in_neuron_ids or "[]")
            subtree_ids = await _get_subtree_ids(db, focus_neuron_id)
            if not any(nid in subtree_ids for nid in neuron_ids):
                continue

        # Get context neurons (the ones that reference this citation)
        context_ids = json.loads(entry.detected_in_neuron_ids or "[]")[:5]

        return GapTarget(
            source="emergent_queue",
            description=(
                f"The neuron graph references '{entry.citation_pattern}' "
                f"({entry.domain}/{entry.family}) in {entry.detection_count} location(s), "
                f"but no dedicated neuron covers this reference. "
                f"Generate a question that would require detailed knowledge of {entry.citation_pattern}."
            ),
            context_neuron_ids=context_ids,
            emergent_queue_id=entry.id,
        )

    return None


async def _check_low_eval_queries(
    db: AsyncSession, focus_neuron_id: int | None
) -> GapTarget | None:
    """Find a past query that scored poorly and hasn't been retried by autopilot."""
    # Get query IDs already targeted by autopilot
    already_tried = set()
    runs_result = await db.execute(select(AutopilotRun.query_id).where(AutopilotRun.query_id.isnot(None)))
    for row in runs_result.all():
        already_tried.add(row[0])

    # Find queries with low overall eval (<=2) that haven't been autopilot-retried
    stmt = (
        select(EvalScore.query_id, func.min(EvalScore.overall).label("min_overall"))
        .group_by(EvalScore.query_id)
        .having(func.min(EvalScore.overall) <= 2)
        .order_by(func.min(EvalScore.overall).asc())
    )
    result = await db.execute(stmt)
    candidates = result.all()

    for query_id, min_overall in candidates:
        if query_id in already_tried:
            continue

        query = await db.get(Query, query_id)
        if not query:
            continue

        # If focused, check if the query's activated neurons overlap with focus subtree
        if focus_neuron_id:
            activated_ids = json.loads(query.selected_neuron_ids or "[]")
            subtree_ids = await _get_subtree_ids(db, focus_neuron_id)
            if activated_ids and not any(nid in subtree_ids for nid in activated_ids):
                continue

        context_ids = json.loads(query.selected_neuron_ids or "[]")[:5]

        return GapTarget(
            source="low_eval",
            description=(
                f"A previous query scored {min_overall}/5 on evaluation: "
                f'"{query.user_message[:200]}". '
                f"Generate a similar question in this topic area to test whether "
                f"the knowledge gap has been addressed."
            ),
            context_neuron_ids=context_ids,
            query_id=query_id,
        )

    return None


async def _check_thin_neurons(
    db: AsyncSession, focus_neuron_id: int | None
) -> GapTarget | None:
    """Find active neurons with minimal content that need enrichment."""
    stmt = select(Neuron).where(
        Neuron.is_active == True,
        _knowledge_node_clause(),  # Don't flag structural/concept nodes as thin
    )

    if focus_neuron_id:
        subtree_ids = await _get_subtree_ids(db, focus_neuron_id)
        stmt = stmt.where(Neuron.id.in_(subtree_ids))

    result = await db.execute(stmt.order_by(Neuron.layer, Neuron.id))
    neurons = result.scalars().all()

    # Find neurons with very short or empty content
    for neuron in neurons:
        content_len = len(neuron.content or "")
        summary_len = len(neuron.summary or "")
        if content_len < 50 and summary_len < 30:
            # Get parent chain for context
            context_ids = await _get_ancestor_ids(db, neuron.id)
            context_ids.append(neuron.id)

            return GapTarget(
                source="thin_neuron",
                description=(
                    f"Neuron #{neuron.id} '{neuron.label}' (L{neuron.layer} {neuron.node_type}, "
                    f"dept={neuron.department or 'none'}) has minimal content "
                    f"({content_len} chars). Generate a question that would require "
                    f"detailed knowledge about {neuron.label} to answer well."
                ),
                context_neuron_ids=context_ids,
            )

    return None


async def _check_sparse_subtrees(
    db: AsyncSession, focus_neuron_id: int | None
) -> GapTarget | None:
    """Find nodes that have significantly fewer children than their siblings."""
    # Get child counts per parent
    stmt = (
        select(
            Neuron.parent_id,
            func.count(Neuron.id).label("child_count"),
        )
        .where(Neuron.is_active == True, Neuron.parent_id.isnot(None))
        .group_by(Neuron.parent_id)
    )
    result = await db.execute(stmt)
    child_counts = {row[0]: row[1] for row in result.all()}

    if not child_counts:
        return None

    # Group siblings — find parents that have sparse children compared to peers.
    # Projection-DEPTH heuristic (grouping nodes at depth 1-2 in any projection),
    # so it stays keyed on layer by design, not on the abstraction axis.
    stmt = select(Neuron).where(
        Neuron.is_active == True,
        Neuron.layer.in_([1, 2]),
    )

    if focus_neuron_id:
        subtree_ids = await _get_subtree_ids(db, focus_neuron_id)
        stmt = stmt.where(Neuron.id.in_(subtree_ids))

    result = await db.execute(stmt.order_by(Neuron.layer, Neuron.id))
    candidates = result.scalars().all()

    # Find nodes with fewer children than the median for their layer
    layer_counts: dict[int, list[int]] = {}
    for n in candidates:
        count = child_counts.get(n.id, 0)
        layer_counts.setdefault(n.layer, []).append(count)

    for n in candidates:
        count = child_counts.get(n.id, 0)
        layer_list = layer_counts.get(n.layer, [])
        if not layer_list:
            continue
        median = sorted(layer_list)[len(layer_list) // 2]
        # Sparse = less than half the median and median is at least 4
        if median >= 4 and count < median // 2:
            context_ids = await _get_ancestor_ids(db, n.id)
            context_ids.append(n.id)
            # Add a few children for context
            children_result = await db.execute(
                select(Neuron.id).where(Neuron.parent_id == n.id, Neuron.is_active == True).limit(5)
            )
            context_ids.extend([r[0] for r in children_result.all()])

            return GapTarget(
                source="sparse_subtree",
                description=(
                    f"Neuron '{n.label}' (L{n.layer} {n.node_type}, dept={n.department or 'none'}) "
                    f"has only {count} children while peers average {median}. "
                    f"Generate a question about {n.label} that would expose missing "
                    f"subtopics or knowledge areas."
                ),
                context_neuron_ids=context_ids,
            )

    return None


async def _get_subtree_ids(db: AsyncSession, root_id: int) -> set[int]:
    """Get all neuron IDs in the subtree rooted at root_id (including root)."""
    ids = {root_id}
    frontier = [root_id]
    while frontier:
        result = await db.execute(
            select(Neuron.id).where(
                Neuron.parent_id.in_(frontier),
                Neuron.is_active == True,
            )
        )
        children = [r[0] for r in result.all()]
        if not children:
            break
        ids.update(children)
        frontier = children
    return ids


async def _get_ancestor_ids(db: AsyncSession, neuron_id: int) -> list[int]:
    """Walk up the parent chain and return ancestor IDs (root first)."""
    ancestors = []
    current = await db.get(Neuron, neuron_id)
    while current and current.parent_id:
        ancestors.insert(0, current.parent_id)
        current = await db.get(Neuron, current.parent_id)
    return ancestors


async def _check_emergent_clusters(
    db: AsyncSession, focus_neuron_id: int | None
) -> GapTarget | None:
    """Find cross-department clusters with no corresponding Task-level neuron."""
    from app.services.clustering import find_clusters

    clusters = await find_clusters(db, min_weight=0.3, min_size=3, min_departments=2)
    if not clusters:
        return None

    for cluster in clusters:
        nids = cluster["neuron_ids"]
        depts = cluster["departments"]
        suggested = cluster["suggested_label"]

        # Check if there's already a process-abstraction neuron that can
        # anchor/coordinate this cluster
        task_result = await db.execute(
            select(Neuron).where(
                Neuron.is_active == True,
                _coordination_node_clause(),
                Neuron.id.in_(nids),
            )
        )
        existing_tasks = task_result.scalars().all()
        if existing_tasks:
            continue  # Already has Task-level neurons in the cluster

        # If focused, check overlap with focus subtree
        if focus_neuron_id:
            subtree_ids = await _get_subtree_ids(db, focus_neuron_id)
            if not any(nid in subtree_ids for nid in nids):
                continue

        context_ids = nids[:5]

        return GapTarget(
            source="emergent_cluster",
            description=(
                f"A cross-department cluster spanning {', '.join(depts)} "
                f"({len(nids)} neurons, suggested topic: '{suggested}') "
                f"has no Task-level neuron to anchor it. Generate a question that "
                f"would require synthesizing knowledge across these departments "
                f"on the topic of {suggested}."
            ),
            context_neuron_ids=context_ids,
        )

    return None


# ── Scored heuristic wrappers ─────────────────────────────────────────
# Each wraps an existing heuristic, adding GapEvidence and priority score.

async def _scored_emergent_queue(
    db: AsyncSession, focus_neuron_id: int | None,
) -> ScoredGap | None:
    """Scored wrapper for emergent queue check."""
    target = await _check_emergent_queue(db, focus_neuron_id)
    if not target:
        return None
    eq = await db.get(EmergentQueue, target.emergent_queue_id) if target.emergent_queue_id else None
    det_count = eq.detection_count if eq else 1
    evidence = [GapEvidence(
        signal="emergent_queue",
        description=target.description,
        metric_value=float(det_count),
        threshold=1.0,
        neuron_ids=target.context_neuron_ids,
    )]
    severity = min(det_count / 5.0, 1.0)
    breadth = min(len(target.context_neuron_ids) / 5.0, 1.0)
    return ScoredGap(
        source="emergent_queue",
        description=target.description,
        context_neuron_ids=target.context_neuron_ids,
        evidence=evidence,
        priority_score=_compute_priority(severity, breadth, 0.5, "emergent_queue"),
        emergent_queue_id=target.emergent_queue_id,
    )


async def _scored_low_eval(
    db: AsyncSession, focus_neuron_id: int | None,
) -> ScoredGap | None:
    """Scored wrapper for low-eval query check."""
    target = await _check_low_eval_queries(db, focus_neuron_id)
    if not target:
        return None
    evidence = [GapEvidence(
        signal="low_eval",
        description=target.description,
        metric_value=2.0,
        threshold=2.0,
        neuron_ids=target.context_neuron_ids,
        query_ids=[target.query_id] if target.query_id else [],
    )]
    return ScoredGap(
        source="low_eval",
        description=target.description,
        context_neuron_ids=target.context_neuron_ids,
        evidence=evidence,
        priority_score=_compute_priority(0.8, 0.3, 0.4, "low_eval"),
        query_id=target.query_id,
    )


async def _scored_thin_neurons(
    db: AsyncSession, focus_neuron_id: int | None,
) -> ScoredGap | None:
    """Scored wrapper for thin neuron check."""
    target = await _check_thin_neurons(db, focus_neuron_id)
    if not target:
        return None
    evidence = [GapEvidence(
        signal="thin_neuron",
        description=target.description,
        metric_value=0.0,
        threshold=50.0,
        neuron_ids=target.context_neuron_ids,
    )]
    return ScoredGap(
        source="thin_neuron",
        description=target.description,
        context_neuron_ids=target.context_neuron_ids,
        evidence=evidence,
        priority_score=_compute_priority(0.5, 0.2, 0.3, "thin_neuron"),
    )


async def _scored_sparse_subtrees(
    db: AsyncSession, focus_neuron_id: int | None,
) -> ScoredGap | None:
    """Scored wrapper for sparse subtree check."""
    target = await _check_sparse_subtrees(db, focus_neuron_id)
    if not target:
        return None
    evidence = [GapEvidence(
        signal="sparse_subtree",
        description=target.description,
        metric_value=0.0,
        threshold=0.0,
        neuron_ids=target.context_neuron_ids,
    )]
    return ScoredGap(
        source="sparse_subtree",
        description=target.description,
        context_neuron_ids=target.context_neuron_ids,
        evidence=evidence,
        priority_score=_compute_priority(0.4, 0.3, 0.3, "sparse_subtree"),
    )


async def _scored_emergent_clusters(
    db: AsyncSession, focus_neuron_id: int | None,
) -> ScoredGap | None:
    """Scored wrapper for emergent cluster check."""
    target = await _check_emergent_clusters(db, focus_neuron_id)
    if not target:
        return None
    evidence = [GapEvidence(
        signal="emergent_cluster",
        description=target.description,
        metric_value=0.0,
        threshold=0.0,
        neuron_ids=target.context_neuron_ids,
    )]
    return ScoredGap(
        source="emergent_cluster",
        description=target.description,
        context_neuron_ids=target.context_neuron_ids,
        evidence=evidence,
        priority_score=_compute_priority(0.3, 0.4, 0.2, "emergent_cluster"),
    )


# ── New metric-based checks ──────────────────────────────────────────

async def _scored_eval_dimensions(
    db: AsyncSession, focus_neuron_id: int | None,
) -> ScoredGap | None:
    """Check for weak eval dimensions (accuracy, completeness, faithfulness < 3)."""
    from app.services.metrics_aggregator import get_eval_dimension_averages

    avgs = await get_eval_dimension_averages(db, last_n=50)
    weak_dims = [
        (dim, val) for dim, val in avgs.items()
        if val > 0 and val < 3.0 and dim != "overall"
    ]
    if not weak_dims:
        return None

    worst_dim, worst_val = min(weak_dims, key=lambda x: x[1])

    dim_col = getattr(EvalScore, worst_dim, None)
    if dim_col is None:
        return None

    low_q_result = await db.execute(
        select(EvalScore.query_id)
        .where(dim_col <= 2)
        .order_by(EvalScore.query_id.desc())
        .limit(5)
    )
    query_ids = [r[0] for r in low_q_result.all()]

    context_ids: list[int] = []
    for qid in query_ids[:3]:
        q = await db.get(Query, qid)
        if q and q.selected_neuron_ids:
            nids = json.loads(q.selected_neuron_ids)[:3]
            context_ids.extend(nids)
    context_ids = list(set(context_ids))[:10]

    evidence = [GapEvidence(
        signal=f"low_eval_{worst_dim}",
        description=(
            f"Average {worst_dim} score is {worst_val:.2f}/5 across recent queries, "
            f"below the 3.0 threshold."
        ),
        metric_value=worst_val,
        threshold=3.0,
        neuron_ids=context_ids,
        query_ids=query_ids,
    )]

    severity = (3.0 - worst_val) / 2.0
    return ScoredGap(
        source="eval_dimension",
        description=(
            f"The graph's responses score poorly on {worst_dim} "
            f"(avg {worst_val:.2f}/5 over last 50 queries). Generate a question "
            f"that tests {worst_dim} to expose and address this weakness."
        ),
        context_neuron_ids=context_ids,
        evidence=evidence,
        priority_score=_compute_priority(severity, 0.5, 0.4, "eval_dimension"),
    )


async def _scored_zero_hit_neurons(
    db: AsyncSession, focus_neuron_id: int | None,
) -> ScoredGap | None:
    """Find neurons that have never been activated and are > 30 days old."""
    cutoff = datetime.utcnow() - timedelta(days=30)
    stmt = select(Neuron).where(
        Neuron.is_active.is_(True),
        Neuron.invocations == 0,
        Neuron.created_at < cutoff,
        _knowledge_node_clause(),
    )
    if focus_neuron_id:
        subtree_ids = await _get_subtree_ids(db, focus_neuron_id)
        stmt = stmt.where(Neuron.id.in_(subtree_ids))

    result = await db.execute(stmt.order_by(Neuron.created_at).limit(10))
    zero_neurons = result.scalars().all()
    if not zero_neurons:
        return None

    nids = [n.id for n in zero_neurons]
    sample = zero_neurons[0]
    context_ids = await _get_ancestor_ids(db, sample.id)
    context_ids.extend(nids[:5])

    evidence = [GapEvidence(
        signal="zero_hit_neuron",
        description=(
            f"{len(zero_neurons)} neurons have never been activated despite being "
            f"over 30 days old. First: #{sample.id} '{sample.label}'."
        ),
        metric_value=float(len(zero_neurons)),
        threshold=1.0,
        neuron_ids=nids,
    )]

    breadth = min(len(zero_neurons) / 10.0, 1.0)
    return ScoredGap(
        source="zero_hit_neuron",
        description=(
            f"Neuron #{sample.id} '{sample.label}' (L{sample.layer}, "
            f"dept={sample.department or 'none'}) has never been activated in over 30 days. "
            f"Generate a question that would require this neuron's knowledge to answer."
        ),
        context_neuron_ids=context_ids,
        evidence=evidence,
        priority_score=_compute_priority(0.3, breadth, 0.6, "zero_hit_neuron"),
    )


async def _scored_coverage_gap(db: AsyncSession) -> ScoredGap | None:
    """Detect department coverage imbalance via coefficient of variation."""
    from app.services.metrics_aggregator import get_department_coverage

    coverage = await get_department_coverage(db)
    if len(coverage) < 2:
        return None

    counts = list(coverage.values())
    mean_c = sum(counts) / len(counts)
    if mean_c == 0:
        return None
    variance = sum((c - mean_c) ** 2 for c in counts) / len(counts)
    std_c = variance ** 0.5
    cv = std_c / mean_c

    if cv < 0.5:
        return None

    min_dept = min(coverage, key=coverage.get)  # type: ignore[arg-type]
    min_count = coverage[min_dept]
    max_dept = max(coverage, key=coverage.get)  # type: ignore[arg-type]
    max_count = coverage[max_dept]

    result = await db.execute(
        select(Neuron.id).where(
            Neuron.is_active.is_(True),
            Neuron.department == min_dept,
        ).limit(5)
    )
    context_ids = [r[0] for r in result.all()]

    evidence = [GapEvidence(
        signal="coverage_gap",
        description=(
            f"Department coverage CV={cv:.2f} (threshold 0.5). "
            f"'{min_dept}' has {min_count} neurons vs '{max_dept}' with {max_count}."
        ),
        metric_value=cv,
        threshold=0.5,
        neuron_ids=context_ids,
    )]

    severity = min((cv - 0.5) / 0.5, 1.0)
    return ScoredGap(
        source="coverage_gap",
        description=(
            f"Department '{min_dept}' has only {min_count} active neurons compared to "
            f"'{max_dept}' with {max_count} (CV={cv:.2f}). Generate a question about "
            f"{min_dept} topics to build out this underrepresented area."
        ),
        context_neuron_ids=context_ids,
        evidence=evidence,
        priority_score=_compute_priority(severity, 0.4, 0.3, "coverage_gap"),
    )


async def _scored_quality_trend(db: AsyncSession) -> ScoredGap | None:
    """Detect declining quality by comparing recent vs previous eval windows."""
    from app.services.metrics_aggregator import get_quality_trend

    trend = await get_quality_trend(db, window=20)
    delta = trend["delta"]

    if delta >= -0.3 or trend["previous_avg"] == 0:
        return None

    evidence = [GapEvidence(
        signal="quality_trend",
        description=(
            f"Average eval score dropped from {trend['previous_avg']:.2f} to "
            f"{trend['recent_avg']:.2f} (delta={delta:.2f})."
        ),
        metric_value=abs(delta),
        threshold=0.3,
    )]

    severity = min(abs(delta) / 1.0, 1.0)
    return ScoredGap(
        source="quality_trend",
        description=(
            f"Overall eval quality is declining (avg {trend['previous_avg']:.2f} → "
            f"{trend['recent_avg']:.2f}). Generate a question in an area where the "
            f"graph should perform well to diagnose the regression."
        ),
        context_neuron_ids=[],
        evidence=evidence,
        priority_score=_compute_priority(severity, 0.6, 0.5, "quality_trend"),
    )


async def _scored_stale_neurons(
    db: AsyncSession, focus_neuron_id: int | None,
) -> ScoredGap | None:
    """Find active regulatory neurons with no verification in 90+ days."""
    cutoff = datetime.utcnow() - timedelta(days=90)
    stmt = select(Neuron).where(
        Neuron.is_active.is_(True),
        _knowledge_node_clause(),
        (Neuron.last_verified.is_(None)) | (Neuron.last_verified < cutoff),
        Neuron.source_type == "regulatory",
    )
    if focus_neuron_id:
        subtree_ids = await _get_subtree_ids(db, focus_neuron_id)
        stmt = stmt.where(Neuron.id.in_(subtree_ids))

    result = await db.execute(stmt.order_by(Neuron.created_at).limit(10))
    stale = result.scalars().all()
    if not stale:
        return None

    nids = [n.id for n in stale]
    sample = stale[0]
    context_ids = await _get_ancestor_ids(db, sample.id)
    context_ids.extend(nids[:5])

    evidence = [GapEvidence(
        signal="stale_neuron",
        description=(
            f"{len(stale)} regulatory neurons have not been verified in 90+ days. "
            f"First: #{sample.id} '{sample.label}'."
        ),
        metric_value=float(len(stale)),
        threshold=1.0,
        neuron_ids=nids,
    )]

    breadth = min(len(stale) / 10.0, 1.0)
    return ScoredGap(
        source="stale_neuron",
        description=(
            f"Regulatory neuron #{sample.id} '{sample.label}' has not been verified "
            f"in over 90 days. Generate a question that would test whether this "
            f"neuron's content is still current and accurate."
        ),
        context_neuron_ids=context_ids,
        evidence=evidence,
        priority_score=_compute_priority(0.7, breadth, 0.8, "stale_neuron"),
    )
