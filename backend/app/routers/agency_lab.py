"""Agency Lab API — policy economy, workforce, experiments, and outcomes."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (AgencyExperiment, AgencyPermissionLease, AgencyPlanRevision,
                        AgencyPolicy, AgencyScoreEvent, AgencyVenturePlan,
                        AgencyWorkerProfile, AgencyWorkOrder, AgencyWorkOrderEvent)
from app.routers.recall import require_memory_surface
from app.services.agency_lab import (POLICY_MODES, POLICY_STATUSES, ROLES, dashboard,
                                     record_score, score_event, settle_event, validate_policy)
from app.services.agency_work_orders import (PLAN_STATUSES, add_event, canonical_digest,
    choose_audit, invalidate_descendants, issue_work_order, lock_completion, validate_plan_graph)

router = APIRouter(prefix="/agency-lab", tags=["agency-lab"],
                   dependencies=[Depends(require_memory_surface)])


class PolicyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    mode: str = "simulation"
    config: dict = Field(default_factory=dict)


class PolicyTransition(BaseModel):
    status: str


class WorkerCreate(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,159}$")
    display_name: str = Field(min_length=1, max_length=200)
    role: str
    model: str = Field(min_length=1, max_length=120)
    harness: str = Field(min_length=1, max_length=60)
    task_classes: list[str] = Field(default_factory=list)
    risk_tiers: list[int] = Field(default_factory=lambda: [1, 2])
    skill_ids: list[str] = Field(default_factory=list)
    permission_ceiling: str = "project_reversible"
    starting_capital: float = Field(default=100, ge=0, le=1_000_000)


class ExperimentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    mode: str = "simulation"
    policy_ids: list[int] = Field(min_length=1)
    dimensions: dict
    primary_metric: str = Field(min_length=1, max_length=100)
    guardrails: dict = Field(default_factory=dict)
    sample_target: int = Field(default=30, ge=2, le=100_000)


class ScoreCreate(BaseModel):
    worker_profile_id: int
    policy_id: int
    experiment_id: int | None = None
    work_order_id: str | None = Field(default=None, max_length=120)
    event_type: str
    task_class: str = Field(default="general", max_length=80)
    risk_tier: int = Field(default=1, ge=1, le=5)
    forecast_probability: float | None = Field(default=None, ge=0, le=1)
    resolved_outcome: bool | None = None
    severity: float = Field(default=1, gt=0, le=10)
    pre_submission: bool = False
    reproducible: bool = False
    known_failure: bool = False
    evidence: dict = Field(default_factory=dict)


class SettlementCreate(BaseModel):
    passed: bool
    evidence: dict = Field(default_factory=dict)


class VentureCreate(BaseModel):
    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,119}$")
    title: str = Field(min_length=1, max_length=240)
    graph: dict
    change_reason: str = Field(min_length=1, max_length=2000)
    created_by: str = Field(default="user", min_length=1, max_length=100)


class VentureRevisionCreate(BaseModel):
    graph: dict
    change_reason: str = Field(min_length=1, max_length=2000)
    created_by: str = Field(default="user", min_length=1, max_length=100)


class VentureTransition(BaseModel):
    status: str


class WorkOrderCreate(BaseModel):
    venture_key: str
    node_id: str
    worker_profile_id: int
    policy_id: int
    permissions: dict = Field(default_factory=lambda: {"filesystem": "project", "commands": ["read", "test"]})
    ttl_minutes: int = Field(default=60, ge=1, le=43200)


class CompletionCreate(BaseModel):
    disposition: str
    claims: list[dict]
    confidence: float = Field(ge=0, le=1)
    limitations: list[str] = Field(default_factory=list)
    disclosures: list[dict] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    next_action: str


class AuditResultCreate(BaseModel):
    passed: bool
    verifier: str = Field(min_length=1, max_length=160)
    evidence: dict = Field(default_factory=dict)
    defects: list[dict] = Field(default_factory=list)


@router.get("/dashboard")
async def get_dashboard(db: AsyncSession = Depends(get_db)):
    return await dashboard(db)


@router.get("/ventures")
async def list_ventures(db: AsyncSession = Depends(get_db)):
    ventures = (await db.execute(select(AgencyVenturePlan).order_by(AgencyVenturePlan.id.desc()))).scalars().all()
    return [{"id": x.id, "key": x.key, "title": x.title, "status": x.status,
             "current_revision": x.current_revision, "created_at": x.created_at} for x in ventures]


@router.get("/ventures/{venture_key}/topology")
async def get_venture_topology(venture_key: str, db: AsyncSession = Depends(get_db)):
    venture = await db.scalar(select(AgencyVenturePlan).where(AgencyVenturePlan.key == venture_key))
    if venture is None:
        raise HTTPException(404, "venture not found")
    revision = await db.scalar(select(AgencyPlanRevision).where(
        AgencyPlanRevision.venture_plan_id == venture.id,
        AgencyPlanRevision.revision == venture.current_revision))
    orders = (await db.execute(select(AgencyWorkOrder).where(
        AgencyWorkOrder.venture_plan_id == venture.id,
        AgencyWorkOrder.plan_revision == venture.current_revision
    ).order_by(AgencyWorkOrder.created_at))).scalars().all()
    worker_ids = {x.worker_profile_id for x in orders if x.worker_profile_id is not None}
    workers = {} if not worker_ids else {x.id: x for x in (await db.execute(
        select(AgencyWorkerProfile).where(AgencyWorkerProfile.id.in_(worker_ids)))).scalars().all()}
    return {
        "venture": {"id": venture.id, "key": venture.key, "title": venture.title,
                    "status": venture.status, "revision": venture.current_revision},
        "revision": {"digest": revision.digest, "created_at": revision.created_at,
                     "change_reason": revision.change_reason},
        "graph": revision.graph,
        "work_orders": [{"id": x.id, "plan_node_id": x.plan_node_id, "status": x.status,
            "task_class": x.task_class, "risk_tier": x.risk_tier,
            "worker": ({"id": workers[x.worker_profile_id].id,
                        "key": workers[x.worker_profile_id].key,
                        "display_name": workers[x.worker_profile_id].display_name,
                        "model": workers[x.worker_profile_id].model,
                        "harness": workers[x.worker_profile_id].harness}
                       if x.worker_profile_id in workers else None),
            "contract_digest": x.contract_digest, "completion": x.locked_completion,
            "audit": x.audit_state, "created_at": x.created_at,
            "completed_at": x.completed_at} for x in orders],
    }


@router.post("/ventures")
async def create_venture(req: VentureCreate, db: AsyncSession = Depends(get_db)):
    if await db.scalar(select(AgencyVenturePlan.id).where(AgencyVenturePlan.key == req.key)):
        raise HTTPException(409, "venture key already exists")
    try: graph = validate_plan_graph(req.graph)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    venture = AgencyVenturePlan(key=req.key, title=req.title, created_by=req.created_by)
    db.add(venture); await db.flush()
    digest = canonical_digest(graph)
    db.add(AgencyPlanRevision(venture_plan_id=venture.id, revision=1, graph=graph,
        digest=digest, change_reason=req.change_reason, created_by=req.created_by))
    await db.commit(); await db.refresh(venture)
    return {"id": venture.id, "key": venture.key, "revision": 1, "digest": digest}


@router.post("/ventures/{venture_key}/revisions")
async def revise_venture(venture_key: str, req: VentureRevisionCreate, db: AsyncSession = Depends(get_db)):
    venture = await db.scalar(select(AgencyVenturePlan).where(AgencyVenturePlan.key == venture_key))
    if venture is None: raise HTTPException(404, "venture not found")
    try: graph = validate_plan_graph(req.graph)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    venture.current_revision += 1
    digest = canonical_digest(graph)
    db.add(AgencyPlanRevision(venture_plan_id=venture.id, revision=venture.current_revision,
        graph=graph, digest=digest, change_reason=req.change_reason, created_by=req.created_by))
    await db.commit()
    return {"key": venture.key, "revision": venture.current_revision, "digest": digest}


@router.post("/ventures/{venture_key}/transition")
async def transition_venture(venture_key: str, req: VentureTransition, db: AsyncSession = Depends(get_db)):
    venture = await db.scalar(select(AgencyVenturePlan).where(AgencyVenturePlan.key == venture_key))
    if venture is None: raise HTTPException(404, "venture not found")
    if req.status not in PLAN_STATUSES: raise HTTPException(422, "invalid venture status")
    venture.status = req.status; await db.commit()
    return {"key": venture.key, "status": venture.status}


@router.post("/work-orders")
async def create_work_order(req: WorkOrderCreate, db: AsyncSession = Depends(get_db)):
    venture = await db.scalar(select(AgencyVenturePlan).where(AgencyVenturePlan.key == req.venture_key))
    worker, policy = await db.get(AgencyWorkerProfile, req.worker_profile_id), await db.get(AgencyPolicy, req.policy_id)
    if venture is None or worker is None or policy is None: raise HTTPException(404, "venture, worker, or policy not found")
    revision = await db.scalar(select(AgencyPlanRevision).where(
        AgencyPlanRevision.venture_plan_id == venture.id,
        AgencyPlanRevision.revision == venture.current_revision))
    try: row = await issue_work_order(db, venture=venture, revision=revision, node_id=req.node_id,
        worker=worker, policy=policy, permissions=req.permissions, ttl_minutes=req.ttl_minutes)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    return {"id": row.id, "status": row.status, "contract": row.contract}


@router.get("/work-orders")
async def list_work_orders(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(AgencyWorkOrder).order_by(AgencyWorkOrder.created_at.desc()).limit(200))).scalars().all()
    return [{"id": x.id, "plan_node_id": x.plan_node_id, "status": x.status,
             "worker_profile_id": x.worker_profile_id, "task_class": x.task_class,
             "risk_tier": x.risk_tier, "contract_digest": x.contract_digest,
             "completion": x.locked_completion, "audit": x.audit_state} for x in rows]


@router.get("/work-orders/{work_order_id}/origin")
async def get_origin_hook(work_order_id: str, db: AsyncSession = Depends(get_db)):
    row = await db.get(AgencyWorkOrder, work_order_id)
    if row is None: raise HTTPException(404, "work order not found")
    lease = await db.scalar(select(AgencyPermissionLease).where(AgencyPermissionLease.work_order_id == row.id))
    return {"work_order": row.specification, "reward_contract": row.contract,
            "permission_lease": {"scope": lease.scope, "expires_at": lease.expires_at, "status": lease.status}}


@router.post("/work-orders/{work_order_id}/complete")
async def complete_work_order(work_order_id: str, req: CompletionCreate, db: AsyncSession = Depends(get_db)):
    row = await db.get(AgencyWorkOrder, work_order_id)
    if row is None: raise HTTPException(404, "work order not found")
    if row.locked_completion is not None: raise HTTPException(409, "completion claims already locked")
    try: locked = lock_completion(req.model_dump())
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    worker, policy = await db.get(AgencyWorkerProfile, row.worker_profile_id), await db.get(AgencyPolicy, row.policy_id)
    row.locked_completion, row.completed_at, row.status = locked, datetime.utcnow(), "submitted"
    row.audit_state = choose_audit(work_order_id=row.id, risk_tier=row.risk_tier, config=policy.config, worker=worker)
    await add_event(db, row.id, "claims_locked", f"worker:{worker.key}", {"digest": locked["digest"]})
    await db.commit()
    return {"id": row.id, "status": row.status, "completion_digest": locked["digest"], "audit": row.audit_state}


@router.post("/work-orders/{work_order_id}/audit")
async def audit_work_order(work_order_id: str, req: AuditResultCreate, db: AsyncSession = Depends(get_db)):
    row = await db.get(AgencyWorkOrder, work_order_id)
    if row is None or row.locked_completion is None: raise HTTPException(409, "submitted work order required")
    worker = await db.get(AgencyWorkerProfile, row.worker_profile_id)
    if req.verifier in (worker.key, f"worker:{worker.key}"): raise HTTPException(409, "worker cannot self-verify")
    row.status = "verified" if req.passed else "failed"
    state = dict(row.audit_state); state.update({"passed": req.passed, "verifier": req.verifier,
        "evidence": req.evidence, "defects": req.defects}); row.audit_state = state
    invalidated = [] if req.passed else await invalidate_descendants(db, row)
    await add_event(db, row.id, "audit_passed" if req.passed else "audit_failed", req.verifier,
                    {"evidence": req.evidence, "defects": req.defects, "invalidated": invalidated})
    await db.commit()
    return {"id": row.id, "status": row.status, "invalidated": invalidated}


@router.get("/work-orders/{work_order_id}/events")
async def get_work_order_events(work_order_id: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(AgencyWorkOrderEvent).where(
        AgencyWorkOrderEvent.work_order_id == work_order_id).order_by(AgencyWorkOrderEvent.id))).scalars().all()
    return [{"id": x.id, "event_type": x.event_type, "actor": x.actor,
             "payload": x.payload, "created_at": x.created_at} for x in rows]


@router.get("/policies")
async def list_policies(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(AgencyPolicy).order_by(AgencyPolicy.id.desc()))).scalars().all()
    return [{"id": x.id, "name": x.name, "version": x.version, "status": x.status,
             "mode": x.mode, "description": x.description, "config": x.config,
             "created_at": x.created_at} for x in rows]


@router.post("/policies")
async def create_policy(req: PolicyCreate, db: AsyncSession = Depends(get_db)):
    if req.mode not in POLICY_MODES:
        raise HTTPException(422, "invalid policy mode")
    try:
        config = validate_policy(req.config)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    version = (await db.execute(select(func.max(AgencyPolicy.version)).where(
        AgencyPolicy.name == req.name))).scalar_one_or_none() or 0
    row = AgencyPolicy(name=req.name, version=version + 1, status="draft", mode=req.mode,
                       config=config, description=req.description, created_by="user")
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"id": row.id, "version": row.version, "status": row.status}


@router.post("/policies/{policy_id}/transition")
async def transition_policy(policy_id: int, req: PolicyTransition,
                            db: AsyncSession = Depends(get_db)):
    row = await db.get(AgencyPolicy, policy_id)
    if row is None:
        raise HTTPException(404, "policy not found")
    allowed = {"draft": {"simulated"}, "simulated": {"approved"},
               "approved": {"live", "retired"}, "live": {"retired"}, "retired": set()}
    if req.status not in POLICY_STATUSES or req.status not in allowed[row.status]:
        raise HTTPException(409, f"invalid transition {row.status} -> {req.status}")
    row.status = req.status
    if req.status == "live":
        row.activated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"id": row.id, "status": row.status}


@router.post("/workers")
async def create_worker(req: WorkerCreate, db: AsyncSession = Depends(get_db)):
    if req.role not in ROLES:
        raise HTTPException(422, f"role must be one of {ROLES}")
    if any(x < 1 or x > 5 for x in req.risk_tiers):
        raise HTTPException(422, "risk tiers must be 1..5")
    if await db.scalar(select(AgencyWorkerProfile.id).where(AgencyWorkerProfile.key == req.key)):
        raise HTTPException(409, "worker key already exists")
    row = AgencyWorkerProfile(key=req.key, display_name=req.display_name, role=req.role,
        model=req.model, harness=req.harness, task_classes=req.task_classes,
        risk_tiers=req.risk_tiers, skill_ids=req.skill_ids,
        permission_ceiling=req.permission_ceiling, agency_capital=req.starting_capital,
        peak_capital=req.starting_capital, score_totals={})
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"id": row.id, "key": row.key, "agency_capital": row.agency_capital}


@router.post("/experiments")
async def create_experiment(req: ExperimentCreate, db: AsyncSession = Depends(get_db)):
    if req.mode not in POLICY_MODES:
        raise HTTPException(422, "invalid experiment mode")
    existing = set((await db.execute(select(AgencyPolicy.id).where(
        AgencyPolicy.id.in_(req.policy_ids)))).scalars())
    if existing != set(req.policy_ids):
        raise HTTPException(422, "one or more policy ids do not exist")
    required = {"model", "harness", "task_class", "risk_tier"}
    if not required <= set(req.dimensions):
        raise HTTPException(422, f"dimensions must include {sorted(required)}")
    row = AgencyExperiment(name=req.name, mode=req.mode, policy_ids=req.policy_ids,
        dimensions=req.dimensions, primary_metric=req.primary_metric,
        guardrails=req.guardrails, sample_target=req.sample_target)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"id": row.id, "status": row.status}


@router.post("/score")
async def create_score(req: ScoreCreate, db: AsyncSession = Depends(get_db)):
    worker = await db.get(AgencyWorkerProfile, req.worker_profile_id)
    policy = await db.get(AgencyPolicy, req.policy_id)
    if worker is None or policy is None:
        raise HTTPException(404, "worker or policy not found")
    # Simulation/shadow policies measure behavior but cannot widen permissions;
    # capital remains a ledger signal until a policy is explicitly live.
    try:
        row = await record_score(db, worker, policy, req.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"id": row.id, "points_total": row.points_total,
            "settled_points": row.settled_points, "escrow_points": row.escrow_points,
            "capital_after": worker.agency_capital, "worker_status": worker.status}


@router.post("/simulate")
async def simulate(req: ScoreCreate, db: AsyncSession = Depends(get_db)):
    policy = await db.get(AgencyPolicy, req.policy_id)
    if policy is None:
        raise HTTPException(404, "policy not found")
    try:
        return score_event(req.event_type, policy.config,
            forecast_probability=req.forecast_probability, resolved_outcome=req.resolved_outcome,
            severity=req.severity, pre_submission=req.pre_submission,
            reproducible=req.reproducible, known_failure=req.known_failure)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/scores/{score_id}/settle")
async def settle_score(score_id: int, req: SettlementCreate,
                       db: AsyncSession = Depends(get_db)):
    original = await db.get(AgencyScoreEvent, score_id)
    if original is None:
        raise HTTPException(404, "score event not found")
    if original.escrow_points <= 0:
        raise HTTPException(409, "score event has no positive escrow to settle")
    try:
        row = await settle_event(db, original, passed=req.passed, evidence=req.evidence)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    worker = await db.get(AgencyWorkerProfile, original.worker_profile_id)
    return {"id": row.id, "event_type": row.event_type,
            "capital_adjustment": row.settled_points,
            "escrow_offset": row.escrow_points,
            "capital_after": worker.agency_capital if worker else None}
