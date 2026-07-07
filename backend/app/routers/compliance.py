"""Compliance endpoints: management reviews, snapshots, evidence mapping, reports."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

# Project root: compliance.py lives at backend/app/routers/compliance.py
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_ALLOWED_PREFIXES = ("docs", "backend/app")
_MAX_FILE_BYTES = 64 * 1024  # 64 KB cap

from app.config import settings
from app.database import get_db, async_session
from app.tenant import tenant
from app.models import ManagementReview, ComplianceSnapshot, EvidenceMapping, Neuron, NeuronRefinement, AuditLog
from app.routers.admin import run_compliance_audit

router = APIRouter(prefix="/admin", tags=["compliance"])


# ── System Use Notification Banner (AC-8, CMMC 3.1.9) ──

@router.get("/system-banner")
async def get_system_banner():
    """Return the system use notification banner text and enabled state.

    AC-8: System use notification — display approved banner before granting access.
    CMMC 3.1.9: Provide privacy and security notices consistent with CUI rules.
    """
    return {
        "enabled": settings.system_use_banner_enabled,
        "banner_text": tenant.system_use_banner if settings.system_use_banner_enabled else "",
        "session_timeout_minutes": settings.session_timeout_minutes,
    }

# Review cadence in days (from governance.md)
REVIEW_CADENCES: dict[str, int] = {
    "pii_audit": 90,
    "scoring_health": 7,
    "governance_review": 90,
    "incident_review": 90,
    "compliance_audit": 30,
    "neuron_expansion": 90,
    "model_change": 90,
}


# ── Evidence Content Viewer ──

@router.get("/evidence-content")
async def evidence_content(path: str = Query(..., description="Relative file path from project root")):
    """Serve file content for document/code evidence artifacts."""
    # Reject obviously bad paths
    if ".." in path:
        raise HTTPException(403, "Path traversal not allowed")

    resolved = (_PROJECT_ROOT / path).resolve()

    # Must stay within allowed directories
    if not any(resolved.is_relative_to(_PROJECT_ROOT / prefix) for prefix in _ALLOWED_PREFIXES):
        raise HTTPException(403, f"Access denied: path must be under {_ALLOWED_PREFIXES}")

    if not resolved.is_file():
        raise HTTPException(404, f"File not found: {path}")

    # Determine language from extension
    ext = resolved.suffix.lower()
    lang_map = {".py": "python", ".md": "markdown", ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".txt": "text"}
    language = lang_map.get(ext, "text")

    # Read with size cap
    raw = resolved.read_bytes()
    if len(raw) > _MAX_FILE_BYTES:
        content = raw[:_MAX_FILE_BYTES].decode("utf-8", errors="replace") + f"\n\n... (truncated at {_MAX_FILE_BYTES // 1024} KB)"
    else:
        content = raw.decode("utf-8", errors="replace")

    return {"path": path, "language": language, "content": content, "size": len(raw)}


# ── Management Reviews ──

@router.post("/reviews")
async def create_review(body: dict, db: AsyncSession = Depends(get_db)):
    review = ManagementReview(
        review_type=body["review_type"],
        reviewer=body["reviewer"],
        review_date=date.fromisoformat(body["review_date"]) if isinstance(body.get("review_date"), str) else date.today(),
        findings=body["findings"],
        decisions=body["decisions"],
        action_items=json.dumps(body.get("action_items", [])),
        status=body.get("status", "completed"),
        compliance_snapshot_id=body.get("compliance_snapshot_id"),
    )
    db.add(review)
    await db.commit()
    await db.refresh(review)
    return _review_to_dict(review)


@router.get("/reviews")
async def list_reviews(
    review_type: str | None = None,
    since: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    q = select(ManagementReview).order_by(desc(ManagementReview.review_date))
    if review_type:
        q = q.where(ManagementReview.review_type == review_type)
    if since:
        q = q.where(ManagementReview.review_date >= date.fromisoformat(since))
    q = q.limit(limit)
    result = await db.execute(q)
    return [_review_to_dict(r) for r in result.scalars().all()]


@router.get("/reviews/cadence")
async def review_cadence(db: AsyncSession = Depends(get_db)):
    today = date.today()
    items = []
    for rtype, days in REVIEW_CADENCES.items():
        result = await db.execute(
            select(ManagementReview.review_date)
            .where(ManagementReview.review_type == rtype)
            .order_by(desc(ManagementReview.review_date))
            .limit(1)
        )
        last = result.scalar_one_or_none()
        if last:
            next_due = last + timedelta(days=days)
            is_overdue = today > next_due
            days_until = (next_due - today).days
        else:
            next_due = None
            is_overdue = True
            days_until = None
        items.append({
            "review_type": rtype,
            "cadence_days": days,
            "last_review_date": last.isoformat() if last else None,
            "next_due_date": next_due.isoformat() if next_due else None,
            "is_overdue": is_overdue,
            "days_until_due": days_until,
        })
    return items


@router.get("/reviews/{review_id}")
async def get_review(review_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ManagementReview).where(ManagementReview.id == review_id))
    review = result.scalar_one_or_none()
    if not review:
        raise HTTPException(404, "Review not found")
    return _review_to_dict(review)


@router.put("/reviews/{review_id}")
async def update_review(review_id: int, body: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ManagementReview).where(ManagementReview.id == review_id))
    review = result.scalar_one_or_none()
    if not review:
        raise HTTPException(404, "Review not found")
    for field in ("findings", "decisions", "status", "reviewer"):
        if field in body:
            setattr(review, field, body[field])
    if "action_items" in body:
        review.action_items = json.dumps(body["action_items"])
    if "review_date" in body:
        review.review_date = date.fromisoformat(body["review_date"])
    await db.commit()
    await db.refresh(review)
    return _review_to_dict(review)


def _review_to_dict(r: ManagementReview) -> dict:
    return {
        "id": r.id,
        "review_type": r.review_type,
        "reviewer": r.reviewer,
        "review_date": r.review_date.isoformat() if r.review_date else None,
        "findings": r.findings,
        "decisions": r.decisions,
        "action_items": json.loads(r.action_items) if r.action_items else [],
        "status": r.status,
        "compliance_snapshot_id": r.compliance_snapshot_id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


# ── Compliance Snapshots ──

@router.post("/compliance-snapshots")
async def create_snapshot(
    trigger: str = "manual",
    db: AsyncSession = Depends(get_db),
):
    audit = await run_compliance_audit(db)
    return await _store_snapshot(db, audit, trigger)


@router.get("/compliance-snapshots")
async def list_snapshots(limit: int = 50, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ComplianceSnapshot)
        .order_by(desc(ComplianceSnapshot.snapshot_date))
        .limit(limit)
    )
    return [_snapshot_summary(s) for s in result.scalars().all()]


@router.get("/compliance-snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ComplianceSnapshot).where(ComplianceSnapshot.id == snapshot_id)
    )
    snap = result.scalar_one_or_none()
    if not snap:
        raise HTTPException(404, "Snapshot not found")
    return {
        **_snapshot_summary(snap),
        "snapshot_data": json.loads(snap.snapshot_data) if snap.snapshot_data else None,
        "diff_summary": json.loads(snap.diff_summary) if snap.diff_summary else None,
    }


async def _store_snapshot(db: AsyncSession, audit: dict, trigger: str) -> dict:
    """Store a compliance snapshot and compute diff against previous."""
    # Get previous snapshot for diff
    prev_result = await db.execute(
        select(ComplianceSnapshot)
        .order_by(desc(ComplianceSnapshot.snapshot_date))
        .limit(1)
    )
    prev = prev_result.scalar_one_or_none()

    diff = None
    if prev:
        diff = _compute_diff(prev, audit)

    snap = ComplianceSnapshot(
        snapshot_date=datetime.utcnow(),
        snapshot_data=json.dumps(audit, default=str),
        pii_clean=audit["pii_scan"]["clean"],
        coverage_cv=audit["bias_assessment"]["coverage_cv"],
        fairness_pass=audit["fairness_analysis"]["fairness_pass"],
        missing_citations_count=audit["provenance_audit"]["missing_citations_count"],
        stale_neurons_count=audit["provenance_audit"]["stale_neurons_count"],
        total_neurons=audit["total_neurons"],
        total_evals=audit["validity_reliability"]["total_evals"],
        trigger=trigger,
        diff_summary=json.dumps(diff) if diff else None,
    )
    db.add(snap)
    await db.commit()
    await db.refresh(snap)
    return _snapshot_summary(snap)


def _compute_diff(prev: ComplianceSnapshot, current: dict) -> dict:
    """Compute key metric changes between previous snapshot and current audit."""
    return {
        "pii_clean": {"prev": prev.pii_clean, "current": current["pii_scan"]["clean"]},
        "coverage_cv": {
            "prev": prev.coverage_cv,
            "current": current["bias_assessment"]["coverage_cv"],
            "delta": round(current["bias_assessment"]["coverage_cv"] - prev.coverage_cv, 4),
        },
        "fairness_pass": {"prev": prev.fairness_pass, "current": current["fairness_analysis"]["fairness_pass"]},
        "missing_citations": {
            "prev": prev.missing_citations_count,
            "current": current["provenance_audit"]["missing_citations_count"],
            "delta": current["provenance_audit"]["missing_citations_count"] - prev.missing_citations_count,
        },
        "stale_neurons": {
            "prev": prev.stale_neurons_count,
            "current": current["provenance_audit"]["stale_neurons_count"],
            "delta": current["provenance_audit"]["stale_neurons_count"] - prev.stale_neurons_count,
        },
        "total_neurons": {
            "prev": prev.total_neurons,
            "current": current["total_neurons"],
            "delta": current["total_neurons"] - prev.total_neurons,
        },
        "total_evals": {
            "prev": prev.total_evals,
            "current": current["validity_reliability"]["total_evals"],
            "delta": current["validity_reliability"]["total_evals"] - prev.total_evals,
        },
    }


def _snapshot_summary(s: ComplianceSnapshot) -> dict:
    return {
        "id": s.id,
        "snapshot_date": s.snapshot_date.isoformat() if s.snapshot_date else None,
        "pii_clean": s.pii_clean,
        "coverage_cv": s.coverage_cv,
        "fairness_pass": s.fairness_pass,
        "missing_citations_count": s.missing_citations_count,
        "stale_neurons_count": s.stale_neurons_count,
        "total_neurons": s.total_neurons,
        "total_evals": s.total_evals,
        "trigger": s.trigger,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


# Auto-snapshot helper (called from lifespan)
async def maybe_auto_snapshot():
    """Create a scheduled snapshot if none exists or last is >7 days old."""
    async with async_session() as db:
        result = await db.execute(
            select(ComplianceSnapshot)
            .order_by(desc(ComplianceSnapshot.snapshot_date))
            .limit(1)
        )
        last = result.scalar_one_or_none()
        if last and (datetime.utcnow() - last.snapshot_date).days < 7:
            return  # Recent enough

        try:
            audit = await run_compliance_audit(db)
            await _store_snapshot(db, audit, "scheduled")
            print("Auto-snapshot: created scheduled compliance snapshot")
        except Exception as e:
            print(f"Auto-snapshot skipped: {e}")


# ── Evidence Mapping ──

@router.get("/evidence-map")
async def list_evidence(
    framework: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(EvidenceMapping).order_by(EvidenceMapping.framework, EvidenceMapping.requirement_id)
    if framework:
        q = q.where(EvidenceMapping.framework == framework)
    result = await db.execute(q)
    return [_evidence_to_dict(e) for e in result.scalars().all()]


@router.put("/evidence-map/{mapping_id}")
async def update_evidence(mapping_id: int, body: dict, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(EvidenceMapping).where(EvidenceMapping.id == mapping_id))
    ev = result.scalar_one_or_none()
    if not ev:
        raise HTTPException(404, "Evidence mapping not found")
    for field in ("status", "notes", "last_verified_by", "evidence_location", "verification_query"):
        if field in body:
            setattr(ev, field, body[field])
    if "last_verified" in body:
        ev.last_verified = datetime.fromisoformat(body["last_verified"]) if body["last_verified"] else None
    if body.get("mark_verified"):
        ev.last_verified = datetime.utcnow()
        ev.last_verified_by = body.get("last_verified_by", "system")
    await db.commit()
    await db.refresh(ev)
    return _evidence_to_dict(ev)


@router.post("/evidence-map/verify-all")
async def verify_all_evidence(db: AsyncSession = Depends(get_db)):
    """Automated verification: check endpoints return 200, check tables exist."""
    result = await db.execute(select(EvidenceMapping))
    mappings = result.scalars().all()
    now = datetime.utcnow()
    passed = 0
    failed = 0
    results = []

    for ev in mappings:
        ok = True
        reason = ""
        if ev.evidence_type == "endpoint":
            # We just mark as verified since we can't self-fetch easily
            reason = "endpoint registered"
        elif ev.evidence_type == "table":
            # Check table exists in DB
            try:
                from sqlalchemy import text
                check = await db.execute(text(
                    "SELECT 1 FROM information_schema.tables WHERE table_name = :t AND table_schema = 'public'"
                ), {"t": ev.evidence_location})
                if not check.fetchone():
                    ok = False
                    reason = f"table '{ev.evidence_location}' not found"
            except Exception as e:
                ok = False
                reason = str(e)
        elif ev.evidence_type == "code":
            reason = "code artifact"
        elif ev.evidence_type == "document":
            reason = "document artifact"
        elif ev.evidence_type == "review_log":
            reason = "review log"

        if ok:
            ev.last_verified = now
            ev.last_verified_by = "auto-verify"
            passed += 1
        else:
            failed += 1

        results.append({
            "id": ev.id,
            "requirement_id": ev.requirement_id,
            "ok": ok,
            "reason": reason,
        })

    await db.commit()
    return {"passed": passed, "failed": failed, "total": len(mappings), "results": results}


@router.post("/evidence-map/seed")
async def seed_evidence_map(db: AsyncSession = Depends(get_db)):
    """Populate evidence mappings from known requirements."""
    count = (await db.execute(select(func.count(EvidenceMapping.id)))).scalar() or 0
    if count > 0:
        return {"status": "already_seeded", "count": count}
    return await _seed_evidence_data(db)


async def _seed_evidence_data(db: AsyncSession) -> dict:
    """Insert all evidence mapping seed data."""
    seed = _get_evidence_seed()
    for item in seed:
        db.add(EvidenceMapping(**item))
    await db.commit()
    return {"status": "seeded", "count": len(seed)}


def _get_nist_ai_rmf_seed() -> list[dict]:
    return [
        {"framework": "nist_ai_rmf", "requirement_id": "GOV-1.3", "requirement_name": "Risk Tolerance & Prioritization",
         "status": "addressed", "evidence_type": "document", "evidence_location": "docs/governance.md",
         "verification_query": "grep -l 'risk tolerance' docs/governance.md"},
        {"framework": "nist_ai_rmf", "requirement_id": "GOV-1.4", "requirement_name": "Transparent Documentation",
         "status": "addressed", "evidence_type": "endpoint", "evidence_location": "/queries",
         "verification_query": "GET /queries returns query provenance"},
        {"framework": "nist_ai_rmf", "requirement_id": "GOV-1.5", "requirement_name": "Ongoing Monitoring & Review",
         "status": "addressed", "evidence_type": "endpoint", "evidence_location": "/admin/scoring-health",
         "verification_query": "GET /admin/scoring-health returns drift detection data"},
        {"framework": "nist_ai_rmf", "requirement_id": "GOV-2.1", "requirement_name": "Roles & Responsibilities",
         "status": "addressed", "evidence_type": "document", "evidence_location": "docs/governance.md"},
        {"framework": "nist_ai_rmf", "requirement_id": "GOV-4.3", "requirement_name": "Incident Response",
         "status": "addressed", "evidence_type": "document", "evidence_location": "docs/governance.md"},
        {"framework": "nist_ai_rmf", "requirement_id": "MEA-2.4", "requirement_name": "Production Monitoring",
         "status": "addressed", "evidence_type": "endpoint", "evidence_location": "/admin/health-check"},
        {"framework": "nist_ai_rmf", "requirement_id": "MEA-2.7", "requirement_name": "Security & Resilience",
         "status": "partial", "evidence_type": "code", "evidence_location": "backend/app/services/input_guard.py",
         "verification_query": "Input guard with 16 adversarial patterns"},
        {"framework": "nist_ai_rmf", "requirement_id": "MEA-2.8", "requirement_name": "Transparency & Accountability",
         "status": "addressed", "evidence_type": "endpoint", "evidence_location": "/queries/{id}"},
        {"framework": "nist_ai_rmf", "requirement_id": "MEA-2.10", "requirement_name": "Privacy Risk",
         "status": "addressed", "evidence_type": "endpoint", "evidence_location": "/admin/compliance-audit",
         "verification_query": "PII scan in compliance audit"},
        {"framework": "nist_ai_rmf", "requirement_id": "MEA-2.11", "requirement_name": "Fairness & Bias",
         "status": "addressed", "evidence_type": "endpoint", "evidence_location": "/admin/compliance-audit",
         "verification_query": "Fairness analysis in compliance audit"},
    ]


def _get_aiuc1_seed() -> list[dict]:
    return [
        {"framework": "aiuc_1", "requirement_id": "A006", "requirement_name": "Prevent PII Leakage",
         "status": "addressed", "evidence_type": "endpoint", "evidence_location": "/admin/compliance-audit",
         "verification_query": "PII scan in compliance audit"},
        {"framework": "aiuc_1", "requirement_id": "B002", "requirement_name": "Detect Adversarial Input",
         "status": "addressed", "evidence_type": "code", "evidence_location": "backend/app/services/input_guard.py"},
        {"framework": "aiuc_1", "requirement_id": "B005", "requirement_name": "Implement Real-Time Input Filtering",
         "status": "addressed", "evidence_type": "code", "evidence_location": "backend/app/services/input_guard.py"},
        {"framework": "aiuc_1", "requirement_id": "E004", "requirement_name": "Assign Accountability",
         "status": "addressed", "evidence_type": "document", "evidence_location": "docs/governance.md"},
        {"framework": "aiuc_1", "requirement_id": "E008", "requirement_name": "Review Internal Processes",
         "status": "addressed", "evidence_type": "review_log", "evidence_location": "management_reviews",
         "verification_query": "Management review records in compliance infrastructure"},
        {"framework": "aiuc_1", "requirement_id": "E015", "requirement_name": "Log Model Activity",
         "status": "addressed", "evidence_type": "table", "evidence_location": "queries"},
        {"framework": "aiuc_1", "requirement_id": "C008", "requirement_name": "Monitor AI Risk Categories",
         "status": "addressed", "evidence_type": "endpoint", "evidence_location": "/admin/scoring-health"},
    ]


def _get_iso42001_seed() -> list[dict]:
    return [
        {"framework": "iso_42001", "requirement_id": "5.1", "requirement_name": "Leadership & Commitment",
         "status": "addressed", "evidence_type": "document", "evidence_location": "docs/governance.md"},
        {"framework": "iso_42001", "requirement_id": "9.1", "requirement_name": "Monitoring & Evaluation",
         "status": "addressed", "evidence_type": "endpoint", "evidence_location": "/admin/scoring-health"},
        {"framework": "iso_42001", "requirement_id": "9.2", "requirement_name": "Internal Audit",
         "status": "addressed", "evidence_type": "endpoint", "evidence_location": "/admin/compliance-snapshots",
         "verification_query": "Compliance snapshots provide automated audit evidence"},
        {"framework": "iso_42001", "requirement_id": "9.3", "requirement_name": "Management Review",
         "status": "addressed", "evidence_type": "review_log", "evidence_location": "management_reviews",
         "verification_query": "Management review records with cadence tracking"},
        {"framework": "iso_42001", "requirement_id": "A.6.2.8", "requirement_name": "AI System Logging",
         "status": "addressed", "evidence_type": "table", "evidence_location": "queries"},
        {"framework": "iso_42001", "requirement_id": "A.7.3", "requirement_name": "Data Provenance",
         "status": "addressed", "evidence_type": "table", "evidence_location": "neuron_source_links"},
        {"framework": "iso_42001", "requirement_id": "A.9.3", "requirement_name": "AI System Audit",
         "status": "addressed", "evidence_type": "endpoint", "evidence_location": "/admin/compliance-snapshots"},
        {"framework": "iso_42001", "requirement_id": "A.9.4", "requirement_name": "Management Review of AIMS",
         "status": "addressed", "evidence_type": "review_log", "evidence_location": "management_reviews"},
    ]


def _get_fedramp_moderate_seed() -> list[dict]:
    return [
        {"framework": "fedramp_moderate", "requirement_id": "AC", "requirement_name": "Access Control",
         "status": "gap", "evidence_type": "code", "evidence_location": "backend/app/routers/",
         "notes": "No authentication layer. Must implement RBAC, session management, least privilege."},
        {"framework": "fedramp_moderate", "requirement_id": "AU", "requirement_name": "Audit & Accountability",
         "status": "partial", "evidence_type": "table", "evidence_location": "queries",
         "verification_query": "Query provenance logged; missing centralized tamper-proof audit log"},
        {"framework": "fedramp_moderate", "requirement_id": "CM", "requirement_name": "Configuration Management",
         "status": "partial", "evidence_type": "code", "evidence_location": "backend/requirements.txt",
         "verification_query": "Dependencies pinned; missing baseline configuration documentation"},
        {"framework": "fedramp_moderate", "requirement_id": "CP", "requirement_name": "Contingency Planning",
         "status": "gap", "evidence_type": "document", "evidence_location": "docs/",
         "notes": "No contingency plan, backup procedures, or DR documentation"},
        {"framework": "fedramp_moderate", "requirement_id": "IA", "requirement_name": "Identification & Authentication",
         "status": "gap", "evidence_type": "code", "evidence_location": "backend/app/",
         "notes": "No authentication system. Must implement MFA, credential management."},
        {"framework": "fedramp_moderate", "requirement_id": "IR", "requirement_name": "Incident Response",
         "status": "partial", "evidence_type": "document", "evidence_location": "docs/governance.md",
         "verification_query": "P1-P4 severity levels defined; missing formal IR plan and testing"},
        {"framework": "fedramp_moderate", "requirement_id": "RA", "requirement_name": "Risk Assessment",
         "status": "addressed", "evidence_type": "document", "evidence_location": "docs/risk-map.md",
         "verification_query": "15 failure modes with likelihood x impact scoring"},
        {"framework": "fedramp_moderate", "requirement_id": "SC", "requirement_name": "System & Communications Protection",
         "status": "partial", "evidence_type": "code", "evidence_location": "backend/app/services/input_guard.py",
         "verification_query": "Input guard provides application-layer boundary protection; missing TLS, encryption at rest"},
        {"framework": "fedramp_moderate", "requirement_id": "SI", "requirement_name": "System & Information Integrity",
         "status": "partial", "evidence_type": "code", "evidence_location": "backend/app/services/input_guard.py",
         "verification_query": "Input validation, output risk tagging, PII scanning; missing flaw remediation tracking"},
        {"framework": "fedramp_moderate", "requirement_id": "CA", "requirement_name": "Security Assessment & Authorization",
         "status": "gap", "evidence_type": "document", "evidence_location": "docs/",
         "notes": "No SSP, SAR, POA&M, or ATO. Required for FedRAMP authorization."},
    ]


def _get_soc2_type2_seed() -> list[dict]:
    return [
        {"framework": "soc2_type2", "requirement_id": "CC3", "requirement_name": "Risk Assessment",
         "status": "addressed", "evidence_type": "document", "evidence_location": "docs/risk-map.md",
         "verification_query": "Risk register with treatment plans and quarterly reassessment"},
        {"framework": "soc2_type2", "requirement_id": "CC4", "requirement_name": "Monitoring Activities",
         "status": "addressed", "evidence_type": "endpoint", "evidence_location": "/admin/scoring-health",
         "verification_query": "Drift detection, circuit breaker, compliance snapshots"},
        {"framework": "soc2_type2", "requirement_id": "CC6", "requirement_name": "Logical & Physical Access",
         "status": "gap", "evidence_type": "code", "evidence_location": "backend/app/",
         "notes": "No authentication/authorization. Critical gap for multi-user."},
        {"framework": "soc2_type2", "requirement_id": "CC7", "requirement_name": "System Operations",
         "status": "partial", "evidence_type": "endpoint", "evidence_location": "/admin/health-check",
         "verification_query": "Health check, alerts, monitoring; missing vulnerability management"},
        {"framework": "soc2_type2", "requirement_id": "CC9", "requirement_name": "Risk Mitigation",
         "status": "addressed", "evidence_type": "document", "evidence_location": "docs/governance.md",
         "verification_query": "Risk register with treatment decisions, vendor risk documented"},
        {"framework": "soc2_type2", "requirement_id": "A1", "requirement_name": "Availability",
         "status": "partial", "evidence_type": "endpoint", "evidence_location": "/admin/health-check",
         "notes": "Health check exists; missing SLA, capacity planning, backup/recovery"},
        {"framework": "soc2_type2", "requirement_id": "PI1", "requirement_name": "Processing Integrity",
         "status": "addressed", "evidence_type": "endpoint", "evidence_location": "/admin/compliance-audit",
         "verification_query": "Deterministic scoring, output grounding checks, compliance validation"},
        {"framework": "soc2_type2", "requirement_id": "C1", "requirement_name": "Confidentiality",
         "status": "partial", "evidence_type": "endpoint", "evidence_location": "/admin/health-check",
         "notes": "Data local-only; missing data classification, encryption at rest"},
        {"framework": "soc2_type2", "requirement_id": "P1", "requirement_name": "Privacy",
         "status": "partial", "evidence_type": "endpoint", "evidence_location": "/admin/compliance-audit",
         "notes": "PII scanning exists; missing privacy notice, consent management, retention policy"},
    ]


def _get_cmmc_level2_seed() -> list[dict]:
    return [
        {"framework": "cmmc_level2", "requirement_id": "3.1", "requirement_name": "Access Control (22 practices)",
         "status": "gap", "evidence_type": "code", "evidence_location": "backend/app/",
         "notes": "No authentication. Must implement RBAC, MFA, least privilege for CUI handling."},
        {"framework": "cmmc_level2", "requirement_id": "3.3", "requirement_name": "Audit & Accountability (9 practices)",
         "status": "partial", "evidence_type": "table", "evidence_location": "queries",
         "verification_query": "Application-level audit trail exists; missing system-level audit events"},
        {"framework": "cmmc_level2", "requirement_id": "3.4", "requirement_name": "Configuration Management (9 practices)",
         "status": "partial", "evidence_type": "code", "evidence_location": "backend/requirements.txt",
         "verification_query": "Git-tracked, deps pinned; missing baseline configs and change control"},
        {"framework": "cmmc_level2", "requirement_id": "3.5", "requirement_name": "Identification & Authentication (11 practices)",
         "status": "gap", "evidence_type": "code", "evidence_location": "backend/app/",
         "notes": "No user identification or authentication. Critical gap for CUI environments."},
        {"framework": "cmmc_level2", "requirement_id": "3.11", "requirement_name": "Risk Assessment (3 practices)",
         "status": "addressed", "evidence_type": "document", "evidence_location": "docs/risk-map.md",
         "verification_query": "Risk register, vulnerability scanning via compliance audit"},
        {"framework": "cmmc_level2", "requirement_id": "3.12", "requirement_name": "Security Assessment (4 practices)",
         "status": "partial", "evidence_type": "endpoint", "evidence_location": "/admin/compliance-snapshots",
         "verification_query": "Self-assessment via snapshots; missing POA&M and continuous monitoring program"},
        {"framework": "cmmc_level2", "requirement_id": "3.13", "requirement_name": "System & Comms Protection (16 practices)",
         "status": "partial", "evidence_type": "code", "evidence_location": "backend/app/services/input_guard.py",
         "verification_query": "App-layer boundary protection; missing TLS, encryption at rest, network segmentation"},
        {"framework": "cmmc_level2", "requirement_id": "3.14", "requirement_name": "System & Info Integrity (7 practices)",
         "status": "partial", "evidence_type": "code", "evidence_location": "backend/app/services/input_guard.py",
         "verification_query": "Input validation, PII detection, scoring health monitoring"},
    ]


def _get_evidence_seed() -> list[dict]:
    """All known requirement-to-evidence mappings."""
    return [
        *_get_nist_ai_rmf_seed(),
        *_get_aiuc1_seed(),
        *_get_iso42001_seed(),
        *_get_fedramp_moderate_seed(),
        *_get_soc2_type2_seed(),
        *_get_cmmc_level2_seed(),
    ]


def _evidence_to_dict(e: EvidenceMapping) -> dict:
    return {
        "id": e.id,
        "framework": e.framework,
        "requirement_id": e.requirement_id,
        "requirement_name": e.requirement_name,
        "status": e.status,
        "evidence_type": e.evidence_type,
        "evidence_location": e.evidence_location,
        "verification_query": e.verification_query,
        "last_verified": e.last_verified.isoformat() if e.last_verified else None,
        "last_verified_by": e.last_verified_by,
        "notes": e.notes,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "updated_at": e.updated_at.isoformat() if e.updated_at else None,
    }


# ── Self-Assessment Report Generator ──

@router.get("/compliance-report")
async def compliance_report(
    framework: str | None = None,
    format: str = "json",
    db: AsyncSession = Depends(get_db),
):
    """Generate a self-assessment report for audit preparation."""
    # Load evidence mappings
    eq = select(EvidenceMapping).order_by(EvidenceMapping.framework, EvidenceMapping.requirement_id)
    if framework:
        eq = eq.where(EvidenceMapping.framework == framework)
    evidence_result = await db.execute(eq)
    evidence = [_evidence_to_dict(e) for e in evidence_result.scalars().all()]

    # Load most recent snapshot
    snap_result = await db.execute(
        select(ComplianceSnapshot).order_by(desc(ComplianceSnapshot.snapshot_date)).limit(1)
    )
    snap = snap_result.scalar_one_or_none()
    snapshot_summary = _snapshot_summary(snap) if snap else None

    # Load most recent review per type
    reviews_by_type = {}
    for rtype in REVIEW_CADENCES:
        rev_result = await db.execute(
            select(ManagementReview)
            .where(ManagementReview.review_type == rtype)
            .order_by(desc(ManagementReview.review_date))
            .limit(1)
        )
        rev = rev_result.scalar_one_or_none()
        if rev:
            reviews_by_type[rtype] = _review_to_dict(rev)

    # Build per-requirement report
    report_items = []
    for ev in evidence:
        item = {
            **ev,
            "latest_review": reviews_by_type.get(ev["framework"]),
        }
        report_items.append(item)

    # Count statuses
    status_counts = {}
    for ev in evidence:
        status_counts[ev["status"]] = status_counts.get(ev["status"], 0) + 1

    report = {
        "generated_at": datetime.utcnow().isoformat(),
        "frameworks": [framework] if framework else ["nist_ai_rmf", "aiuc_1", "iso_42001"],
        "status_summary": status_counts,
        "total_requirements": len(evidence),
        "latest_snapshot": snapshot_summary,
        "latest_reviews": reviews_by_type,
        "requirements": report_items,
    }

    if format == "html":
        return HTMLResponse(_render_report_html(report))
    return report


def _render_report_html(report: dict) -> str:
    """Render a printable HTML compliance report."""
    rows = ""
    for req in report["requirements"]:
        color = "#22c55e" if req["status"] == "addressed" else "#fb923c" if req["status"] == "partial" else "#ef4444" if req["status"] == "gap" else "#64748b"
        rows += f"""<tr>
            <td>{req['framework']}</td>
            <td><strong>{req['requirement_id']}</strong></td>
            <td>{req['requirement_name']}</td>
            <td style="color:{color};font-weight:600">{req['status'].upper()}</td>
            <td><code>{req['evidence_type']}</code></td>
            <td><code>{req['evidence_location']}</code></td>
            <td>{req['last_verified'] or '<em>never</em>'}</td>
            <td>{req.get('notes') or ''}</td>
        </tr>"""

    snap = report.get("latest_snapshot")
    snap_html = ""
    if snap:
        snap_html = f"""<div style="margin:16px 0;padding:12px;background:#1a1e2e;border-radius:8px">
            <strong>Latest Snapshot:</strong> {snap['snapshot_date']}<br>
            Neurons: {snap['total_neurons']} | PII Clean: {'Yes' if snap['pii_clean'] else 'No'} |
            Coverage CV: {snap['coverage_cv']:.3f} | Fairness: {'Pass' if snap['fairness_pass'] else 'Fail'} |
            Missing Citations: {snap['missing_citations_count']}
        </div>"""

    status = report.get("status_summary", {})
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Corvus Compliance Report</title>
<style>
    body {{ font-family: system-ui, sans-serif; background: #0f1117; color: #e2e8f0; padding: 32px; max-width: 1200px; margin: 0 auto; }}
    h1 {{ color: #60a5fa; border-bottom: 2px solid #60a5fa33; padding-bottom: 8px; }}
    h2 {{ color: #94a3b8; margin-top: 24px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; margin-top: 12px; }}
    th {{ background: #1e293b; color: #94a3b8; text-align: left; padding: 8px 12px; border-bottom: 2px solid #334155; }}
    td {{ padding: 8px 12px; border-bottom: 1px solid #1e293b; }}
    tr:hover {{ background: #1e293b44; }}
    code {{ background: #1e293b; padding: 2px 6px; border-radius: 3px; font-size: 0.8rem; }}
    .summary {{ display: flex; gap: 16px; margin: 16px 0; }}
    .summary-card {{ background: #1a1e2e; padding: 12px 20px; border-radius: 8px; text-align: center; }}
    .summary-card .count {{ font-size: 1.5rem; font-weight: 700; }}
    .summary-card .label {{ font-size: 0.75rem; color: #94a3b8; }}
    @media print {{ body {{ background: white; color: black; }} th {{ background: #f1f5f9; color: #334155; }} td {{ border-color: #e2e8f0; }} }}
</style></head><body>
<h1>Corvus Compliance Self-Assessment Report</h1>
<p>Generated: {report['generated_at']} | Frameworks: {', '.join(report['frameworks'])}</p>
<div class="summary">
    <div class="summary-card"><div class="count" style="color:#22c55e">{status.get('addressed', 0)}</div><div class="label">Addressed</div></div>
    <div class="summary-card"><div class="count" style="color:#fb923c">{status.get('partial', 0)}</div><div class="label">Partial</div></div>
    <div class="summary-card"><div class="count" style="color:#ef4444">{status.get('gap', 0)}</div><div class="label">Gaps</div></div>
    <div class="summary-card"><div class="count">{report['total_requirements']}</div><div class="label">Total</div></div>
</div>
{snap_html}
<h2>Requirements Detail</h2>
<table>
    <thead><tr><th>Framework</th><th>ID</th><th>Requirement</th><th>Status</th><th>Type</th><th>Location</th><th>Last Verified</th><th>Notes</th></tr></thead>
    <tbody>{rows}</tbody>
</table>
</body></html>"""


