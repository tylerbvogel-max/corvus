"""Region policy router — the controller console for per-silo configuration.

One shared substrate, per-region knobs: scoring weights, loop config, ACL,
write-gate overrides, ontology projection. This is the "tunable controller
layer" surface of the two-layer product.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AutopilotConfig, Neuron, RegionPolicy
from app.services.region_policy import (
    WEIGHT_KEYS, default_weights, invalidate_region_policy_cache,
)

router = APIRouter(prefix="/admin/regions", tags=["regions"])


class RegionPolicyIn(BaseModel):
    display_name: str | None = None
    description: str | None = None
    scoring_weights: dict | None = None
    loop_config: dict | None = None
    acl: dict | None = None
    write_gate: dict | None = None
    projection: dict | None = None
    is_active: bool = True


def _policy_out(p: RegionPolicy, neuron_count: int = 0) -> dict:
    return {
        "id": p.id,
        "region": p.region,
        "display_name": p.display_name,
        "description": p.description,
        "scoring_weights": p.scoring_weights,
        "effective_weights": {
            **default_weights(), **(p.scoring_weights or {}),
        },
        "loop_config": p.loop_config,
        "acl": p.acl,
        "write_gate": p.write_gate,
        "projection": p.projection,
        "is_active": p.is_active,
        "neuron_count": neuron_count,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


def _validate_policy(body: RegionPolicyIn) -> None:
    if body.scoring_weights:
        unknown = set(body.scoring_weights) - set(WEIGHT_KEYS)
        if unknown:
            raise HTTPException(
                400, f"Unknown scoring weight keys: {sorted(unknown)}; "
                     f"allowed: {list(WEIGHT_KEYS)}",
            )
    acl_visibility = (body.acl or {}).get("visibility")
    if acl_visibility not in (None, "open", "restricted"):
        raise HTTPException(400, "acl.visibility must be 'open' or 'restricted'")


@router.get("")
async def list_regions(db: AsyncSession = Depends(get_db)) -> dict:
    """All regions on the substrate: policy (if configured) + neuron counts."""
    counts = dict((await db.execute(
        select(Neuron.department, func.count(Neuron.id))
        .where(Neuron.is_active.is_(True), Neuron.department.isnot(None))
        .group_by(Neuron.department)
    )).all())
    policies = (await db.execute(select(RegionPolicy))).scalars().all()
    policy_by_region = {p.region: p for p in policies}

    regions = []
    for region in sorted(set(counts) | set(policy_by_region)):
        policy = policy_by_region.get(region)
        if policy:
            regions.append(_policy_out(policy, counts.get(region, 0)))
        else:
            regions.append({
                "region": region,
                "neuron_count": counts.get(region, 0),
                "is_active": True,
                "effective_weights": default_weights(),
            })
    return {"regions": regions, "default_weights": default_weights()}


@router.get("/{region}")
async def get_region(region: str, db: AsyncSession = Depends(get_db)) -> dict:
    policy = (await db.execute(
        select(RegionPolicy).where(RegionPolicy.region == region)
    )).scalar_one_or_none()
    if not policy:
        raise HTTPException(404, f"No policy configured for region {region!r}")
    count = (await db.execute(
        select(func.count(Neuron.id)).where(
            Neuron.is_active.is_(True), Neuron.department == region,
        )
    )).scalar() or 0
    return _policy_out(policy, count)


@router.put("/{region}")
async def upsert_region(
    region: str, body: RegionPolicyIn, db: AsyncSession = Depends(get_db),
) -> dict:
    """Create or update a region's policy. The write is audited via the
    HTTP audit middleware; scoring/ACL caches are invalidated."""
    _validate_policy(body)

    policy = (await db.execute(
        select(RegionPolicy).where(RegionPolicy.region == region)
    )).scalar_one_or_none()
    if policy is None:
        policy = RegionPolicy(region=region)
        db.add(policy)

    policy.display_name = body.display_name
    policy.description = body.description
    policy.scoring_weights = body.scoring_weights
    policy.loop_config = body.loop_config
    policy.acl = body.acl
    policy.write_gate = body.write_gate
    policy.projection = body.projection
    policy.is_active = body.is_active
    policy.updated_at = datetime.utcnow()

    await _sync_region_loop_config(db, region, body.loop_config)

    await db.commit()
    invalidate_region_policy_cache()
    await db.refresh(policy)
    return _policy_out(policy)


async def _sync_region_loop_config(
    db: AsyncSession, region: str, loop_config: dict | None,
) -> None:
    """Mirror a policy's loop_config into a per-region AutopilotConfig row."""
    existing = (await db.execute(
        select(AutopilotConfig).where(AutopilotConfig.region == region)
    )).scalar_one_or_none()
    if not loop_config:
        if existing:
            existing.enabled = False
        return

    if existing is None:
        max_id = (await db.execute(
            select(func.coalesce(func.max(AutopilotConfig.id), 0))
        )).scalar() or 0
        existing = AutopilotConfig(id=max_id + 1, region=region)
        db.add(existing)

    existing.enabled = bool(loop_config.get("enabled", False))
    existing.interval_minutes = int(loop_config.get("interval_minutes", 30))
    existing.directive = str(loop_config.get("directive", ""))
    existing.eval_model = str(loop_config.get("eval_model", "haiku"))
