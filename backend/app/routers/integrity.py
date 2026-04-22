"""Graph integrity router — scan, review, and resolve findings.

Endpoints for all five integrity processes (homeostasis, duplicate detection,
missing connections, conflict monitoring, aging review) plus finding
management and dashboard aggregation.

All scans are dry-run by default. Graph modifications flow through the
standard AutopilotProposal → approve → apply workflow.
"""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Action, IntegrityScan, IntegrityFinding, Neuron


router = APIRouter(prefix="/admin/integrity", tags=["integrity"])


# ── Request / Response Schemas ────────────────────────────────────


class HomeostasisScanRequest(BaseModel):
    scope: str = "global"
    scale_factor: float = Field(0.8, gt=0.0, le=1.0)
    floor_threshold: float = Field(0.05, ge=0.0)
    initiated_by: str = "admin"


class DuplicateScanRequest(BaseModel):
    scope: str = "global"
    similarity_threshold: float = Field(0.92, gt=0.0, le=1.0)
    max_pairs: int = Field(100, ge=1, le=500)
    cross_department_only: bool = False
    initiated_by: str = "admin"


class ConnectionScanRequest(BaseModel):
    scope: str = "global"
    similarity_threshold: float = Field(0.65, gt=0.0, le=1.0)
    max_suggestions: int = Field(50, ge=1, le=500)
    exclude_same_parent: bool = True
    initiated_by: str = "admin"


class ConflictScanRequest(BaseModel):
    scope: str = "global"
    sim_min: float = Field(0.60, ge=0.0, le=1.0)
    sim_max: float = Field(0.85, ge=0.0, le=1.0)
    batch_size: int = Field(5, ge=1, le=20)
    max_pairs: int = Field(200, ge=1, le=1000)
    initiated_by: str = "admin"


class AgingScanRequest(BaseModel):
    scope: str = "global"
    staleness_overrides: dict | None = None
    include_never_verified: bool = True
    min_invocations: int = Field(0, ge=0)
    initiated_by: str = "admin"


class HomeostasisApplyRequest(BaseModel):
    reviewer: str = Field(..., min_length=1, max_length=100)


class FindingResolveRequest(BaseModel):
    resolution: str = Field(..., min_length=1, max_length=30)
    reviewer: str = Field(..., min_length=1, max_length=100)
    notes: str = ""


class FindingDismissRequest(BaseModel):
    reviewer: str = Field(..., min_length=1, max_length=100)
    notes: str = ""


class BulkResolveRequest(BaseModel):
    finding_ids: list[int] = Field(..., max_length=50)
    resolution: str = Field(..., min_length=1, max_length=30)
    reviewer: str = Field(..., min_length=1, max_length=100)
    notes: str = ""


class FindingProposeRequest(BaseModel):
    resolution: str = Field(..., min_length=1, max_length=30)
    reviewer: str = Field(..., min_length=1, max_length=100)
    notes: str = ""


class RunRowOut(BaseModel):
    """Unified row shape for the Dashboard run-history table.

    Merges IntegrityScan rows and Action(kind='agent.run') rows into a
    single normalized projection so the UI renders one chronological list.
    """
    id: int
    kind: str  # "scan:<scan_type>" | "agent:<agent_name>"
    started_at: str | None
    completed_at: str | None
    initiated_by: str
    state: str
    summary: str | None
    scan_scope: str | None = None
    findings_count: int | None = None
    turns: int | None = None
    tool_calls: int | None = None
    mutations: int | None = None
    errors: int | None = None


# Kinds of agent tool actions that create IntegrityFinding proposals
_AGENT_PROPOSAL_TOOL_KINDS = (
    "agent.tool.mark_duplicate",
    "agent.tool.mark_reviewed_as_unique",
)


# ── Scan Endpoints ────────────────────────────────────────────────


@router.post("/homeostasis/scan")
async def scan_homeostasis_endpoint(
    req: HomeostasisScanRequest,
    db: AsyncSession = Depends(get_db),
):
    """Dry-run weight renormalization scan."""
    from app.services.integrity.homeostasis import scan_homeostasis
    scan, result = await scan_homeostasis(
        db, scope=req.scope, scale_factor=req.scale_factor,
        floor_threshold=req.floor_threshold, initiated_by=req.initiated_by,
    )
    return _scan_response(scan, result.extra)