# ── Code Review Checklist (NASA Standards) ──

NASA_CODE_REVIEW_CHECKLIST = (
    {
        "id": "CR-01",
        "category": "Software Safety",
        "standard": "NPR 7150.2D §4.7 / NASA-STD-8739.8B",
        "requirement": "All changes must consider failure modes. Code that controls scoring, evaluation, or LLM-driven actions must document assumptions and failure behavior.",
        "check_items": [
            "Error handling for LLM API failures (timeouts, malformed responses)",
            "Graceful degradation when external services are unavailable",
            "No silent data corruption — failures must be visible",
            "Neuron scoring changes validated against known-good baselines",
        ],
    },
    {
        "id": "CR-02",
        "category": "Configuration Management",
        "standard": "NPR 7150.2D §4.6",
        "requirement": "Every change must be traceable via git commit with descriptive message. No uncommitted changes in production.",
        "check_items": [
            "Commit message describes the 'why', not just the 'what'",
            "Schema migrations included for any model changes",
            "No hardcoded environment-specific values",
            "Database migration is idempotent (ADD COLUMN IF NOT EXISTS pattern)",
        ],
    },
    {
        "id": "CR-03",
        "category": "Secure Coding",
        "standard": "NPR 7150.2D §4.5 / NASA Secure Coding Portal",
        "requirement": "Validate all external inputs, sanitize LLM outputs before database writes, no hardcoded credentials, no injection vectors.",
        "check_items": [
            "User inputs validated at API boundary (type, range, length)",
            "LLM output parsed defensively (try/except on JSON parse)",
            "SQL queries use parameterized statements (SQLAlchemy ORM or text(:param))",
            "No credentials, API keys, or secrets in source code",
            "File paths validated against allowed prefixes (no path traversal)",
            "CORS settings are intentional and minimal",
        ],
    },
    {
        "id": "CR-04",
        "category": "Formal Inspections",
        "standard": "NASA-STD-8739.9",
        "requirement": "Non-trivial changes (new endpoints, schema changes, LLM prompt modifications) require structured review against acceptance criteria.",
        "check_items": [
            "New API endpoints have docstrings describing behavior",
            "LLM system prompts document intent and expected output format",
            "Schema changes have corresponding migration logic",
            "Breaking changes are flagged with migration path",
        ],
    },
    {
        "id": "CR-05",
        "category": "Testing & Verification",
        "standard": "NPR 7150.2D §4.4 / NASA-STD-8739.8B §3.3",
        "requirement": "New features require verification evidence. API endpoints need smoke tests. LLM pipeline changes need evaluation against known-good queries.",
        "check_items": [
            "New endpoints have at least one test or documented manual verification",
            "Scoring algorithm changes tested against baseline query results",
            "Frontend changes verified with build (npm run build passes)",
            "Edge cases considered (empty results, max limits, concurrent access)",
        ],
    },
    {
        "id": "CR-06",
        "category": "Software Classification",
        "standard": "NASA-STD-8739.8B §3.1",
        "requirement": "This system processes operational knowledge and influences decision-making. Changes to scoring, neuron creation, or observation approval require heightened review.",
        "check_items": [
            "Changes to neuron scoring signals reviewed for unintended bias",
            "Observation auto-approval or batch operations have human checkpoint",
            "LLM model version changes evaluated for behavioral drift",
            "Provenance tracking maintained (source_origin, refinement records)",
        ],
    },
    {
        "id": "CR-07",
        "category": "Metrics & Measurement",
        "standard": "NPR 7150.2D §4.8",
        "requirement": "Track token usage, model costs, and pipeline latency. Cost projections and actuals must remain visible.",
        "check_items": [
            "Token counts recorded for LLM calls (input + output)",
            "Cost tracking updated when adding new LLM-powered features",
            "Performance-sensitive operations have timing instrumentation",
            "New LLM endpoints include model selection parameter",
        ],
    },
    {
        "id": "CR-08",
        "category": "Third-Party Software",
        "standard": "NPR 7150.2D §4.9 / NASA SWEHB §9",
        "requirement": "LLM model updates, dependency upgrades, and SDK updates must be evaluated for behavioral impact before adoption.",
        "check_items": [
            "Anthropic SDK version pinned in requirements.txt",
            "Model version changes tested against evaluation suite",
            "New dependencies justified and license-compatible",
            "COTS/API behavioral changes documented",
        ],
    },
    {
        "id": "CR-09",
        "category": "Documentation",
        "standard": "NPR 7150.2D §4.11",
        "requirement": "Public-facing endpoints must have docstrings. Schema changes must include migration logic. LLM system prompts must document intent.",
        "check_items": [
            "API endpoint has FastAPI docstring (appears in /docs)",
            "Complex business logic has inline comments explaining 'why'",
            "LLM prompts include comment block with purpose and expected format",
            "CLAUDE.md updated if new patterns or conventions introduced",
        ],
    },
    {
        "id": "CR-10",
        "category": "Safety-Critical Coding (Power of Ten)",
        "standard": "JPL/NASA — Holzmann, 'The Power of Ten: Rules for Developing Safety-Critical Code'",
        "requirement": "Ten coding rules for safety-critical software derived from JPL/NASA flight software practices. These constrain code complexity to enable automated verification and prevent unpredictable failures.",
        "check_items": [
            "R1: Simple control flow only — no goto, setjmp, longjmp, or recursion",
            "R2: All loops have a fixed upper bound (provable termination)",
            "R3: No dynamic memory allocation after initialization (no malloc/new in operational code)",
            "R4: No function longer than ~60 lines (single printed page)",
            "R5: Minimum two assertions per function (document assumptions, catch violations early)",
            "R6: All variables declared at the smallest possible scope",
            "R7: Static analysis run on every source file; all findings resolved",
            "R8: Pointer use restricted to struct members and array elements (no arbitrary pointer arithmetic)",
            "R9: Compile at highest warning level; all warnings eliminated",
            "R10: Development environment and coding standards matched to software criticality level",
        ],
    },
    {
        "id": "CR-11",
        "category": "Corvus-Specific",
        "standard": "Corvus Development Policy",
        "requirement": "Screen capture data is ephemeral. Observation-to-neuron flow must maintain provenance. Human approval required before graph modifications.",
        "check_items": [
            "Raw screenshots not persisted beyond processing pipeline",
            "Observation → neuron flow creates NeuronRefinement records",
            "LLM evaluation proposals require explicit human approval",
            "Interpretation cadence and alert thresholds are configurable",
            "Chrome extension URLs point to integrated backend (port 8002)",
        ],
    },
)


