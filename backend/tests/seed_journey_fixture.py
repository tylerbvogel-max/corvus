"""Seed the throwaway journey database for the operator-journey browser specs.

Invoked by tests/run_journey_backend.sh after `alembic upgrade head` and
before uvicorn starts (roadmap record durability-frontend-contracts,
verification #6). Seeds exactly what the three journeys click:

  1. One neuron + one proposed AutopilotProposal with a summary-update item
     whose old_value byte-matches the live neuron summary (revalidate_items
     terminally supersedes on any drift — the match is load-bearing).
  2. One completed IntegrityScan + one open stale_content finding
     (the only finding type with direct-resolve buttons — one click,
     no proposal round-trip).
  3. One RoadmapLedger at revision 1 with a single active record that can
     accept a reconciliation receipt.

The neuron takes explicit id 9001: seed_core_data runs at app startup —
AFTER this script — and inserts its own rows at low explicit ids, so a
sequence-assigned id here would collide with it. Embedding is left empty
on purpose; startup auto_embed_neurons covers it with the local model.
"""

import asyncio
import os
from urllib.parse import urlsplit

_db = urlsplit(os.environ.get("DATABASE_URL", "").replace("+asyncpg", "")).path.lstrip("/")
assert _db.startswith("corvus_test_"), (
    f"refusing to seed {_db or '<unset DATABASE_URL>'}: journey fixtures "
    "run only against throwaway corvus_test_* databases"
)

NEURON_SUMMARY = "Baseline summary before the journey approval."
NEURON_NEW_SUMMARY = "Summary refined by the operator journey spec."
LEDGER_SLUG = "journey-fixture"
RECORD_ID = "journey-record-1"


async def main() -> None:
    from app.database import async_session
    from app.models import (
        AutopilotProposal, IntegrityFinding, IntegrityScan, Neuron,
        ProposalItem, RoadmapLedger,
    )

    async with async_session() as db:
        db.add(Neuron(
            id=9001, layer=3, node_type="lesson",
            label="journey-target-neuron",
            content="Operator journey fixture neuron. Exists to receive one "
                    "summary refinement through the real proposal lifecycle.",
            summary=NEURON_SUMMARY, department="Environment",
            invocations=3, avg_utility=0.5, is_active=True,
            authority_level="team",
        ))
        await db.flush()

        proposal = AutopilotProposal(
            state="proposed", gap_source="manual",
            gap_description="Journey fixture: refine the summary of the "
                            "journey-target neuron.",
            priority_score=0.5, eval_overall=4, llm_model="journey-seed",
        )
        db.add(proposal)
        await db.flush()
        db.add(ProposalItem(
            proposal_id=proposal.id, action="update", target_neuron_id=9001,
            field="summary", old_value=NEURON_SUMMARY,
            new_value=NEURON_NEW_SUMMARY,
            reason="journey fixture seed",
        ))

        scan = IntegrityScan(
            scan_type="aging_review", scope="global", status="completed",
            findings_count=1, initiated_by="journey-seed",
        )
        db.add(scan)
        await db.flush()
        db.add(IntegrityFinding(
            scan_id=scan.id, finding_type="stale_content", severity="warning",
            priority_score=0.9,
            description="Journey fixture: seeded stale-content finding.",
            neuron_ids_json="[9001]", status="open",
        ))

        db.add(RoadmapLedger(
            slug=LEDGER_SLUG, name="Journey Fixture Project",
            description="Throwaway ledger for the reconciliation journey.",
            state={
                "version": 1,
                "updatedAt": "2026-08-02T00:00:00+00:00",
                "sections": [
                    {"id": "roadmap", "label": "Roadmap", "color": "#3987e5"},
                ],
                "nodes": [{
                    "id": RECORD_ID,
                    "label": "Journey record",
                    "section": "roadmap",
                    "status": "active",
                    "summary": "A record that accepts one reconciliation "
                               "receipt during the journey spec.",
                    "verification": ["Journey spec passes"],
                    "prereqs": [],
                }],
                "edges": [],
                "milestones": [],
            },
        ))
        await db.commit()

    print(f"journey fixture seeded into {_db}: neuron 9001, "
          f"proposal {proposal.id}, finding on scan {scan.id}, "
          f"ledger {LEDGER_SLUG}")


if __name__ == "__main__":
    asyncio.run(main())
