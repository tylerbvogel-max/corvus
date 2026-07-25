#!/usr/bin/env python3
"""Idempotently record the strategic-plasticity delivery in the canonical ledger."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import async_session
from app.models import RoadmapLedger
from app.services.roadmap_ledger import advance_state, state_summary


SLUG = "corvus-long-horizon"
NODE_ID = "fwd-roadmap-strategic-plasticity"


async def run() -> None:
    async with async_session() as db:
        ledger = await db.scalar(
            select(RoadmapLedger)
            .where(RoadmapLedger.slug == SLUG)
            .with_for_update()
        )
        if ledger is None:
            raise RuntimeError(f"canonical ledger {SLUG!r} not found")
        existing = next(
            (node for node in ledger.state["nodes"] if node["id"] == NODE_ID),
            None,
        )
        if existing is not None:
            print({
                "status": "already-recorded",
                "revision": ledger.revision,
                "version": ledger.state["version"],
                "summary": state_summary(ledger.state),
            })
            return

        completed_at = datetime.now(timezone.utc).isoformat()
        node = {
            "id": NODE_ID,
            "label": "Strategic plasticity — horizons, assumptions, and review cycles",
            "section": "forward",
            "status": "done",
            "horizon": "active",
            "reviewCadence": "manual",
            "prereqs": ["fwd-roadmap-agency-ledger"],
            "summary": (
                "Long-horizon records now expose planning resolution, falsifiable "
                "assumptions, due-review signals, and Agency-commissioned evidence loops."
            ),
            "prompt": (
                "Implementation lives in backend/app/services/roadmap_ledger.py, "
                "backend/app/routers/roadmap_ledgers.py, and "
                "frontend/src/components/RoadmapLedgersPage.tsx."
            ),
            "completedAt": completed_at,
            "verification": [
                "Strategic fields reject malformed horizons, cadences, dates, and assumptions",
                "A due review compiles into a revision-pinned Agency work order",
                "Accepted review evidence advances cadence without closing delivery",
                "Normal delivery remains independently audited and human accepted",
                "Frontend production build and live browser verification pass",
            ],
            "verificationResults": {
                "backend_tests": "40 passed",
                "live_review": (
                    "due → commissioned → submitted → verified → human accepted "
                    "→ planned with next review"
                ),
                "live_delivery": (
                    "issued → submitted → verified → human accepted → done"
                ),
            },
        }
        state = {
            **ledger.state,
            "nodes": [*ledger.state["nodes"], node],
            "edges": [
                *ledger.state.get("edges", []),
                {
                    "from": "fwd-roadmap-agency-ledger",
                    "to": NODE_ID,
                    "type": "enables",
                },
            ],
        }
        ledger.state = advance_state(
            state, int(ledger.state.get("version", 1)),
        )
        ledger.revision += 1
        await db.commit()
        await db.refresh(ledger)
        print({
            "status": "recorded",
            "revision": ledger.revision,
            "version": ledger.state["version"],
            "summary": state_summary(ledger.state),
        })


if __name__ == "__main__":
    asyncio.run(run())
