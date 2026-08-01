"""Operator surfaces: system use notification and the audit-trail read side."""

from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import AuditLog
from app.tenant import tenant

router = APIRouter(prefix="/admin", tags=["operations"])


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


# ── Audit Log (AU-2, AU-3, AU-6, AU-7) ──
# The write side is app/middleware/audit.py::AuditMiddleware, which the factory
# installs on every profile. These reads are its counterpart and follow it.

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
    # Total count.
    # This counted `func.count(AuditLog.id)` with `select_from(select(AuditLog)
    # ).subquery()`, which leaves AuditLog in the FROM clause alongside the
    # subquery — a cartesian product, so the reported total was the SQUARE of
    # the true count (22,816 rows reported as 520,569,856 on 2026-08-01).
    # SQLAlchemy emitted a SAWarning about it that nothing was reading.
    count_q = select(func.count(AuditLog.id))
    if since:
        count_q = count_q.where(AuditLog.timestamp >= datetime.fromisoformat(since))
    total_result = await db.execute(count_q)
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
