#!/usr/bin/env python3
"""Live round-trip probe for the unified roadmap → Agency Lab lifecycle."""

from __future__ import annotations

import asyncio
import json

import httpx
from sqlalchemy import delete, select

from app.database import async_session
from app.models import (
    AgencyPermissionLease, AgencyPlanRevision, AgencyVenturePlan,
    AgencyWorkOrder, AgencyWorkOrderEvent, RoadmapLedger,
)


BASE = "http://127.0.0.1:8005"
SLUG = "roadmap-integration-probe"


async def cleanup() -> None:
    async with async_session() as db:
        ledger = await db.scalar(select(RoadmapLedger).where(RoadmapLedger.slug == SLUG))
        if ledger is None:
            return
        venture = await db.scalar(select(AgencyVenturePlan).where(
            AgencyVenturePlan.key == f"roadmap-{ledger.id}-{ledger.slug}"[:120]
        ))
        if venture is not None:
            order_ids = list((await db.execute(select(AgencyWorkOrder.id).where(
                AgencyWorkOrder.venture_plan_id == venture.id
            ))).scalars())
            if order_ids:
                await db.execute(delete(AgencyWorkOrderEvent).where(
                    AgencyWorkOrderEvent.work_order_id.in_(order_ids)))
                await db.execute(delete(AgencyPermissionLease).where(
                    AgencyPermissionLease.work_order_id.in_(order_ids)))
                await db.execute(delete(AgencyWorkOrder).where(
                    AgencyWorkOrder.id.in_(order_ids)))
            await db.execute(delete(AgencyPlanRevision).where(
                AgencyPlanRevision.venture_plan_id == venture.id))
            await db.delete(venture)
        await db.delete(ledger)
        await db.commit()


async def request(client: httpx.AsyncClient, method: str, path: str, **kwargs) -> httpx.Response:
    response = await client.request(method, path, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path}: HTTP {response.status_code} {response.text}")
    return response


