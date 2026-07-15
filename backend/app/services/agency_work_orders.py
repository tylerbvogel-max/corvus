"""Agent-native venture plans, signed reward contracts, and audit routing."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (AgencyPermissionLease, AgencyPlanRevision, AgencyPolicy,
                        AgencyVenturePlan, AgencyWorkerProfile, AgencyWorkOrder,
                        AgencyWorkOrderEvent)
from app.services.agency_lab import agency_wager

PLAN_STATUSES = ("draft", "active", "paused", "complete", "abandoned")
WORK_STATUSES = ("issued", "accepted", "submitted", "auditing", "verified", "failed", "invalidated")


def canonical_digest(value: dict) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(body.encode()).hexdigest()


def validate_plan_graph(graph: dict) -> dict:
    nodes = graph.get("nodes")
    edges = graph.get("edges", [])
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("north-star graph requires at least one node")
    ids = [n.get("id") for n in nodes]
    if any(not x or not isinstance(x, str) for x in ids) or len(ids) != len(set(ids)):
        raise ValueError("north-star node ids must be unique non-empty strings")
    required = {"id", "title", "outcome", "acceptance", "risk_tier", "task_class"}
    for node in nodes:
        missing = required - set(node)
        if missing:
            raise ValueError(f"node {node.get('id')} missing {sorted(missing)}")
        if not 1 <= int(node["risk_tier"]) <= 5 or not isinstance(node["acceptance"], list):
            raise ValueError(f"node {node['id']} has invalid risk or acceptance criteria")
    known = set(ids)
    if any(e.get("from") not in known or e.get("to") not in known for e in edges):
        raise ValueError("every edge must reference known nodes")
    return {"mission": graph.get("mission", ""), "constraints": graph.get("constraints", []),
            "kill_criteria": graph.get("kill_criteria", []), "nodes": nodes, "edges": edges}


def compile_contract(*, work_order_id: str, node: dict, policy: AgencyPolicy,
                     worker: AgencyWorkerProfile, plan_digest: str, lease_scope: dict) -> dict:
    wager = agency_wager(worker.beta_alpha, worker.beta_beta, worker.agency_capital, policy.config)
    completion_schema = {
        "type": "object", "additionalProperties": False,
        "required": ["disposition", "claims", "confidence", "limitations", "disclosures", "evidence", "next_action"],
        "properties": {
            "disposition": {"enum": ["complete", "partial", "failed", "blocked"]},
            "claims": {"type": "array", "items": {"type": "object"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "limitations": {"type": "array", "items": {"type": "string"}},
            "disclosures": {"type": "array", "items": {"type": "object"}},
            "evidence": {"type": "array", "items": {"type": "object"}},
            "next_action": {"type": "string"},
        },
    }
    public = {
        "schema": "corvus.reward-contract/v1", "work_order_id": work_order_id,
        "plan_digest": plan_digest, "policy": {"id": policy.id, "version": policy.version,
        "digest": canonical_digest(policy.config)}, "opening_capital": worker.agency_capital,
        "capital_at_risk": wager["capital_at_risk"], "escrow_rules": policy.config["settlement"],
        "rewards": {k: v for k, v in policy.config.items() if k in ("delivery", "disclosure", "critic", "calibration")},
        "clawbacks": policy.config["concealment"], "audit_floor": policy.config["audit"]["base_probability"],
        "safe_harbor": "Disclose uncertainty or failure before submission; reproducible evidence earns more.",
        "permissions": lease_scope, "acceptance": node["acceptance"],
        "required_exit": ["disposition", "claims", "confidence", "limitations", "disclosures", "evidence", "next_action"],
        "completion_schema": completion_schema,
        "invariants": ["worker_cannot_self_verify", "claims_lock_at_submission", "contract_is_immutable", "permissions_expire"],
    }
    public["digest"] = canonical_digest(public)
    return public


def choose_audit(*, work_order_id: str, risk_tier: int, config: dict,
                 worker: AgencyWorkerProfile) -> dict:
    audit = config["audit"]
    probability = float(audit["base_probability"])
    if risk_tier >= 3:
        probability *= float(audit["high_risk_multiplier"])
    if risk_tier >= int(audit["mandatory_risk_tier"]) or worker.beta_alpha + worker.beta_beta <= 6:
        probability = 1.0
    probability = min(1.0, probability)
    secret = os.getenv("AGENCY_AUDIT_SECRET", "corvus-development-audit-secret")
    draw = int.from_bytes(hmac.new(secret.encode(), work_order_id.encode(), hashlib.sha256).digest()[:8], "big") / 2**64
    selected = draw < probability
    return {"deterministic_inspection": True, "critic_selected": selected,
            "critic_probability": round(probability, 4), "selection_basis": "private_hmac_draw",
            "reproduction_required": risk_tier >= 4, "adversarial_required": risk_tier >= 5}


async def add_event(db: AsyncSession, work_order_id: str, event_type: str, actor: str, payload: dict) -> None:
    db.add(AgencyWorkOrderEvent(work_order_id=work_order_id, event_type=event_type, actor=actor, payload=payload))


async def issue_work_order(db: AsyncSession, *, venture: AgencyVenturePlan, revision: AgencyPlanRevision,
                           node_id: str, worker: AgencyWorkerProfile, policy: AgencyPolicy,
                           permissions: dict, ttl_minutes: int) -> AgencyWorkOrder:
    node = next((n for n in revision.graph["nodes"] if n["id"] == node_id), None)
    if node is None:
        raise ValueError("plan node not found in pinned revision")
    work_id = f"wo-{venture.key}-{node_id}-{secrets.token_hex(4)}"
    contract = compile_contract(work_order_id=work_id, node=node, policy=policy, worker=worker,
                                plan_digest=revision.digest, lease_scope=permissions)
    row = AgencyWorkOrder(id=work_id, venture_plan_id=venture.id, plan_revision=revision.revision,
        plan_node_id=node_id, worker_profile_id=worker.id, policy_id=policy.id, status="issued",
        task_class=node["task_class"], risk_tier=node["risk_tier"], specification=node,
        contract=contract, contract_digest=contract["digest"], audit_state={})
    db.add(row)
    await db.flush()
    db.add(AgencyPermissionLease(work_order_id=work_id, scope=permissions,
        expires_at=datetime.utcnow() + timedelta(minutes=ttl_minutes)))
    await add_event(db, work_id, "contract_delivered", "corvus", {"digest": contract["digest"]})
    await db.commit(); await db.refresh(row)
    return row


def lock_completion(payload: dict) -> dict:
    required = {"disposition", "claims", "confidence", "limitations", "disclosures", "evidence", "next_action"}
    missing = required - set(payload)
    if missing:
        raise ValueError(f"completion missing {sorted(missing)}")
    if payload["disposition"] not in ("complete", "partial", "failed", "blocked"):
        raise ValueError("invalid disposition")
    if not isinstance(payload["claims"], list) or not 0 <= float(payload["confidence"]) <= 1:
        raise ValueError("claims must be a list and confidence must be 0..1")
    locked = dict(payload)
    locked["locked_at"] = datetime.now(timezone.utc).isoformat()
    locked["digest"] = canonical_digest(payload)
    return locked


async def invalidate_descendants(db: AsyncSession, failed: AgencyWorkOrder) -> list[str]:
    revision = await db.scalar(select(AgencyPlanRevision).where(
        AgencyPlanRevision.venture_plan_id == failed.venture_plan_id,
        AgencyPlanRevision.revision == failed.plan_revision))
    if revision is None:
        return []
    frontier, descendants = [failed.plan_node_id], set()
    while frontier:
        parent = frontier.pop()
        for edge in revision.graph.get("edges", []):
            if edge["from"] == parent and edge["to"] not in descendants:
                descendants.add(edge["to"]); frontier.append(edge["to"])
    rows = (await db.execute(select(AgencyWorkOrder).where(
        AgencyWorkOrder.venture_plan_id == failed.venture_plan_id,
        AgencyWorkOrder.plan_revision == failed.plan_revision,
        AgencyWorkOrder.plan_node_id.in_(descendants),
        AgencyWorkOrder.status.in_(("issued", "accepted", "submitted", "auditing"))))).scalars().all()
    for row in rows:
        row.status = "invalidated"
        await add_event(db, row.id, "upstream_invalidated", "corvus", {"upstream_work_order_id": failed.id})
    return [row.id for row in rows]