@router.post("/homeostasis/{scan_id}/apply")
async def apply_homeostasis_endpoint(
    scan_id: int,
    req: HomeostasisApplyRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create proposal from homeostasis scan for human review."""
    from app.services.integrity.homeostasis import apply_homeostasis
    proposal = await apply_homeostasis(db, scan_id, reviewer=req.reviewer)
    return {"proposal_id": proposal.id, "state": proposal.state,
            "item_count": len(proposal.items) if proposal.items else 0}


@router.post("/duplicates/scan")
async def scan_duplicates_endpoint(
    req: DuplicateScanRequest,
    db: AsyncSession = Depends(get_db),
):
    """Find near-duplicate neurons using embedding similarity."""
    from app.services.integrity.pattern_separation import scan_duplicates
    scan, result = await scan_duplicates(
        db, scope=req.scope, similarity_threshold=req.similarity_threshold,
        max_pairs=req.max_pairs, cross_department_only=req.cross_department_only,
        initiated_by=req.initiated_by,
    )
    return _scan_response(scan, result.extra)


@router.post("/connections/scan")
async def scan_connections_endpoint(
    req: ConnectionScanRequest,
    db: AsyncSession = Depends(get_db),
):
    """Find missing semantic connections between neurons."""
    from app.services.integrity.pattern_completion import scan_missing_connections
    scan, result = await scan_missing_connections(
        db, scope=req.scope, similarity_threshold=req.similarity_threshold,
        max_suggestions=req.max_suggestions,
        exclude_same_parent=req.exclude_same_parent,
        initiated_by=req.initiated_by,
    )
    return _scan_response(scan, result.extra)


@router.post("/conflicts/scan")
async def scan_conflicts_endpoint(
    req: ConflictScanRequest,
    db: AsyncSession = Depends(get_db),
):
    """Detect contradictions via embedding prefilter + LLM classification."""
    from app.services.integrity.conflict_monitor import scan_contradictions
    scan, result = await scan_contradictions(
        db, scope=req.scope, sim_min=req.sim_min, sim_max=req.sim_max,
        batch_size=req.batch_size, max_pairs=req.max_pairs,
        initiated_by=req.initiated_by,
    )
    return _scan_response(scan, result.extra)


@router.post("/aging/scan")
async def scan_aging_endpoint(
    req: AgingScanRequest,
    db: AsyncSession = Depends(get_db),
):
    """Surface neurons with stale content for human review."""
    from app.services.integrity.aging_review import scan_stale_neurons
    scan, result = await scan_stale_neurons(
        db, scope=req.scope, staleness_overrides=req.staleness_overrides,
        include_never_verified=req.include_never_verified,
        min_invocations=req.min_invocations, initiated_by=req.initiated_by,
    )
    return _scan_response(scan, result.extra)


# ── Scan & Finding Management ────────────────────────────────────


@router.get("/scans")
async def list_scans(
    scan_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List integrity scans, optionally filtered."""
    stmt = select(IntegrityScan).order_by(IntegrityScan.id.desc())
    if scan_type:
        stmt = stmt.where(IntegrityScan.scan_type == scan_type)
    if status:
        stmt = stmt.where(IntegrityScan.status == status)
    stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return [_scan_summary(s) for s in result.scalars().all()]


@router.get("/scans/{scan_id}")
async def get_scan(scan_id: int, db: AsyncSession = Depends(get_db)):
    """Scan detail with all findings."""
    scan = await db.get(IntegrityScan, scan_id)
    if not scan:
        raise HTTPException(404, "Scan not found")
    return _scan_detail(scan)


@router.get("/findings")
async def list_findings(
    finding_type: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """List findings with optional filters."""
    stmt = select(IntegrityFinding).order_by(IntegrityFinding.priority_score.desc())
    if finding_type:
        stmt = stmt.where(IntegrityFinding.finding_type == finding_type)
    if status:
        stmt = stmt.where(IntegrityFinding.status == status)
    if severity:
        stmt = stmt.where(IntegrityFinding.severity == severity)
    stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return [_finding_out(f) for f in result.scalars().all()]


@router.get("/findings/{finding_id}")
async def get_finding(finding_id: int, db: AsyncSession = Depends(get_db)):
    """Finding detail with full neuron content."""
    finding = await db.get(IntegrityFinding, finding_id)
    if not finding:
        raise HTTPException(404, "Finding not found")
    return await _finding_detail(db, finding)


@router.post("/findings/{finding_id}/propose")
async def propose_finding(
    finding_id: int,
    req: FindingProposeRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a proposal from a finding resolution. Graph-modifying
    resolutions (merged, differentiated, linked, a_correct, b_correct,
    context_added) flow through the proposal queue for human approval."""
    from app.services.integrity.proposals import create_integrity_proposal
    proposal = await create_integrity_proposal(
        db, finding_id, req.resolution, req.reviewer, req.notes,
    )
    return {
        "proposal_id": proposal.id,
        "state": proposal.state,
        "item_count": len(proposal.items) if proposal.items else 0,
        "gap_source": proposal.gap_source,
    }


# Resolutions that must use /propose instead of /resolve
_GRAPH_RESOLUTIONS = frozenset({
    "merged", "differentiated", "linked",
    "a_correct", "b_correct", "context_added",
})


@router.post("/findings/{finding_id}/resolve")
async def resolve_finding(
    finding_id: int,
    req: FindingResolveRequest,
    db: AsyncSession = Depends(get_db),
):
    """Resolve a finding directly (non-graph actions only).

    Graph-modifying resolutions must use /propose instead.
    """
    if req.resolution in _GRAPH_RESOLUTIONS:
        raise HTTPException(
            400,
            f"Resolution '{req.resolution}' modifies the graph — use "
            f"POST /findings/{finding_id}/propose instead.",
        )

    finding = await db.get(IntegrityFinding, finding_id)
    if not finding:
        raise HTTPException(404, "Finding not found")
    if finding.status not in ("open", "proposed"):
        raise HTTPException(400, f"Cannot resolve finding in status '{finding.status}'")

    finding.status = "resolved"
    finding.resolution = req.resolution
    finding.resolved_by = req.reviewer
    finding.resolved_at = datetime.utcnow()

    # For aging review "reviewed" resolution, update the neuron's last_verified
    if finding.finding_type == "stale_content" and req.resolution == "reviewed":
        await _mark_neurons_verified(db, finding)

    await db.commit()
    return _finding_out(finding)


@router.post("/findings/{finding_id}/dismiss")
async def dismiss_finding(
    finding_id: int,
    req: FindingDismissRequest,
    db: AsyncSession = Depends(get_db),
):
    """Dismiss a finding (acknowledged, no action needed)."""
    finding = await db.get(IntegrityFinding, finding_id)
    if not finding:
        raise HTTPException(404, "Finding not found")

    finding.status = "dismissed"
    finding.resolution = "dismissed"
    finding.resolved_by = req.reviewer
    finding.resolved_at = datetime.utcnow()

    # Dismissing a stale finding also updates last_verified
    if finding.finding_type == "stale_content":
        await _mark_neurons_verified(db, finding)

    await db.commit()
    return _finding_out(finding)


@router.post("/findings/bulk-resolve")
async def bulk_resolve_findings(
    req: BulkResolveRequest,
    db: AsyncSession = Depends(get_db),
):
    """Batch-resolve multiple findings."""
    results = []
    for fid in req.finding_ids:
        finding = await db.get(IntegrityFinding, fid)
        if finding and finding.status in ("open", "proposed"):
            finding.status = "resolved"
            finding.resolution = req.resolution
            finding.resolved_by = req.reviewer
            finding.resolved_at = datetime.utcnow()
            if finding.finding_type == "stale_content" and req.resolution == "reviewed":
                await _mark_neurons_verified(db, finding)
            results.append({"id": fid, "status": "resolved"})
        else:
            results.append({"id": fid, "status": "skipped"})
    await db.commit()
    return {"resolved": results}


@router.get("/dashboard")
async def integrity_dashboard(db: AsyncSession = Depends(get_db)):
    """Aggregate stats: open findings by type, recent scans."""
    # Open findings by type
    type_counts = await db.execute(
        select(IntegrityFinding.finding_type, func.count(IntegrityFinding.id))
        .where(IntegrityFinding.status == "open")
        .group_by(IntegrityFinding.finding_type)
    )
    open_by_type = {row[0]: row[1] for row in type_counts.all()}

    # Open findings by severity
    sev_counts = await db.execute(
        select(IntegrityFinding.severity, func.count(IntegrityFinding.id))
        .where(IntegrityFinding.status == "open")
        .group_by(IntegrityFinding.severity)
    )
    open_by_severity = {row[0]: row[1] for row in sev_counts.all()}

    # Total open
    total_open = sum(open_by_type.values())

    # Recent scans (last 10)
    recent = await db.execute(
        select(IntegrityScan).order_by(IntegrityScan.id.desc()).limit(10)
    )
    recent_scans = [_scan_summary(s) for s in recent.scalars().all()]

    return {
        "open_findings_total": total_open,
        "open_by_type": open_by_type,
        "open_by_severity": open_by_severity,
        "recent_scans": recent_scans,
    }


@router.get("/runs", response_model=list[RunRowOut])
async def list_integrity_runs(
    limit: int = 50,
    kind_filter: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[RunRowOut]:
    """Unified chronological feed of IntegrityScans + agent runs.

    kind_filter: 'scan' keeps only IntegrityScan rows, 'agent' keeps only
    agent runs, None merges both. Rows sort by completed_at desc (nulls
    fall back to started_at). Bounded by limit (1..500).
    """
    assert 1 <= limit <= 500, "limit must be 1..500"
    fetch = min(limit * 2, 500)
    rows: list[RunRowOut] = []

    if kind_filter in (None, "scan"):
        scan_stmt = (
            select(IntegrityScan)
            .order_by(IntegrityScan.id.desc())
            .limit(fetch)
        )
        for scan in (await db.execute(scan_stmt)).scalars().all():
            rows.append(_scan_to_run_row(scan))

    if kind_filter in (None, "agent"):
        agent_stmt = (
            select(Action)
            .where(Action.kind == "agent.run")
            .order_by(Action.id.desc())
            .limit(fetch)
        )
        for action in (await db.execute(agent_stmt)).scalars().all():
            rows.append(_agent_run_to_row(action))

    rows.sort(key=_run_row_sort_key, reverse=True)
    return rows[:limit]


# ── Helpers ───────────────────────────────────────────────────────


async def _mark_neurons_verified(
    db: AsyncSession, finding: IntegrityFinding,
) -> None:
    """Update last_verified on neurons referenced by a finding."""
    if not finding.neuron_ids_json:
        return
    neuron_ids = json.loads(finding.neuron_ids_json)
    now = datetime.utcnow()
    for nid in neuron_ids:
        neuron = await db.get(Neuron, nid)
        if neuron:
            neuron.last_verified = now


def _scan_summary(scan: IntegrityScan) -> dict:
    """Compact scan representation for lists."""
    return {
        "id": scan.id, "scan_type": scan.scan_type,
        "scope": scan.scope, "status": scan.status,
        "findings_count": scan.findings_count,
        "initiated_by": scan.initiated_by,
        "started_at": scan.started_at.isoformat() if scan.started_at else None,
        "completed_at": scan.completed_at.isoformat() if scan.completed_at else None,
    }


def _scan_detail(scan: IntegrityScan) -> dict:
    """Full scan with findings."""
    return {
        **_scan_summary(scan),
        "parameters": json.loads(scan.parameters_json) if scan.parameters_json else {},
        "findings": [_finding_out(f) for f in (scan.findings or [])],
    }


def _scan_response(scan: IntegrityScan, extra: dict) -> dict:
    """Scan response combining summary and extra data."""
    return {**_scan_summary(scan), **extra}


def _finding_out(finding: IntegrityFinding) -> dict:
    """Compact finding representation."""
    return {
        "id": finding.id, "scan_id": finding.scan_id,
        "finding_type": finding.finding_type,
        "severity": finding.severity,
        "priority_score": finding.priority_score,
        "description": finding.description,
        "status": finding.status,
        "resolution": finding.resolution,
        "proposal_id": finding.proposal_id,
        "resolved_by": finding.resolved_by,
        "resolved_at": finding.resolved_at.isoformat() if finding.resolved_at else None,
        "neuron_ids": json.loads(finding.neuron_ids_json) if finding.neuron_ids_json else [],
        "created_at": finding.created_at.isoformat() if finding.created_at else None,
    }


async def _finding_detail(db: AsyncSession, finding: IntegrityFinding) -> dict:
    """Full finding with neuron content loaded."""
    base = _finding_out(finding)
    base["detail"] = json.loads(finding.detail_json) if finding.detail_json else {}
    base["edge_ids"] = json.loads(finding.edge_ids_json) if finding.edge_ids_json else []

    # Load neuron content for referenced neurons
    neuron_ids = json.loads(finding.neuron_ids_json) if finding.neuron_ids_json else []
    neurons = {}
    for nid in neuron_ids[:10]:  # Cap to avoid excessive queries
        neuron = await db.get(Neuron, nid)
        if neuron:
            neurons[nid] = {
                "id": neuron.id, "label": neuron.label,
                "department": neuron.department, "layer": neuron.layer,
                "content": neuron.content, "summary": neuron.summary,
                "source_type": neuron.source_type,
                "source_origin": neuron.source_origin,
                "invocations": neuron.invocations,
            }
    base["neurons"] = neurons

    # Reverse-lookup: was this finding's proposal created by an agent?
    # Scan the most recent applied agent tool actions and match finding_id
    # in input_json. Bounded (50 rows) — scales fine for audit-trail use.
    agent_stmt = (
        select(Action)
        .where(
            Action.kind.in_(_AGENT_PROPOSAL_TOOL_KINDS),
            Action.state == "applied",
        )
        .order_by(Action.id.desc())
        .limit(50)
    )
    agent_run_id: int | None = None
    agent_name: str | None = None
    for act in (await db.execute(agent_stmt)).scalars().all():
        payload = act.input_json or {}
        if payload.get("finding_id") == finding.id:
            agent_run_id = act.parent_action_id
            # actor_id on the child = the agent name (runtime sets it)
            agent_name = act.actor_id
            # If parent missing actor_id, fall back to looking up parent
            if agent_run_id is not None and not agent_name:
                parent = await db.get(Action, agent_run_id)
                if parent is not None:
                    agent_name = parent.actor_id
            break
    base["created_by_agent_run_id"] = agent_run_id
    base["created_by_agent_name"] = agent_name
    return base


def _scan_to_run_row(scan: IntegrityScan) -> RunRowOut:
    """Project an IntegrityScan row onto the unified RunRowOut shape."""
    return RunRowOut(
        id=scan.id,
        kind=f"scan:{scan.scan_type}",
        started_at=scan.started_at.isoformat() if scan.started_at else None,
        completed_at=scan.completed_at.isoformat() if scan.completed_at else None,
        initiated_by=scan.initiated_by or "unknown",
        state=scan.status,
        summary=None,
        scan_scope=scan.scope,
        findings_count=scan.findings_count,
    )


def _agent_run_to_row(action: Action) -> RunRowOut:
    """Project an agent.run Action onto the unified RunRowOut shape."""
    result = action.result_json or {}
    payload = action.input_json or {}
    started = action.created_at.isoformat() if action.created_at else None
    completed = action.applied_at.isoformat() if action.applied_at else None
    return RunRowOut(
        id=action.id,
        kind=f"agent:{action.actor_id or 'unknown'}",
        started_at=started,
        completed_at=completed,
        initiated_by=str(payload.get("triggered_by", "unknown")),
        state=action.state,
        summary=result.get("summary"),
        turns=result.get("turns"),
        tool_calls=result.get("tool_calls"),
        mutations=result.get("mutations"),
        errors=result.get("errors"),
    )


def _run_row_sort_key(row: RunRowOut) -> str:
    """Sort unified rows newest-first on completed_at, falling back to
    started_at so in-progress rows still land near the top. Empty string
    for both sinks to the bottom."""
    return row.completed_at or row.started_at or ""
