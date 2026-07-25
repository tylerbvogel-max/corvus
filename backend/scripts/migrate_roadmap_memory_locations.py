#!/usr/bin/env python3
"""Stage the post-ledger-cutover neuron corrections as one audited proposal.

The graph remains untouched until a human approves the emitted proposal.
Re-running is safe: an existing proposed/applied migration is reused.
"""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import select

from app.database import async_session
from app.models import AutopilotProposal, Neuron, ProposalItem


DESCRIPTION = (
    "Complete the contextual sweep of retired Master Corvus roadmap-location "
    "claims with canonical Corvus-Mind Roadmap Ledger guidance"
)

UPDATES = {
    133: {
        "label": "Corvus-Mind Roadmap Ledgers are canonical project tracking",
        "summary": (
            "Canonical project roadmaps live in Corvus-Mind Roadmap Ledgers; "
            "the former static Master Corvus flowchart is archival only."
        ),
        "content": (
            "Corvus-Mind stores the canonical project Roadmap Ledgers in its "
            "roadmap_ledgers database model and exposes them through the "
            "Roadmaps UI and /roadmap-ledgers API. Each ledger can map to a "
            "repository through project_path. The former static Master Corvus "
            "flowchart is archival only and must not be used for current "
            "planning or mutation. Evidence: the corvus-long-horizon ledger "
            "was migrated with 129 records and verified at revision 5 / state "
            "61 on 2026-07-25."
        ),
    },
    142: {
        "label": "Corvus-Mind roadmap ledger location and access",
        "summary": (
            "Use the Corvus-Mind Roadmaps page, roadmap-ledgers API, and "
            "roadmap MCP tools for canonical forward planning."
        ),
        "content": (
            "Canonical Corvus planning state lives in Corvus-Mind's "
            "roadmap_ledgers table and /roadmap-ledgers API, surfaced through "
            "the Roadmaps page. Agents resolve ledgers by project_path and use "
            "roadmap_context and roadmap_admit. Do not read or edit the "
            "archived Master Corvus flowchart as forward state. Evidence: a "
            "fresh Codex session resolved corvus-long-horizon from the Corvus "
            "repository and emitted PlanningAdmissionPending before the "
            "ledger advanced to revision 5 on 2026-07-25."
        ),
    },
    256: {
        "label": "Store implementation context in Corvus-Mind ledger records",
        "summary": (
            "Put kickoff context and verification requirements in the relevant "
            "Corvus-Mind Roadmap Ledger record, not transient agent plan files."
        ),
        "content": (
            "When planning Corvus implementation work, put kickoff context—"
            "file paths, code locations, verification steps, and acceptance "
            "criteria—directly into the relevant record's prompt and "
            "verification fields in the Corvus-Mind Roadmap Ledger. Do not use "
            "transient Claude or Codex plan files as durable project memory. "
            "Commission the record into Agency Lab when it becomes executable. "
            "Evidence: the migrated corvus-long-horizon ledger preserves "
            "record prompts and can issue revision-pinned Agency Lab work orders."
        ),
    },
    265: {
        "content_replace": (
            (
                "Roadmap node `fwd-corvus-mind` (master-corvus) is `active`",
                "Roadmap record `fwd-corvus-mind` in the Corvus-Mind "
                "`corvus-long-horizon` ledger is `active`",
            ),
            (
                "Full kickoff context in the `fwd-corvus-mind` roadmap node.",
                "Full kickoff context lives in the `fwd-corvus-mind` record "
                "of the Corvus-Mind `corvus-long-horizon` ledger.",
            ),
        ),
    },
    266: {
        "content_replace": (
            (
                "- Master Corvus roadmap page: "
                "`~/Projects/master-corvus/src/components/opportunities/"
                "ContributionRoadmap.tsx`",
                "- Archived Master Corvus contribution analysis: "
                "`~/Projects/master-corvus/src/components/opportunities/"
                "ContributionRoadmap.tsx`; current execution planning belongs "
                "in a Corvus-Mind Roadmap Ledger record",
            ),
        ),
    },
    319: {
        "label": (
            "Corvus-Mind ledger records support prompt and disposition fields"
        ),
        "summary": (
            "Corvus-Mind Roadmap Ledger records retain prompt, disposition, "
            "resultRecap, verification, horizon, assumption, and review fields."
        ),
        "content": (
            "Records in Corvus-Mind Roadmap Ledger state retain prompt and "
            "disposition fields for kickoff plans and settlement status, plus "
            "resultRecap and verification evidence for completed work. "
            "Long-horizon records can also carry horizons, falsifiable "
            "assumptions, and review cadence. Evidence: the imported "
            "corvus-long-horizon ledger validates these fields through "
            "roadmap_ledger.py and exposes them in the Roadmaps UI and API."
        ),
    },
    1150: {
        "label": (
            "Corvus-Mind ledger resultRecap field stores completion evidence"
        ),
        "summary": (
            "Completed Corvus-Mind Roadmap Ledger records use resultRecap and "
            "verificationResults to preserve implementation and evaluation evidence."
        ),
        "content": (
            "Completed Corvus-Mind Roadmap Ledger records use resultRecap for "
            "the human-readable outcome and verificationResults for structured "
            "implementation and evaluation evidence. Agency Lab acceptance "
            "remains a separate evidence gate and can return receipts without "
            "silently closing durable roadmap intent. Evidence: migrated "
            "completed records retain resultRecap, and the admission-controller "
            "record closed at revision 5 with backend, frontend, startup, "
            "offline, harness, live-flow, UI, and trust receipts."
        ),
    },
    1201: {
        "label": "Reconsolidation concerns belong after the auditor record",
        "summary": (
            "Place reconsolidation research and discovery immediately after "
            "mind-reconsolidation-auditor in the Corvus-Mind project ledger."
        ),
        "content": (
            "The planned research and discovery work addressing "
            "reconsolidation design concerns belongs in the Corvus-Mind "
            "corvus-long-horizon ledger immediately after the "
            "mind-reconsolidation-auditor record, with the goal of defining a "
            "path forward. Evidence: Tyler explicitly directed placement after "
            "the auditor record; migration to Corvus-Mind preserves that ordering."
        ),
    },
    1255: {
        "label": (
            "Roadmap record mind-deeds-corroborated for agent-origin facts"
        ),
        "summary": (
            "The mind-deeds-corroborated feature remains represented in the "
            "migrated Corvus-Mind corvus-long-horizon ledger."
        ),
        "content": (
            "The roadmap record mind-deeds-corroborated is preserved in the "
            "Corvus-Mind corvus-long-horizon ledger. It covers admitting "
            "agent-asserted conclusions into the knowledge graph only when "
            "tool-event corroboration prevents memory pollution. Use the "
            "Corvus-Mind Roadmaps page or API to inspect or change this record. "
            "Evidence: the full legacy roadmap corpus was imported into the "
            "129-record corvus-long-horizon ledger on 2026-07-25."
        ),
    },
    1271: {
        "label": "corvus-singular-roadmap-location",
        "summary": (
            "Corvus-Mind Roadmap Ledgers are the only current forward-tracking "
            "surface; Master Corvus planning views are archival."
        ),
        "content": (
            "As of 2026-07-25, Corvus-Mind Roadmap Ledgers—stored in the "
            "roadmap_ledgers database model and accessed through the Roadmaps "
            "page, API, and roadmap MCP tools—are the only current "
            "forward-tracking surface for Corvus. Master Corvus planning views "
            "and the former static flowchart are archival historical framing. "
            "Never add or update forward work there. New work belongs in a "
            "project-mapped Corvus-Mind ledger and becomes executable through "
            "a revision-pinned Agency Lab work order."
        ),
    },
}


