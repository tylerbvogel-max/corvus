"""Project roadmap ledger API for the Corvus-Mind working surface."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    AgencyPlanRevision, AgencyPolicy, AgencyVenturePlan, AgencyWorkerProfile,
    AgencyWorkOrder, RoadmapLedger,
)
from app.routers.recall import require_memory_surface
from app.services.agency_work_orders import (
    add_event, canonical_digest, issue_work_order, validate_plan_graph,
)
from app.services.roadmap_admission import (
    admit_session, recent_admissions, refresh_cache,
)
from app.services.roadmap_ledger import (
    SLUG_RE, advance_state, compile_review_node, compile_work_order_node,
    empty_state, next_review_at, node_is_ready, slugify, state_summary,
    validate_state,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/roadmap-ledgers",
    tags=["roadmap-ledgers"],
    dependencies=[Depends(require_memory_surface)],
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


class CommissionCreate(BaseModel):
    expected_revision: int = Field(ge=1)
    worker_profile_id: int
    policy_id: int
    task_class: str = Field(default="coding", min_length=1, max_length=80)
    risk_tier: int = Field(default=2, ge=1, le=5)
    permissions: dict = Field(default_factory=lambda: {
        "filesystem": "project",
        "commands": ["read", "edit", "test"],
        "network": False,
    })
    ttl_minutes: int = Field(default=240, ge=1, le=43200)


class AcceptDelivery(BaseModel):
    expected_revision: int = Field(ge=1)


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


def _work_order(
    row: AgencyWorkOrder,
    worker: AgencyWorkerProfile | None = None,
    policy: AgencyPolicy | None = None,
) -> dict:
    return {
        "id": row.id,
        "plan_node_id": row.plan_node_id,
        "status": row.status,
        "worker_profile_id": row.worker_profile_id,
        "worker": {
            "id": worker.id,
            "key": worker.key,
            "display_name": worker.display_name,
            "model": worker.model,
            "harness": worker.harness,
        } if worker is not None else None,
        "policy": {
            "id": policy.id,
            "name": policy.name,
            "version": policy.version,
            "mode": policy.mode,
        } if policy is not None else None,
        "task_class": row.task_class,
        "risk_tier": row.risk_tier,
        "contract_digest": row.contract_digest,
        "contract": row.contract,
        "completion": row.locked_completion,
        "audit": row.audit_state,
        "roadmap_revision": row.specification.get("roadmap_ledger_revision"),
        "kind": row.specification.get("roadmap_work_kind", "delivery"),
        "created_at": row.created_at,
        "completed_at": row.completed_at,
    }


def _venture_key(row: RoadmapLedger) -> str:
    return f"roadmap-{row.id}-{row.slug}"[:120]


async def _load_worker_policy(
    db: AsyncSession, req: CommissionCreate,
) -> tuple[AgencyWorkerProfile, AgencyPolicy]:
    worker = await db.get(AgencyWorkerProfile, req.worker_profile_id)
    policy = await db.get(AgencyPolicy, req.policy_id)
    if worker is None or policy is None:
        raise HTTPException(404, "worker or policy not found")
    if worker.status != "active":
        raise HTTPException(409, "worker is not active")
    if req.risk_tier not in worker.risk_tiers:
        raise HTTPException(409, "worker is not approved for this risk tier")
    return worker, policy


async def _issue_ledger_order(
    db: AsyncSession,
    *,
    ledger: RoadmapLedger,
    node_id: str,
    contract_node: dict,
    worker: AgencyWorkerProfile,
    policy: AgencyPolicy,
    req: CommissionCreate,
    change_reason: str,
) -> AgencyWorkOrder:
    graph = validate_plan_graph({
        "mission": ledger.description or ledger.name,
        "constraints": ledger.state.get("constraints", [
            "Preserve human-authored roadmap intent",
            "Disclose uncertainty and limitations before submission",
        ]),
        "kill_criteria": ledger.state.get("killCriteria", [
            "Unsupported completion claims",
            "Unreviewed mutation of the roadmap ledger",
        ]),
        "nodes": [contract_node],
        "edges": [],
    })
    key = _venture_key(ledger)
    venture = await db.scalar(
        select(AgencyVenturePlan).where(AgencyVenturePlan.key == key).with_for_update()
    )
    digest = canonical_digest(graph)
    if venture is None:
        venture = AgencyVenturePlan(
            key=key, title=ledger.name, status="active",
            current_revision=1, created_by="human:roadmap-ledger",
        )
        db.add(venture)
        await db.flush()
        revision_number = 1
    else:
        venture.title = ledger.name
        venture.current_revision += 1
        revision_number = venture.current_revision
    revision = AgencyPlanRevision(
        venture_plan_id=venture.id,
        revision=revision_number,
        graph=graph,
        digest=digest,
        change_reason=change_reason,
        created_by="human:roadmap-ledger",
    )
    db.add(revision)
    await db.flush()
    return await issue_work_order(
        db, venture=venture, revision=revision, node_id=node_id,
        worker=worker, policy=policy, permissions=req.permissions,
        ttl_minutes=req.ttl_minutes,
    )


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


@router.get("/{slug}/work-orders")
async def list_ledger_work_orders(slug: str, db: AsyncSession = Depends(get_db)):
    ledger = await db.scalar(select(RoadmapLedger).where(RoadmapLedger.slug == slug))
    if ledger is None:
        raise HTTPException(404, "roadmap ledger not found")
    venture = await db.scalar(
        select(AgencyVenturePlan).where(AgencyVenturePlan.key == _venture_key(ledger))
    )
    if venture is None:
        return []
    rows = (await db.execute(
        select(AgencyWorkOrder).where(
            AgencyWorkOrder.venture_plan_id == venture.id,
        ).order_by(AgencyWorkOrder.created_at.desc())
    )).scalars().all()
    worker_ids = {row.worker_profile_id for row in rows if row.worker_profile_id is not None}
    policy_ids = {row.policy_id for row in rows}
    workers = {
        row.id: row for row in (await db.execute(
            select(AgencyWorkerProfile).where(AgencyWorkerProfile.id.in_(worker_ids))
        )).scalars().all()
    } if worker_ids else {}
    policies = {
        row.id: row for row in (await db.execute(
            select(AgencyPolicy).where(AgencyPolicy.id.in_(policy_ids))
        )).scalars().all()
    } if policy_ids else {}
    return [
        _work_order(
            row,
            worker=workers.get(row.worker_profile_id),
            policy=policies.get(row.policy_id),
        )
        for row in rows
    ]


@router.post("/{slug}/nodes/{node_id}/commission", status_code=201)
async def commission_node(
    slug: str, node_id: str, req: CommissionCreate,
    db: AsyncSession = Depends(get_db),
):
    ledger = await db.scalar(
        select(RoadmapLedger).where(RoadmapLedger.slug == slug).with_for_update()
    )
    if ledger is None:
        raise HTTPException(404, "roadmap ledger not found")
    if ledger.revision != req.expected_revision:
        raise HTTPException(409, f"ledger changed since it was opened (current revision {ledger.revision})")
    node = next((item for item in ledger.state["nodes"] if item["id"] == node_id), None)
    if node is None:
        raise HTTPException(404, "roadmap record not found")
    if not node_is_ready(ledger.state, node):
        raise HTTPException(409, "only ready, in-scope records can be commissioned")

    worker, policy = await _load_worker_policy(db, req)
    try:
        contract_node = compile_work_order_node(
            ledger_slug=ledger.slug,
            ledger_revision=ledger.revision,
            source_version=ledger.state["version"],
            node=node,
            task_class=req.task_class,
            risk_tier=req.risk_tier,
        )
        work_order = await _issue_ledger_order(
            db, ledger=ledger, node_id=node_id, contract_node=contract_node,
            worker=worker, policy=policy, req=req,
            change_reason=(
                f"Commission roadmap record {node_id} from ledger revision "
                f"{ledger.revision}"
            ),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _work_order(work_order, worker=worker, policy=policy)


@router.post("/{slug}/nodes/{node_id}/review", status_code=201)
async def commission_strategic_review(
    slug: str, node_id: str, req: CommissionCreate,
    db: AsyncSession = Depends(get_db),
):
    ledger = await db.scalar(
        select(RoadmapLedger).where(RoadmapLedger.slug == slug).with_for_update()
    )
    if ledger is None:
        raise HTTPException(404, "roadmap ledger not found")
    if ledger.revision != req.expected_revision:
        raise HTTPException(
            409,
            f"ledger changed since it was opened (current revision {ledger.revision})",
        )
    node = next((item for item in ledger.state["nodes"] if item["id"] == node_id), None)
    if node is None:
        raise HTTPException(404, "roadmap record not found")
    if node.get("status") in {"done", "deprioritized", "cancelled"}:
        raise HTTPException(409, "retired records cannot enter strategic review")

    worker, policy = await _load_worker_policy(db, req)
    try:
        contract_node = compile_review_node(
            ledger_slug=ledger.slug,
            ledger_revision=ledger.revision,
            source_version=ledger.state["version"],
            node=node,
            risk_tier=req.risk_tier,
        )
        work_order = await _issue_ledger_order(
            db, ledger=ledger, node_id=node_id, contract_node=contract_node,
            worker=worker, policy=policy, req=req,
            change_reason=(
                f"Commission strategic review for {node_id} from ledger revision "
                f"{ledger.revision}"
            ),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _work_order(work_order, worker=worker, policy=policy)


@router.post("/{slug}/nodes/{node_id}/accept/{work_order_id}")
async def accept_verified_delivery(
    slug: str, node_id: str, work_order_id: str, req: AcceptDelivery,
    db: AsyncSession = Depends(get_db),
):
    ledger = await db.scalar(
        select(RoadmapLedger).where(RoadmapLedger.slug == slug).with_for_update()
    )
    if ledger is None:
        raise HTTPException(404, "roadmap ledger not found")
    if ledger.revision != req.expected_revision:
        raise HTTPException(409, f"ledger changed since it was opened (current revision {ledger.revision})")
    order = await db.get(AgencyWorkOrder, work_order_id)
    if order is None:
        raise HTTPException(404, "work order not found")
    spec = order.specification
    if spec.get("roadmap_ledger_slug") != slug or order.plan_node_id != node_id:
        raise HTTPException(409, "work order does not belong to this roadmap record")
    if order.status != "verified":
        raise HTTPException(
            409,
            "only an independently verified work order can return to the roadmap",
        )

    nodes = list(ledger.state["nodes"])
    index = next((i for i, item in enumerate(nodes) if item["id"] == node_id), None)
    if index is None:
        raise HTTPException(404, "roadmap record not found")
    node = dict(nodes[index])
    accepted_at = datetime.now(timezone.utc)
    work_kind = spec.get("roadmap_work_kind", "delivery")
    if work_kind == "strategic-review":
        history = list(node.get("reviewHistory", []))
        history.append({
            "acceptedAt": accepted_at.isoformat(),
            "workOrderId": order.id,
            "ledgerRevision": spec.get("roadmap_ledger_revision"),
            "completion": order.locked_completion,
            "audit": order.audit_state,
        })
        node.update({
            "lastReviewedAt": accepted_at.isoformat(),
            "nextReviewAt": next_review_at(node, from_time=accepted_at),
            "reviewHistory": history,
            "reviewResultRecap": order.locked_completion,
            "acceptedReviewWorkOrderId": order.id,
        })
        event_type = "roadmap_review_accepted"
    else:
        node.update({
            "status": "done",
            "completedAt": accepted_at.isoformat(),
            "resultRecap": order.locked_completion,
            "verificationResults": order.audit_state,
            "acceptedWorkOrderId": order.id,
        })
        event_type = "roadmap_closeout_accepted"
    nodes[index] = node
    ledger.state = advance_state(
        {**ledger.state, "nodes": nodes}, int(ledger.state.get("version", 1))
    )
    ledger.revision += 1
    await add_event(
        db, order.id, event_type, "human:roadmap-ledger",
        {
            "ledger_slug": slug,
            "ledger_revision": ledger.revision,
            "node_id": node_id,
            "roadmap_work_kind": work_kind,
        },
    )
    await db.commit()
    await db.refresh(ledger)
    await _refresh_cache_safely(db)
    return _detail(ledger)
