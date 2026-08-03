"""Project roadmap ledger API for the Corvus-Mind working surface."""

from __future__ import annotations

import logging
import os
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import RoadmapLedger
from app.services.commit_verification import resolve_commits
from app.services.roadmap_admission import (
    admit_session, recent_admissions, refresh_cache,
)
from app.services.roadmap_ledger import (
    SLUG_RE, advance_state, empty_state, extract_commit_candidates,
    reconcile_node, retirement_conflict, slugify, state_summary, validate_state,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/roadmap-ledgers",
    tags=["roadmap-ledgers"],
)


class LedgerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,119}$")
    description: str | None = Field(default=None, max_length=4000)
    project_path: str | None = Field(default=None, max_length=500)
    state: dict | None = None


class LedgerUpdate(BaseModel):
    expected_revision: int = Field(ge=1)
    state: dict


class LedgerMetadataUpdate(BaseModel):
    expected_revision: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    project_path: str | None = Field(default=None, max_length=500)


class ReconciliationCreate(BaseModel):
    expected_revision: int = Field(ge=1)
    disposition: str = Field(pattern=r"^(complete|partial|failed|blocked)$")
    result_recap: str = Field(min_length=1, max_length=12000)
    verification_passed: bool
    confidence: float = Field(ge=0, le=1)
    claims: list[str] = Field(default_factory=list, max_length=100)
    limitations: list[str] = Field(default_factory=list, max_length=100)
    disclosures: list[str] = Field(default_factory=list, max_length=100)
    evidence: list[str] = Field(default_factory=list, max_length=200)
    verifier: str = Field(min_length=1, max_length=200)
    accepted_by: str = Field(min_length=1, max_length=200)
    next_action: str | None = Field(default=None, max_length=4000)


class AdmissionCreate(BaseModel):
    session_id: str = Field(pattern=r"^[A-Za-z0-9_-]+$", max_length=200)
    mode: str = Field(default="bound", pattern=r"^(bound|off-ledger)$")
    record_id: str | None = Field(default=None, max_length=160)
    reason: str | None = Field(default=None, max_length=2000)
    cwd: str | None = Field(default=None, max_length=1000)
    harness: str | None = Field(default=None, max_length=80)


async def _refresh_cache_safely(db: AsyncSession) -> None:
    """A cache failure must never roll back canonical roadmap state."""
    try:
        await refresh_cache(db)
    except (OSError, ValueError) as exc:
        logger.warning("Roadmap admission cache refresh skipped: %s", exc)


def _detail(row: RoadmapLedger) -> dict:
    return {
        "id": row.id,
        "slug": row.slug,
        "name": row.name,
        "description": row.description,
        "project_path": row.project_path,
        "revision": row.revision,
        "state": row.state,
        "summary": state_summary(row.state),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


@router.get("")
async def list_ledgers(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(RoadmapLedger).order_by(RoadmapLedger.updated_at.desc(), RoadmapLedger.id)
    )).scalars().all()
    return [{
        "id": row.id,
        "slug": row.slug,
        "name": row.name,
        "description": row.description,
        "project_path": row.project_path,
        "revision": row.revision,
        "summary": state_summary(row.state),
        "updated_at": row.updated_at,
    } for row in rows]


