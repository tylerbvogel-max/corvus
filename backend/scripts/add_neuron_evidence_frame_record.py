#!/usr/bin/env python3
"""Add the near-term neuron evidence-frame implementation record."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import async_session
from app.models import RoadmapLedger
from app.services.roadmap_admission import refresh_cache
from app.services.roadmap_ledger import advance_state, state_summary


SLUG = "corvus-long-horizon"
NODE_ID = "mind-neuron-evidence-frame"


PROMPT = """NEAR-TERM IMPLEMENTATION: structured neuron evidence frame for LoCoMo and Corvus-Mind memory construction.

Problem:
Corvus has invested heavily in retrieval, graph connection, spread activation, and token-bounded assembly, but neuron content is still too dependent on whatever an agent happens to write during distillation, janitor cleanup, update, or reconsolidation. LoCoMo misses can come from retrieved neurons that are topically right but not answer-reconstructable.

Design intent:
Neurons should be evidence-bearing answer capsules, not free-form prose blobs. Connections decide how memories meet each other; neuron fill must preserve enough grounded context for a future agent to answer the relevant question correctly without needing the original transcript.

Required construction syntax:
Every agent path that creates, updates, merges, combines, supersedes, or reconsolidates durable memories MUST emit and validate the following frame for each resulting neuron:

Claim:
Entities:
Time scope:
Context:
Evidence:
Future-use:
Likely queries:
Confidence:
Volatility:

Hard constraints:
- Agents MUST adhere to this syntax. Do not allow lazy natural-language summaries to pass as durable neuron content.
- The frame must be machine-checkable before write/approval. Missing headings, empty required slots, or malformed slot order must fail closed before a neuron is created, updated, merged, or approved.
- The schema must apply to memory construction paths, not only UI display or documentation. Cover distiller, janitor, dedup/consolidation, reconsolidation, proposal approval, and any LoCoMo-specific ingest/update path that writes neurons.
- Entities must include people, organizations, projects, tools, places, and objects needed for retrieval and answer reconstruction.
- Time scope must distinguish dated events, stable preferences, current plans, expired facts, and unknown/unstated timing.
- Evidence must cite the source conversation/session/event/proposal/tool receipt enough to audit the write. Do not invent evidence.
- Future-use must state why the fact matters for future assistance or benchmark answering.
- Likely queries must include natural question phrasings a future user/eval might ask.
- Confidence and volatility must be explicit. Volatile or uncertain claims must not be silently promoted as stable facts.
- Include why/motivation only when stated or strongly evidenced; otherwise record it as unknown rather than hallucinating.
- Merge/combine behavior must preserve all distinct entities, dates, constraints, exceptions, and contradictions. Combining similar topics must state whether the result reinforced, corrected, narrowed, superseded, or contradicted the prior memory.

Implementation notes:
- Prefer a shared parser/validator/helper so all memory-writing agents use the same contract.
- Store the canonical frame in neuron content or a structured companion field if the existing schema supports it without migration risk. If stored in content, preserve exact headings.
- Existing neurons do not need immediate full backfill, but any touched neuron must leave the pipeline in framed form.
- Add adversarial tests where an agent attempts to write a plausible prose summary, omits Evidence, omits Time scope, or collapses two similar but time-distinct facts; all must fail or require correction.

Evaluation:
Run a LoCoMo before/after slice that measures answerability, not just retrieval rank. Compare old-style vs framed-neuron construction on a fixed subset with temporal and multi-hop questions emphasized. Attribute effects through the Oracle Funnel where available: candidate, rank, assembly, synthesis, judge, success.
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
            "label": "Neuron evidence frame — answer-reconstructable memory syntax",
            "section": "forward",
            "status": "proposed",
            "reviewCadence": "event",
            "reviewTrigger": "Before the next LoCoMo tuning cycle or any broad janitor/reconsolidation change that rewrites durable memories.",
            "prereqs": [
                "standing-locomo-benchmark",
                "mind-oracle-funnel-locomo",
                "mind-neuron-granularity",
            ],
            "summary": (
                "Near-term LoCoMo and memory-quality test: enforce a shared structured "
                "evidence frame for all durable neuron construction so retrieved memories "
                "are answer-reconstructable rather than agent-authored prose blobs."
            ),
            "prompt": PROMPT,
            "createdAt": now,
            "verification": [
                "Shared frame parser/validator rejects missing headings, empty required slots, malformed order, and prose-only neuron writes.",
                "All durable memory construction paths are covered: distiller, janitor, dedup/consolidation, reconsolidation, proposal approval, and LoCoMo ingest/update writers.",
                "Unit or integration tests demonstrate fail-closed behavior for omitted Evidence, omitted Time scope, invented why/motivation, and lazy prose summaries.",
                "A touched-neuron migration rule is verified: existing neurons need not be bulk-backfilled, but any updated/combined neuron exits in framed syntax.",
                "LoCoMo before/after slice reports answerability and category impact, with Oracle Funnel attribution where available and no hidden corpus/config drift.",
            ],
            "acceptanceCriteria": [
                "Agents that construct memories MUST adhere to the frame syntax before any durable write or approval.",
                "The implementation makes lazy avoidance mechanically difficult by centralizing validation and failing closed.",
                "Merge/update prompts preserve entities, dates, constraints, exceptions, contradictions, and explicit change relationship.",
                "The record is not complete until live receipts show malformed construction attempts are blocked.",
            ],
        }
        state = {
            **ledger.state,
            "nodes": [*ledger.state["nodes"], node],
            "edges": [
                *ledger.state.get("edges", []),
                {
                    "from": "standing-locomo-benchmark",
                    "to": NODE_ID,
                    "type": "motivates",
                },
                {
                    "from": "mind-oracle-funnel-locomo",
                    "to": NODE_ID,
                    "type": "measures",
                },
                {
                    "from": "mind-neuron-granularity",
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