async def main() -> None:
    async with async_session() as db:
        existing = await db.scalar(
            select(AutopilotProposal)
            .where(
                AutopilotProposal.gap_source == "manual_memory_migration",
                AutopilotProposal.gap_description == DESCRIPTION,
                AutopilotProposal.state.in_(("proposed", "applied")),
            )
            .order_by(AutopilotProposal.id.desc())
        )
        if existing is not None:
            print(json.dumps({
                "proposal_id": existing.id,
                "state": existing.state,
                "reused": True,
            }))
            return

        proposal = AutopilotProposal(
            state="proposed",
            gap_source="manual_memory_migration",
            gap_description=DESCRIPTION,
            gap_evidence_json=json.dumps([{
                "source": "human_request",
                "session_id": "019f997c-1321-7860-b0b9-ba4e6a7613ea",
                "ledger_slug": "corvus-long-horizon",
                "ledger_revision": 5,
                "verification": (
                    "Seven active neurons contained stale roadmap-state "
                    "location guidance."
                ),
            }]),
            priority_score=1.0,
            llm_reasoning=(
                "Tyler explicitly requested that roadmap and ledger neurons "
                "stop directing agents to the retired location."
            ),
            llm_model="human-directed",
            eval_overall=100,
            eval_text=(
                "Exact active-neuron corpus audit identified the stale set "
                "before proposal creation."
            ),
        )
        db.add(proposal)
        await db.flush()

        item_count = 0
        for neuron_id, fields in UPDATES.items():
            neuron = await db.get(Neuron, neuron_id)
            assert neuron is not None, f"neuron {neuron_id} required"
            if neuron.superseded_by is not None:
                # The lifecycle gate correctly forbids rewriting an absorbed
                # fact. Complete the half-absorption instead so recall and
                # future compilers cannot keep surfacing its stale text.
                if neuron.is_active:
                    db.add(ProposalItem(
                        proposal_id=proposal.id,
                        action="update",
                        target_neuron_id=neuron.id,
                        field="is_active",
                        old_value="true",
                        new_value="false",
                        reason=(
                            "Complete existing supersession and retire stale "
                            "roadmap-location text from active recall."
                        ),
                    ))
                    item_count += 1
                continue
            assert neuron.is_active, f"active neuron {neuron_id} required"
            replacements = fields.get("content_replace", ())
            if replacements:
                old_content = neuron.content or ""
                new_content = old_content
                for old_fragment, new_fragment in replacements:
                    assert old_fragment in new_content, (
                        f"expected fragment missing from neuron {neuron_id}"
                    )
                    new_content = new_content.replace(old_fragment, new_fragment)
                fields = {**fields, "content": new_content}
            for field in ("label", "summary", "content"):
                if field not in fields:
                    continue
                old_value = getattr(neuron, field) or ""
                new_value = fields[field]
                if old_value == new_value:
                    continue
                db.add(ProposalItem(
                    proposal_id=proposal.id,
                    action="update",
                    target_neuron_id=neuron.id,
                    field=field,
                    old_value=old_value,
                    new_value=new_value,
                    reason=(
                        "Migrate canonical roadmap guidance to Corvus-Mind "
                        "after verified ledger cutover."
                    ),
                ))
                item_count += 1

        if item_count == 0:
            await db.rollback()
            print(json.dumps({"state": "noop", "neuron_ids": sorted(UPDATES)}))
            return
        await db.commit()
        print(json.dumps({
            "proposal_id": proposal.id,
            "state": proposal.state,
            "items": item_count,
            "neuron_ids": sorted(UPDATES),
        }))


if __name__ == "__main__":
    asyncio.run(main())