@router.post("", status_code=201)
async def create_ledger(req: LedgerCreate, db: AsyncSession = Depends(get_db)):
    try:
        slug = req.slug or slugify(req.name)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not SLUG_RE.fullmatch(slug):
        raise HTTPException(422, "invalid ledger slug")
    if await db.scalar(select(RoadmapLedger.id).where(RoadmapLedger.slug == slug)):
        raise HTTPException(409, "ledger slug already exists")
    try:
        state = validate_state(req.state) if req.state is not None else empty_state()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    row = RoadmapLedger(
        slug=slug,
        name=req.name.strip(),
        description=req.description,
        project_path=req.project_path,
        state=state,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await _refresh_cache_safely(db)
    return _detail(row)


@router.get("/admission-context")
async def admission_context(
    cwd: str | None = Query(default=None, max_length=1000),
    db: AsyncSession = Depends(get_db),
):
    """Refresh and return the tiny deterministic hook projection.

    Hooks normally read the local file directly.  This endpoint repairs a
    missing cache and gives MCP clients the same contract without duplicating
    roadmap ranking logic.
    """
    document = await refresh_cache(db)
    ledgers = document["ledgers"]
    target = None
    if cwd:
        normalized = os.path.realpath(os.path.expanduser(cwd))
        matches = [
            ledger for ledger in ledgers
            if ledger.get("project_path")
            and (
                normalized == ledger["project_path"]
                or normalized.startswith(ledger["project_path"].rstrip("/") + "/")
            )
        ]
        if matches:
            target = max(matches, key=lambda item: len(item["project_path"]))
    return {
        **document,
        "cwd": cwd,
        "mapped": target is not None,
        "ledger": target,
    }


@router.get("/{slug}")
async def get_ledger(slug: str, db: AsyncSession = Depends(get_db)):
    row = await db.scalar(select(RoadmapLedger).where(RoadmapLedger.slug == slug))
    if row is None:
        raise HTTPException(404, "roadmap ledger not found")
    return _detail(row)


@router.put("/{slug}")
async def update_ledger(slug: str, req: LedgerUpdate, db: AsyncSession = Depends(get_db)):
    row = await db.scalar(
        select(RoadmapLedger).where(RoadmapLedger.slug == slug).with_for_update()
    )
    if row is None:
        raise HTTPException(404, "roadmap ledger not found")
    if row.revision != req.expected_revision:
        raise HTTPException(
            409,
            f"ledger changed since it was opened (current revision {row.revision})",
        )
    try:
        row.state = advance_state(req.state, int(row.state.get("version", 1)))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    row.revision += 1
    await db.commit()
    await db.refresh(row)
    await _refresh_cache_safely(db)
    return _detail(row)


@router.patch("/{slug}")
async def update_ledger_metadata(
    slug: str, req: LedgerMetadataUpdate, db: AsyncSession = Depends(get_db),
):
    row = await db.scalar(
        select(RoadmapLedger).where(RoadmapLedger.slug == slug).with_for_update()
    )
    if row is None:
        raise HTTPException(404, "roadmap ledger not found")
    if row.revision != req.expected_revision:
        raise HTTPException(
            409,
            f"ledger changed since it was opened (current revision {row.revision})",
        )
    row.name = req.name.strip()
    row.description = req.description
    row.project_path = req.project_path
    row.revision += 1
    await db.commit()
    await db.refresh(row)
    await _refresh_cache_safely(db)
    return _detail(row)


@router.post("/{slug}/admissions", status_code=201)
async def create_admission(
    slug: str, req: AdmissionCreate, db: AsyncSession = Depends(get_db),
):
    ledger = await db.scalar(select(RoadmapLedger).where(RoadmapLedger.slug == slug))
    if ledger is None:
        raise HTTPException(404, "roadmap ledger not found")
    try:
        return admit_session(
            ledger=ledger,
            session_id=req.session_id,
            mode=req.mode,
            record_id=req.record_id,
            reason=req.reason,
            cwd=req.cwd,
            harness=req.harness,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{slug}/admissions")
async def list_admissions(
    slug: str,
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    if not await db.scalar(select(RoadmapLedger.id).where(RoadmapLedger.slug == slug)):
        raise HTTPException(404, "roadmap ledger not found")
    return recent_admissions(slug, limit=limit)


@router.post("/{slug}/nodes/{node_id}/reconcile")
async def reconcile_roadmap_record(
    slug: str, node_id: str, req: ReconciliationCreate,
    db: AsyncSession = Depends(get_db),
):
    ledger = await db.scalar(
        select(RoadmapLedger).where(RoadmapLedger.slug == slug).with_for_update()
    )
    if ledger is None:
        raise HTTPException(404, "roadmap ledger not found")
    if ledger.revision != req.expected_revision:
        raise HTTPException(409, f"ledger changed since it was opened (current revision {ledger.revision})")

    nodes = list(ledger.state["nodes"])
    index = next((i for i, item in enumerate(nodes) if item["id"] == node_id), None)
    if index is None:
        raise HTTPException(404, "roadmap record not found")
    # A retired record stays frozen in substance but can still be signed for —
    # see retirement_conflict, which owns that rule and explains why.
    conflict = retirement_conflict(
        nodes[index],
        disposition=req.disposition,
        verification_passed=req.verification_passed,
    )
    if conflict:
        raise HTTPException(409, conflict)

    # DONE MEANS COMMITTED (ledger-done-means-committed). Verified completion
    # has to name a commit that exists in this ledger's own repository —
    # otherwise `done` can describe work living only in someone's working
    # tree, which is exactly how mind-synaptic-downscaling went unnoticed.
    candidates = extract_commit_candidates(req.evidence)
    resolved, unresolved, commit_status = await resolve_commits(
        ledger.project_path, candidates)
    if req.verification_passed and req.disposition == "complete":
        if commit_status == "verified" and not resolved:
            raise HTTPException(422, (
                "verified completion must name a commit that exists in "
                f"{ledger.project_path}: none of the claimed tokens resolved "
                f"({', '.join(unresolved) or 'no commit-sha-shaped token found'}). "
                "Commit the work first, then reconcile with its sha."
            ))
    try:
        nodes[index] = reconcile_node(
            nodes[index],
            ledger_revision=ledger.revision,
            disposition=req.disposition,
            result_recap=req.result_recap,
            verification_passed=req.verification_passed,
            confidence=req.confidence,
            claims=req.claims,
            limitations=req.limitations,
            disclosures=req.disclosures,
            evidence=req.evidence,
            verifier=req.verifier,
            accepted_by=req.accepted_by,
            next_action=req.next_action,
            # When no repository was readable the candidates stand unchecked;
            # commit_verification says so, so the receipt never implies a
            # resolution that did not happen.
            evidence_commits=resolved if commit_status == "verified" else candidates,
            commit_verification=commit_status,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    ledger.state = advance_state(
        {**ledger.state, "nodes": nodes}, int(ledger.state.get("version", 1))
    )
    ledger.revision += 1
    await db.commit()
    await db.refresh(ledger)
    await _refresh_cache_safely(db)
    return _detail(ledger)