async def run() -> None:
    await cleanup()
    receipt: dict = {}
    try:
        state = {
            "version": 1,
            "updatedAt": "2026-07-25T00:00:00+00:00",
            "sections": [{"id": "probe", "label": "Probe", "color": "#3987e5"}],
            "nodes": [
                {"id": "foundation", "section": "probe", "label": "Foundation",
                 "status": "done"},
                {"id": "round-trip", "section": "probe", "label": "Round trip",
                 "status": "planned", "summary": "Prove the unified lifecycle.",
                 "prereqs": ["foundation"],
                 "horizon": "horizon-2",
                 "reviewCadence": "monthly",
                 "nextReviewAt": "2026-01-01T00:00:00+00:00",
                 "assumptions": [{
                     "id": "integration-value",
                     "statement": "Strategic review and delivery belong in one ledger.",
                     "status": "standing",
                     "confidence": 75,
                     "evidenceFor": ["Unified lifecycle design"],
                     "evidenceAgainst": [],
                     "invalidationTrigger": "Review evidence cannot return without closing delivery",
                     "consequence": "Separate the strategic review surface",
                 }],
                 "verification": ["completion claims lock", "independent audit passes"]},
            ],
            "edges": [{"from": "foundation", "to": "round-trip", "type": "enables"}],
            "milestones": [],
        }
        async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
            created = (await request(client, "POST", "/roadmap-ledgers", json={
                "slug": SLUG, "name": "Roadmap Integration Probe",
                "description": "Disposable live verification fixture.", "state": state,
            })).json()
            receipt["created"] = {
                "revision": created["revision"], "records": created["summary"]["records"],
            }

            edited_state = created["state"]
            edited_state["nodes"][1]["prompt"] = "Execute one bounded live lifecycle."
            edited = (await request(
                client, "PUT", f"/roadmap-ledgers/{SLUG}",
                json={"expected_revision": created["revision"], "state": edited_state},
            )).json()
            stale = await client.put(
                f"/roadmap-ledgers/{SLUG}",
                json={"expected_revision": created["revision"], "state": edited["state"]},
            )
            if stale.status_code != 409:
                raise RuntimeError(f"stale write expected 409, observed {stale.status_code}")
            receipt["optimistic_concurrency"] = {"fresh_revision": edited["revision"], "stale_status": 409}

            review_order = (await request(
                client, "POST", f"/roadmap-ledgers/{SLUG}/nodes/round-trip/review",
                json={
                    "expected_revision": edited["revision"],
                    "worker_profile_id": 7,
                    "policy_id": 1,
                    "task_class": "strategic-review",
                    "risk_tier": 1,
                    "permissions": {
                        "filesystem": "read-only",
                        "commands": ["read"],
                        "network": True,
                    },
                    "ttl_minutes": 10,
                },
            )).json()
            review_id = review_order["id"]
            if review_order["kind"] != "strategic-review":
                raise RuntimeError("review endpoint did not issue a strategic-review order")
            await request(
                client, "POST", f"/agency-lab/work-orders/{review_id}/complete",
                json={
                    "disposition": "complete",
                    "claims": [{"claim": "unified lifecycle assumption remains supported"}],
                    "confidence": 0.95,
                    "limitations": ["disposable integration fixture"],
                    "disclosures": [],
                    "evidence": [{"kind": "api-round-trip", "observed": True}],
                    "next_action": "retain and schedule next review",
                },
            )
            await request(
                client, "POST", f"/agency-lab/work-orders/{review_id}/audit",
                json={
                    "passed": True,
                    "verifier": "live-strategy-critic-1",
                    "evidence": {"review_contract": "observed"},
                    "defects": [],
                },
            )
            reviewed = (await request(
                client, "POST",
                f"/roadmap-ledgers/{SLUG}/nodes/round-trip/accept/{review_id}",
                json={"expected_revision": edited["revision"]},
            )).json()
            reviewed_node = next(
                node for node in reviewed["state"]["nodes"]
                if node["id"] == "round-trip"
            )
            if (
                reviewed_node["status"] != "planned"
                or not reviewed_node.get("lastReviewedAt")
                or not reviewed_node.get("nextReviewAt")
                or len(reviewed_node.get("reviewHistory", [])) != 1
            ):
                raise RuntimeError(
                    "accepted review did not preserve delivery state and advance cadence"
                )
            receipt["strategic_review"] = {
                "kind": review_order["kind"],
                "ledger_status": reviewed_node["status"],
                "next_review_at": reviewed_node["nextReviewAt"],
                "history": len(reviewed_node["reviewHistory"]),
            }

            commissioned = (await request(
                client, "POST", f"/roadmap-ledgers/{SLUG}/nodes/round-trip/commission",
                json={
                    "expected_revision": reviewed["revision"],
                    "worker_profile_id": 7,
                    "policy_id": 1,
                    "task_class": "integration-test",
                    "risk_tier": 1,
                    "permissions": {"filesystem": "none", "commands": ["read"], "network": False},
                    "ttl_minutes": 10,
                },
            )).json()
            if not commissioned.get("worker") or not commissioned.get("policy"):
                raise RuntimeError("commission response omitted human-readable worker or policy")
            work_id = commissioned["id"]
            receipt["commissioned"] = {
                "id": work_id,
                "status": commissioned["status"],
                "roadmap_revision": commissioned["roadmap_revision"],
                "worker": commissioned["worker"]["display_name"],
                "model": commissioned["worker"]["model"],
                "policy": (
                    f'{commissioned["policy"]["name"]} '
                    f'v{commissioned["policy"]["version"]}'
                ),
            }

            submitted = (await request(
                client, "POST", f"/agency-lab/work-orders/{work_id}/complete",
                json={
                    "disposition": "complete",
                    "claims": [{"claim": "live lifecycle completed"}],
                    "confidence": 0.99,
                    "limitations": [],
                    "disclosures": [],
                    "evidence": [{"kind": "api-round-trip", "observed": True}],
                    "next_action": "independent audit",
                },
            )).json()
            audited = (await request(
                client, "POST", f"/agency-lab/work-orders/{work_id}/audit",
                json={"passed": True, "verifier": "live-critic-1",
                      "evidence": {"probe": "observed"}, "defects": []},
            )).json()
            accepted = (await request(
                client, "POST",
                f"/roadmap-ledgers/{SLUG}/nodes/round-trip/accept/{work_id}",
                json={"expected_revision": reviewed["revision"]},
            )).json()
            closed = next(node for node in accepted["state"]["nodes"] if node["id"] == "round-trip")
            if closed["status"] != "done" or closed["acceptedWorkOrderId"] != work_id:
                raise RuntimeError("verified delivery did not close the roadmap record")
            listed = (await request(client, "GET", f"/roadmap-ledgers/{SLUG}/work-orders")).json()
            if not listed[0].get("worker") or not listed[0].get("policy"):
                raise RuntimeError("work register omitted human-readable worker or policy")
            receipt["return_path"] = {
                "submitted": submitted["status"],
                "audited": audited["status"],
                "ledger_status": closed["status"],
                "accepted_work_order": closed["acceptedWorkOrderId"],
                "listed_orders": len(listed),
            }
            if len(listed) != 2:
                raise RuntimeError(f"work register expected 2 orders, observed {len(listed)}")
        print(json.dumps(receipt, indent=2))
    finally:
        await cleanup()


if __name__ == "__main__":
    asyncio.run(run())