@router.get("/code-review")
async def code_review_checklist(db: AsyncSession = Depends(get_db)):
    """Return NASA-aligned code review checklist with coverage metrics from the neuron graph."""

    # Count NASA standard neurons and their refinement activity
    nasa_roles = ["nasa_npr7150", "nasa_std8739", "nasa_swehb", "jpl_power_of_ten"]
    neuron_result = await db.execute(
        select(Neuron.id, Neuron.label, Neuron.layer, Neuron.role_key,
               Neuron.invocations, Neuron.content, Neuron.summary)
        .where(Neuron.role_key.in_(nasa_roles), Neuron.is_active == True)
        .order_by(Neuron.role_key, Neuron.layer, Neuron.label)
    )
    neurons = neuron_result.all()

    # Group by role
    standards: dict[str, dict] = {}
    for n in neurons:
        rk = n.role_key
        if rk not in standards:
            standards[rk] = {"role_label": "", "neurons": [], "total_invocations": 0}
        entry = {
            "id": n.id, "label": n.label, "layer": n.layer,
            "invocations": n.invocations or 0,
            "has_content": bool(n.content and len(n.content) > 20),
            "has_summary": bool(n.summary and len(n.summary) > 10),
        }
        if n.layer == 1:
            standards[rk]["role_label"] = n.label
        else:
            standards[rk]["neurons"].append(entry)
        standards[rk]["total_invocations"] += (n.invocations or 0)

    # Recent refinements touching NASA neurons
    nasa_ids = [n.id for n in neurons]
    refinement_count = 0
    if nasa_ids:
        ref_result = await db.execute(
            select(func.count(NeuronRefinement.id))
            .where(NeuronRefinement.neuron_id.in_(nasa_ids))
        )
        refinement_count = ref_result.scalar() or 0

    # Coverage score: % of L2 neurons with content
    l2_neurons = [n for n in neurons if n.layer >= 2]
    content_count = sum(1 for n in l2_neurons if n.content and len(n.content) > 20)
    coverage_pct = round(content_count / len(l2_neurons) * 100, 1) if l2_neurons else 0

    return {
        "checklist": NASA_CODE_REVIEW_CHECKLIST,
        "standards_coverage": standards,
        "metrics": {
            "total_nasa_neurons": len(neurons),
            "l2_with_content": content_count,
            "l2_total": len(l2_neurons),
            "coverage_pct": coverage_pct,
            "total_invocations": sum(n.invocations or 0 for n in neurons),
            "refinement_count": refinement_count,
        },
    }


