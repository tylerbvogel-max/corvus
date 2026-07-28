#!/usr/bin/env python3
"""Add the token-pressure maintenance scheduling investigation record."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import async_session
from app.models import RoadmapLedger
from app.services.roadmap_admission import refresh_cache
from app.services.roadmap_ledger import advance_state, state_summary


SLUG = "corvus-long-horizon"
NODE_ID = "mind-token-pressure-maintenance"


PROMPT = """INVESTIGATION: token-pressure maintenance scheduling for Corvus-Mind.

Question:
Today Corvus-Mind maintenance activities are primarily wall-clock scheduled: some jobs run daily, others every 6 hours, etc. Investigate whether the primary trigger should instead be usage pressure, e.g. after Claude Code/Codex consume some threshold of transcript/model tokens, conversations are aggregated and applicable backend maintenance jobs run.

Initial design verdict:
Use a hybrid scheduler. Usage-pressure checkpoints should become the primary trigger because memory maintenance cost is caused by new cognitive material, not elapsed time. Wall-clock cadence should remain as a stale-data backstop.

Do NOT implement a single blunt global "run everything after 10M tokens" checkpoint unless the investigation proves it. The preferred direction is pressure-based scheduling with separate thresholds per maintenance class.

Candidate trigger model:
- raw_harness_tokens_since_distill
- candidate_memory_tokens_since_distill
- accepted_neuron_tokens_since_maintenance
- new_neuron_count_since_dedup
- updated_neuron_count_since_dedup
- cluster_pressure
- contradiction_pressure
- retrieval_failure_pressure
- maintenance_lag_tokens

Candidate job thresholds:
- Session ingest / candidate extraction: every 250k-1M new transcript tokens, with a wall-clock stale-data backstop.
- Dedup / graph lint: every 2M-5M new extracted memory tokens or about 50 new/updated durable neurons.
- Reconsolidation: every 5M-10M transcript tokens, or when cluster/contradiction pressure crosses threshold.
- Skill compilation: every accepted same-scope lesson cluster >= 3 or established cluster maturity threshold, with a wall-clock backstop.
- Deep janitor / auditor: every 10M-25M transcript tokens, weekly/monthly backstop, or elevated retrieval-failure/contradiction pressure.

Commercial / Performance-page rationale:
The Performance page currently reports hot-path query cost, graph-upkeep maintenance cost, maintenance_per_query, and maintenance_by_workload. Time cadence makes maintenance cost look arbitrary and can waste spend after quiet periods while delaying maintenance after heavy work. Token-pressure scheduling would let Corvus report better unit economics:
- maintenance_cost_per_1M_harness_tokens
- maintenance_cost_per_accepted_neuron
- maintenance_cost_per_100_queries
- maintenance_lag_tokens
- maintenance_saved_vs_wall_clock
- freshness SLA by tokens and by wall-clock age

Key product claim to test:
Small frequent ingest + medium periodic cleanup + rare large reconsolidation should keep memory fresh while reducing wasted maintenance and making amortized cost scale with actual usage.

Investigation tasks:
1. Inventory every current maintenance trigger and wall-clock cadence: distiller, janitors, dedup/graph lint, reconsolidation audit/review, skill compiler, action buses, and any autopilot/heartbeat path.
2. Identify the existing token sources available from Claude Code/Codex hooks, model usage ledger, episode logs, and backend query tables.
3. Define a canonical "maintenance pressure ledger" that records per-tenant counters, last-run checkpoints, and reset semantics.
4. Propose per-job threshold defaults with conservative backstops and explain why each threshold matches the job's cost/risk profile.
5. Model current maintenance cost under wall-clock scheduling versus token-pressure scheduling using Performance page data, including the current amortized maintenance/query cost.
6. Design fail-safe behavior for low-usage tenants, high-usage bursts, failed maintenance runs, and manual force-run operations.
7. Decide whether this should replace cron/systemd timers, wrap them, or be implemented as a scheduler layer called by the existing heartbeat.

Acceptance bar for implementation after investigation:
- Do not merely move cron values into token constants. The implementation must make maintenance fire from observed usage pressure.
- Wall-clock triggers must remain only as backstops or explicit freshness SLAs.
- Performance page must expose the new pressure metrics and show amortized cost against actual usage volume.
- The system must prevent maintenance starvation for quiet tenants and prevent runaway maintenance during heavy sessions.
"""


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

        now = datetime.now(timezone.utc).isoformat()
        node = {
            "id": NODE_ID,
            "label": "Investigate token-pressure maintenance scheduling",
            "section": "forward",
            "status": "proposed",
            "reviewCadence": "event",
            "reviewTrigger": (
                "Before further tuning of maintenance cadence, cost reporting, "
                "or production tenant economics."
            ),
            "prereqs": [
                "mind-full-benchmark-token-economics",
                "mind-retrieval-telemetry",
                "mind-reconsolidation-auditor",
                "mind-skill-signpost",
            ],
            "summary": (
                "Investigate replacing wall-clock-first Corvus-Mind maintenance "
                "cadence with usage-pressure checkpoints driven by harness tokens, "
                "memory-token growth, neuron churn, cluster pressure, and retrieval "
                "failure pressure, while keeping wall-clock freshness backstops."
            ),
            "prompt": PROMPT,
            "createdAt": now,
            "verification": [
                "Current wall-clock maintenance triggers and cadences are inventoried with code paths and workload names.",
                "Available token/counter sources are mapped: Claude Code/Codex hooks, model usage ledger, episode logs, queries, and neuron/proposal tables.",
                "A proposed maintenance pressure ledger schema defines counters, checkpoints, reset rules, tenant scope, and failure behavior.",
                "Per-job threshold defaults are justified against cost, freshness risk, and memory-quality risk.",
                "Performance-page metric changes are specified, including maintenance cost per 1M harness tokens, per accepted neuron, per 100 queries, and maintenance lag tokens.",
                "A cost model compares current wall-clock scheduling to token-pressure scheduling using current amortized maintenance/query data.",
                "Investigation explicitly decides whether to replace timers, wrap timers, or implement a scheduler layer called by the existing heartbeat.",
            ],
            "acceptanceCriteria": [
                "The recommended design makes observed usage pressure the primary trigger and wall-clock cadence a backstop.",
                "The design prevents low-usage maintenance starvation and high-usage runaway maintenance.",
                "The design avoids a single blunt global 10M-token checkpoint unless evidence supports it.",
                "The roadmap result includes concrete implementation steps and verification receipts for a later build node.",
            ],
        }
        state = {
            **ledger.state,
            "nodes": [*ledger.state["nodes"], node],
            "edges": [
                *ledger.state.get("edges", []),
                {
                    "from": "mind-full-benchmark-token-economics",
                    "to": NODE_ID,
                    "type": "motivates",
                },
                {
                    "from": "mind-retrieval-telemetry",
                    "to": NODE_ID,
                    "type": "feeds",
                },
                {
                    "from": "mind-reconsolidation-auditor",
                    "to": NODE_ID,
                    "type": "constrains",
                },
                {
                    "from": "mind-skill-signpost",
                    "to": NODE_ID,
                    "type": "constrains",
                },
            ],
            "updatedAt": now,
        }
        ledger.state = advance_state(state, int(ledger.state.get("version", 1)))
        ledger.revision += 1
        await db.commit()
        await db.refresh(ledger)
        await refresh_cache(db)
        print({
            "status": "recorded",
            "node_id": NODE_ID,
            "revision": ledger.revision,
            "version": ledger.state["version"],
            "summary": state_summary(ledger.state),
        })


if __name__ == "__main__":
    asyncio.run(run())
