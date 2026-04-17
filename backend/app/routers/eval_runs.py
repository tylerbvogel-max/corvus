"""Admin endpoints for EvalRun (Pattern #3 — immutable eval artifacts).

Three surfaces:

- ``POST /admin/eval/runs`` — execute a tenant-local suite end-to-end.
- ``GET  /admin/eval/runs``  — index of recent runs for the admin UI.
- ``GET  /admin/eval/runs/{id}`` — one run plus all its cases.
- ``POST /admin/eval/runs/{id}/certify`` — promote a completed run to be
  the ``/v1/query`` ``eval_run_id`` (single tenant-level pointer).

All endpoints require the ``admin`` role. Runs are append-only beyond
the terminal state — there is no update/delete path, by design.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.middleware.rbac import UserIdentity, require_role
from app.models import EvalRun, EvalRunCase, TenantConfig
from app.services import eval_runs

router = APIRouter(prefix="/admin/eval", tags=["admin-eval"])


# ── Response shapes ──

class EvalRunSummary(BaseModel):
    id: int
    suite_name: str
    suite_hash: str
    status: str
    started_at: datetime
    completed_at: datetime | None
    started_by: str
    tenant_id: str
    summary: dict | None
    is_certified: bool = False


class EvalRunCaseOut(BaseModel):
    id: int
    case_label: str
    query_text: str
    query_id: int | None
    lineage_id: int | None
    blocked: bool
    response_text: str | None
    violations: list[dict] = Field(default_factory=list)
    scores: dict | None
    error_message: str | None


class EvalRunDetail(EvalRunSummary):
    model_versions: dict
    scoring_engine_version: str
    overrides_snapshot: dict | None
    cases: list[EvalRunCaseOut]


class StartRunRequest(BaseModel):
    suite_name: str


class CertifyResponse(BaseModel):
    eval_run_id: int
    certified_at: datetime
    certified_by: str


# ── Helpers ──

async def _certified_id(db: AsyncSession) -> int | None:
    config = await db.get(TenantConfig, 1)
    return config.certified_eval_run_id if config else None


def _row_to_summary(row: EvalRun, certified_id: int | None) -> EvalRunSummary:
    return EvalRunSummary(
        id=row.id,
        suite_name=row.suite_name,
        suite_hash=row.suite_hash,
        status=row.status,
        started_at=row.started_at,
        completed_at=row.completed_at,
        started_by=row.started_by,
        tenant_id=row.tenant_id,
        summary=row.summary_json,
        is_certified=(certified_id == row.id),
    )


def _case_to_out(row: EvalRunCase) -> EvalRunCaseOut:
    violations_blob = row.violations_json or {}
    return EvalRunCaseOut(
        id=row.id,
        case_label=row.case_label,
        query_text=row.query_text,
        query_id=row.query_id,
        lineage_id=row.lineage_id,
        blocked=row.blocked,
        response_text=row.response_text,
        violations=list(violations_blob.get("violations") or []),
        scores=row.scores_json,
        error_message=row.error_message,
    )


# ── Routes ──

@router.post("/runs", response_model=EvalRunSummary, status_code=201)
async def start_eval_run(
    req: StartRunRequest,
    db: AsyncSession = Depends(get_db),
    identity: UserIdentity = Depends(require_role("admin")),
):
    """Execute a suite against the live pipeline and persist the artifact."""
    try:
        run = await eval_runs.start_run(db, req.suite_name, identity)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await db.commit()
    certified_id = await _certified_id(db)
    return _row_to_summary(run, certified_id)


@router.get("/runs", response_model=list[EvalRunSummary])
async def list_eval_runs(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    _identity: UserIdentity = Depends(require_role("admin")),
):
    """Recent runs, newest first."""
    assert 0 < limit <= 200, "limit out of range"
    runs = await eval_runs.list_runs(db, limit=limit)
    certified_id = await _certified_id(db)
    return [_row_to_summary(r, certified_id) for r in runs]


@router.get("/runs/{run_id}", response_model=EvalRunDetail)
async def get_eval_run(
    run_id: int,
    db: AsyncSession = Depends(get_db),
    _identity: UserIdentity = Depends(require_role("admin")),
):
    """Return one frozen artifact + all cases."""
    run = await eval_runs.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"eval_run {run_id} not found")
    cases = await eval_runs.list_cases(db, run_id)
    certified_id = await _certified_id(db)
    base = _row_to_summary(run, certified_id)
    return EvalRunDetail(
        **base.model_dump(),
        model_versions=run.model_versions,
        scoring_engine_version=run.scoring_engine_version,
        overrides_snapshot=run.overrides_snapshot,
        cases=[_case_to_out(c) for c in cases],
    )


@router.post("/runs/{run_id}/certify", response_model=CertifyResponse)
async def certify_eval_run(
    run_id: int,
    db: AsyncSession = Depends(get_db),
    identity: UserIdentity = Depends(require_role("admin")),
):
    """Promote a completed run to the tenant-level ``eval_run_id`` pointer.

    Only completed runs may be certified. Certification is a single
    pointer flip, idempotent — promoting the same run twice is a no-op
    beyond updating the ``certified_at`` timestamp.
    """
    run = await eval_runs.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"eval_run {run_id} not found")
    if run.status != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"cannot certify eval_run in state {run.status!r}",
        )
    config = await db.get(TenantConfig, 1)
    if config is None:
        config = TenantConfig(id=1)
        db.add(config)
    config.certified_eval_run_id = run.id
    config.certified_at = datetime.utcnow()
    config.certified_by = identity.user_id
    await db.commit()
    assert config.certified_at is not None
    return CertifyResponse(
        eval_run_id=run.id,
        certified_at=config.certified_at,
        certified_by=identity.user_id,
    )