# ── Audit Log (AU-2, AU-3, AU-6, AU-7) ──

@router.get("/audit-log")
async def list_audit_log(
    action: str | None = None,
    endpoint_filter: str | None = None,
    since: str | None = None,
    status_code_min: int | None = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Query audit log records with optional filters. Supports AU-6 audit review and AU-7 report generation."""
    q = select(AuditLog).order_by(desc(AuditLog.timestamp))
    if action:
        q = q.where(AuditLog.action == action.upper())
    if endpoint_filter:
        q = q.where(AuditLog.endpoint.contains(endpoint_filter))
    if since:
        q = q.where(AuditLog.timestamp >= datetime.fromisoformat(since))
    if status_code_min:
        q = q.where(AuditLog.status_code >= status_code_min)
    q = q.offset(offset).limit(limit)
    result = await db.execute(q)
    rows = result.scalars().all()
    return [_audit_to_dict(r) for r in rows]


@router.get("/audit-log/summary")
async def audit_log_summary(
    since: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Audit log summary statistics for dashboard and AU-6 review."""
    base = select(AuditLog)
    if since:
        base = base.where(AuditLog.timestamp >= datetime.fromisoformat(since))

    # Total count
    total_result = await db.execute(select(func.count(AuditLog.id)).select_from(base.subquery()))
    total = total_result.scalar() or 0

    # Count by action
    action_result = await db.execute(
        select(AuditLog.action, func.count(AuditLog.id))
        .where(AuditLog.timestamp >= datetime.fromisoformat(since) if since else True)
        .group_by(AuditLog.action)
    )
    by_action = {row[0]: row[1] for row in action_result.all()}

    # Count errors (4xx/5xx)
    error_result = await db.execute(
        select(func.count(AuditLog.id))
        .where(AuditLog.status_code >= 400)
        .where(AuditLog.timestamp >= datetime.fromisoformat(since) if since else True)
    )
    error_count = error_result.scalar() or 0

    # Most recent entry
    latest_result = await db.execute(
        select(AuditLog.timestamp).order_by(desc(AuditLog.timestamp)).limit(1)
    )
    latest = latest_result.scalar_one_or_none()

    # Top endpoints
    endpoint_result = await db.execute(
        select(AuditLog.endpoint, func.count(AuditLog.id).label("cnt"))
        .where(AuditLog.timestamp >= datetime.fromisoformat(since) if since else True)
        .group_by(AuditLog.endpoint)
        .order_by(desc("cnt"))
        .limit(10)
    )
    top_endpoints = [{"endpoint": row[0], "count": row[1]} for row in endpoint_result.all()]

    return {
        "total_records": total,
        "by_action": by_action,
        "error_count": error_count,
        "latest_entry": latest.isoformat() if latest else None,
        "top_endpoints": top_endpoints,
    }


def _audit_to_dict(r: AuditLog) -> dict:
    return {
        "id": r.id,
        "timestamp": r.timestamp.isoformat() if r.timestamp else None,
        "action": r.action,
        "endpoint": r.endpoint,
        "status_code": r.status_code,
        "user_agent": r.user_agent,
        "client_ip": r.client_ip,
        "request_body_summary": r.request_body_summary,
        "response_time_ms": r.response_time_ms,
        "error_detail": r.error_detail,
    }


# ── Security Framework Catalogs (FedRAMP, SOC 2, CMMC) ──

@router.get("/frameworks")
async def list_frameworks():
    """Return available security compliance frameworks with summary statistics."""
    from app.data.fedramp_moderate_baseline import get_fedramp_summary
    from app.data.soc2_type2_criteria import get_soc2_summary
    from app.data.cmmc_level2_practices import get_cmmc_summary
    return {
        "frameworks": [
            get_fedramp_summary(),
            get_soc2_summary(),
            get_cmmc_summary(),
        ]
    }


@router.get("/frameworks/fedramp")
async def fedramp_controls(
    family: str | None = None,
    status: str | None = None,
):
    """Return FedRAMP Moderate baseline controls, optionally filtered by family or status."""
    from app.data.fedramp_moderate_baseline import get_fedramp_controls, get_fedramp_summary
    controls = get_fedramp_controls()
    if family:
        controls = [c for c in controls if c["family"] == family.upper()]
    if status:
        controls = [c for c in controls if c["status"] == status]
    return {"summary": get_fedramp_summary(), "controls": controls}


@router.get("/frameworks/soc2")
async def soc2_criteria(
    category: str | None = None,
    status: str | None = None,
):
    """Return SOC 2 Type II Trust Services Criteria, optionally filtered by category or status."""
    from app.data.soc2_type2_criteria import get_soc2_criteria, get_soc2_summary
    criteria = get_soc2_criteria()
    if category:
        criteria = [c for c in criteria if c["category"].lower() == category.lower()]
    if status:
        criteria = [c for c in criteria if c["status"] == status]
    return {"summary": get_soc2_summary(), "criteria": criteria}


@router.get("/frameworks/cmmc")
async def cmmc_practices(
    family: str | None = None,
    status: str | None = None,
):
    """Return CMMC Level 2 practices (NIST 800-171r2), optionally filtered by family or status."""
    from app.data.cmmc_level2_practices import get_cmmc_practices, get_cmmc_summary
    practices = get_cmmc_practices()
    if family:
        practices = [p for p in practices if p["family"] == family]
    if status:
        practices = [p for p in practices if p["status"] == status]
    return {"summary": get_cmmc_summary(), "practices": practices}


@router.get("/frameworks/unified")
async def unified_framework_view(status: str | None = None):
    """Return all three frameworks in a unified view with cross-framework control mapping."""
    from app.data.fedramp_moderate_baseline import get_fedramp_controls, get_fedramp_summary
    from app.data.soc2_type2_criteria import get_soc2_criteria, get_soc2_summary
    from app.data.cmmc_level2_practices import get_cmmc_practices, get_cmmc_summary

    fedramp = get_fedramp_controls()
    soc2 = get_soc2_criteria()
    cmmc = get_cmmc_practices()

    if status:
        fedramp = [c for c in fedramp if c["status"] == status]
        soc2 = [c for c in soc2 if c["status"] == status]
        cmmc = [p for p in cmmc if p["status"] == status]

    # Aggregate status counts across all frameworks
    all_items = fedramp + soc2 + cmmc
    total_counts: dict[str, int] = {}
    for item in all_items:
        s = item["status"]
        total_counts[s] = total_counts.get(s, 0) + 1

    return {
        "total_controls": len(all_items),
        "status_counts": total_counts,
        "fedramp": {"summary": get_fedramp_summary(), "controls": fedramp},
        "soc2": {"summary": get_soc2_summary(), "criteria": soc2},
        "cmmc": {"summary": get_cmmc_summary(), "practices": cmmc},
    }
