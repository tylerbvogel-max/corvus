"""Tools for the ``document_ingest_reviewer`` agent (Phase 4 #205).

Operates on existing AutopilotProposal rows with
``gap_source='document_ingest'``, created by the Sonnet-based extractor
in ``document_extractor.py``. Each proposal represents one extracted
section from an uploaded document, to be filed as a new neuron under
the user-supplied department/role.

The agent's job is to review the extracted classification against the
content and either (a) refine it with a confidence score, or
(b) flag the proposal as genuinely uncertain so a human reviewer
disambiguates before approval.

Philosophy (same as #202 dedup, #204 integrity reconciler):
  - Agent operates entirely at the proposal-queue level. It cannot
    mutate neurons directly.
  - Agent's output sharpens the proposal; the state stays 'proposed'.
    Human approval downstream is what creates the neuron.
  - Rationale is required on every tool call (input_schema enforces
    min 20 chars) — the audit trail shows why every classification
    decision was made.

See ``docs/design/aip-phase-4-node-205-ingest-agent.md`` for the full
design.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tool_base import register_tool
from app.models import AutopilotProposal, Neuron, ProposalItem


_RATIONALE_SCHEMA: dict[str, Any] = {
    "type": "string",
    "minLength": 20,
    "maxLength": 500,
    "description": "One-sentence explanation of why this tool is being called.",
}

# String literal for the proposal source. Kept as module constants so the
# allow-list check and the filter queries can't drift apart.
_INGEST_SOURCE = "document_ingest"
_UNCERTAIN_SOURCE = "document_ingest/uncertain"


def _iso_or_none(dt: Any) -> str | None:
    return dt.isoformat() if dt is not None else None


# ── Read tools ──────────────────────────────────────────────────────────


@register_tool(
    "list_pending_ingest_proposals",
    description=(
        "List open AutopilotProposal rows from document ingest awaiting "
        "review (state='proposed', gap_source='document_ingest', not yet "
        "reviewed). Ordered by id ascending. "
        "Input: {\"limit\": int (default 5, max 20), \"rationale\": str}. "
        "Returns: {\"proposals\": [{\"id\", \"gap_description\", "
        "\"priority_score\", \"item_count\", \"created_at\"}], \"count\": int}."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            "rationale": _RATIONALE_SCHEMA,
        },
        "required": ["rationale"],
        "additionalProperties": False,
    },
    is_mutating=False,
)
async def list_pending_ingest_proposals(
    session: AsyncSession, inp: dict[str, Any],
) -> dict[str, Any]:
    assert isinstance(inp.get("rationale"), str), "rationale is required"
    limit = int(inp.get("limit", 5))
    assert 1 <= limit <= 20, "limit must be 1..20"
    stmt = (
        select(AutopilotProposal)
        .where(and_(
            AutopilotProposal.gap_source == _INGEST_SOURCE,
            AutopilotProposal.state == "proposed",
            AutopilotProposal.reviewed_at.is_(None),
        ))
        .order_by(AutopilotProposal.id.asc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    proposals: list[dict[str, Any]] = []
    for p in rows:
        items = p.items if getattr(p, "items", None) is not None else []
        proposals.append({
            "id": p.id,
            "gap_description": (p.gap_description or "")[:400],
            "priority_score": float(p.priority_score or 0.0),
            "item_count": len(items) if isinstance(items, list) else 0,
            "created_at": _iso_or_none(getattr(p, "created_at", None)),
        })
    return {"proposals": proposals, "count": len(proposals)}


@register_tool(
    "get_ingest_proposal_detail",
    description=(
        "Return full detail for one document-ingest AutopilotProposal: "
        "the pending neuron spec(s) the proposal would create, plus a "
        "sample of sibling neurons under the target parent so the "
        "ontology context is visible. "
        "Input: {\"proposal_id\": int, \"rationale\": str}. "
        "Returns: {\"proposal_id\", \"gap_description\", \"llm_reasoning\", "
        "\"items\": [{\"item_id\", \"action\", \"spec\"}], "
        "\"ontology_sample\": [{\"id\", \"label\", \"layer\", \"department\", \"role_key\"}]}."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "proposal_id": {"type": "integer"},
            "rationale": _RATIONALE_SCHEMA,
        },
        "required": ["proposal_id", "rationale"],
        "additionalProperties": False,
    },
    is_mutating=False,
)
async def get_ingest_proposal_detail(
    session: AsyncSession, inp: dict[str, Any],
) -> dict[str, Any]:
    assert isinstance(inp.get("rationale"), str), "rationale is required"
    proposal_id = int(inp["proposal_id"])
    proposal = await session.get(AutopilotProposal, proposal_id)
    if proposal is None:
        raise KeyError(f"AutopilotProposal {proposal_id} not found")
    if proposal.gap_source not in (_INGEST_SOURCE, _UNCERTAIN_SOURCE):
        raise ValueError(
            f"Proposal {proposal_id} has gap_source {proposal.gap_source!r}, "
            f"not document_ingest"
        )

    items_out: list[dict[str, Any]] = []
    parent_ids: set[int] = set()
    for it in (getattr(proposal, "items", None) or []):
        spec: dict[str, Any] = {}
        if it.neuron_spec_json:
            try:
                spec = json.loads(it.neuron_spec_json)
            except json.JSONDecodeError:
                spec = {"_parse_error": True, "raw": it.neuron_spec_json[:200]}
        if isinstance(spec, dict) and isinstance(spec.get("parent_id"), int):
            parent_ids.add(spec["parent_id"])
        items_out.append({
            "item_id": it.id,
            "action": it.action,
            "spec": spec,
        })

    # Pull a sibling sample under each referenced parent — bounded.
    ontology_sample: list[dict[str, Any]] = []
    for pid in list(parent_ids)[:3]:
        stmt = (
            select(Neuron)
            .where(and_(Neuron.parent_id == pid, Neuron.is_active.is_(True)))
            .limit(8)
        )
        for n in (await session.execute(stmt)).scalars().all():
            ontology_sample.append({
                "id": n.id,
                "label": n.label,
                "layer": n.layer,
                "department": n.department,
                "role_key": n.role_key,
                "summary": (n.summary or "")[:200],
            })

    return {
        "proposal_id": proposal.id,
        "gap_source": proposal.gap_source,
        "gap_description": proposal.gap_description,
        "llm_reasoning": proposal.llm_reasoning,
        "items": items_out,
        "ontology_sample": ontology_sample,
    }


# ── Mutating tools ──────────────────────────────────────────────────────


def _apply_item_updates(
    items_by_id: dict[int, ProposalItem],
    updates: list[dict[str, Any]],
) -> int:
    """Patch each listed ProposalItem's neuron_spec_json with the agent's
    chosen classification. Returns the count actually updated (skips items
    that are not 'create' actions or that aren't in the items_by_id map)."""
    assert isinstance(items_by_id, dict), "items_by_id must be dict"
    assert isinstance(updates, list), "updates must be list"
    updated = 0
    # JPL-2: bounded by input length, which is schema-capped at ~finite size.
    for u in updates:
        item = items_by_id.get(int(u["item_id"]))
        if item is None or item.action != "create":
            continue
        spec: dict[str, Any] = {}
        if item.neuron_spec_json:
            try:
                spec = json.loads(item.neuron_spec_json)
            except json.JSONDecodeError:
                spec = {}
        spec["layer"] = int(u["layer"])
        spec["department"] = str(u["department"])[:100]
        spec["role_key"] = str(u["role_key"])[:100]
        item.neuron_spec_json = json.dumps(spec)
        updated += 1
    return updated


def _append_evidence(
    existing_json: str | None, block: dict[str, Any],
) -> str:
    """Append a structured block onto the proposal's gap_evidence_json.

    Keeps prior evidence intact (the doc extractor writes its own).
    """
    assert isinstance(block, dict), "block must be dict"
    parsed: list[Any] = []
    if existing_json:
        try:
            decoded = json.loads(existing_json)
            if isinstance(decoded, list):
                parsed = decoded
            elif isinstance(decoded, dict):
                parsed = [decoded]
        except json.JSONDecodeError:
            parsed = []
    parsed.append(block)
    return json.dumps(parsed)


@register_tool(
    "refine_ingest_classification",
    description=(
        "Record the agent's reviewed classification for one ingest "
        "proposal. Updates each listed item's neuron_spec_json with the "
        "chosen layer/department/role_key. Marks the proposal reviewed "
        "with the agent's confidence (0..1). Proposal state STAYS "
        "'proposed' — a human still approves downstream. "
        "Input: {\"proposal_id\": int, \"item_updates\": "
        "[{\"item_id\": int, \"layer\": int, \"department\": str, "
        "\"role_key\": str}], \"confidence\": number (0..1), "
        "\"rationale\": str}. "
        "Returns: {\"proposal_id\", \"updated_items\": int, "
        "\"confidence\", \"state\": \"proposed\"}."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "proposal_id": {"type": "integer"},
            "item_updates": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "integer"},
                        "layer": {"type": "integer", "minimum": 0, "maximum": 5},
                        "department": {"type": "string", "maxLength": 100},
                        "role_key": {"type": "string", "maxLength": 100},
                    },
                    "required": ["item_id", "layer", "department", "role_key"],
                    "additionalProperties": False,
                },
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "rationale": _RATIONALE_SCHEMA,
        },
        "required": ["proposal_id", "item_updates", "confidence", "rationale"],
        "additionalProperties": False,
    },
    is_mutating=True,
)
async def refine_ingest_classification(
    session: AsyncSession, inp: dict[str, Any],
) -> dict[str, Any]:
    assert isinstance(inp.get("rationale"), str), "rationale is required"
    proposal_id = int(inp["proposal_id"])
    confidence = float(inp["confidence"])
    assert 0.0 <= confidence <= 1.0, "confidence must be 0..1"
    updates = inp["item_updates"]
    assert isinstance(updates, list) and len(updates) > 0, "item_updates must be non-empty"

    proposal = await session.get(AutopilotProposal, proposal_id)
    if proposal is None:
        raise KeyError(f"AutopilotProposal {proposal_id} not found")
    if proposal.gap_source != _INGEST_SOURCE:
        raise ValueError(
            f"Proposal {proposal_id} has gap_source {proposal.gap_source!r}, "
            f"not document_ingest — refine refuses to touch non-ingest proposals"
        )

    items_by_id = {it.id: it for it in (proposal.items or [])}
    updated = _apply_item_updates(items_by_id, updates)

    rationale = str(inp["rationale"])[:500]
    proposal.llm_reasoning = (
        (proposal.llm_reasoning or "") + f"\n\n[agent:document_ingest_reviewer] "
        f"confidence={confidence:.2f} — {rationale}"
    )[:4000]
    proposal.reviewed_by = "agent:document_ingest_reviewer"
    proposal.reviewed_at = datetime.utcnow()
    proposal.gap_evidence_json = _append_evidence(
        proposal.gap_evidence_json,
        {
            "signal": "agent_classification_refined",
            "agent_model": "haiku",
            "agent_confidence": round(confidence, 4),
            "reviewed_at": datetime.utcnow().isoformat(),
            "updated_item_count": updated,
        },
    )
    await session.flush()
    return {
        "proposal_id": proposal.id,
        "updated_items": updated,
        "confidence": round(confidence, 4),
        "state": proposal.state,
    }


@register_tool(
    "flag_ingest_uncertain",
    description=(
        "Mark an ingest proposal as genuinely uncertain — the agent "
        "could not pick a single classification. Sets gap_source to "
        "'document_ingest/uncertain' so the review UI shows it as a "
        "distinct bucket. Stores candidate placements + the agent's "
        "rationale for each. Proposal state STAYS 'proposed'. "
        "Input: {\"proposal_id\": int, \"candidates\": "
        "[{\"layer\": int, \"department\": str, \"role_key\": str, "
        "\"confidence\": number, \"rationale\": str}], "
        "\"rationale\": str}. "
        "Returns: {\"proposal_id\", \"gap_source\": "
        "\"document_ingest/uncertain\", \"candidate_count\"}."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "proposal_id": {"type": "integer"},
            "candidates": {
                "type": "array",
                "minItems": 2,
                "maxItems": 4,
                "items": {
                    "type": "object",
                    "properties": {
                        "layer": {"type": "integer", "minimum": 0, "maximum": 5},
                        "department": {"type": "string", "maxLength": 100},
                        "role_key": {"type": "string", "maxLength": 100},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "rationale": {"type": "string", "maxLength": 500},
                    },
                    "required": ["layer", "department", "role_key", "confidence", "rationale"],
                    "additionalProperties": False,
                },
            },
            "rationale": _RATIONALE_SCHEMA,
        },
        "required": ["proposal_id", "candidates", "rationale"],
        "additionalProperties": False,
    },
    is_mutating=True,
)
async def flag_ingest_uncertain(
    session: AsyncSession, inp: dict[str, Any],
) -> dict[str, Any]:
    assert isinstance(inp.get("rationale"), str), "rationale is required"
    proposal_id = int(inp["proposal_id"])
    candidates = inp["candidates"]
    assert isinstance(candidates, list) and 2 <= len(candidates) <= 4, (
        "candidates must be a list of 2..4 placements"
    )

    proposal = await session.get(AutopilotProposal, proposal_id)
    if proposal is None:
        raise KeyError(f"AutopilotProposal {proposal_id} not found")
    if proposal.gap_source != _INGEST_SOURCE:
        raise ValueError(
            f"Proposal {proposal_id} has gap_source {proposal.gap_source!r}, "
            f"not document_ingest — flag refuses to touch non-ingest proposals"
        )

    proposal.gap_source = _UNCERTAIN_SOURCE
    rationale = str(inp["rationale"])[:500]
    proposal.llm_reasoning = (
        (proposal.llm_reasoning or "") + f"\n\n[agent:document_ingest_reviewer] "
        f"FLAGGED UNCERTAIN — {rationale}"
    )[:4000]
    proposal.reviewed_by = "agent:document_ingest_reviewer"
    proposal.reviewed_at = datetime.utcnow()
    proposal.gap_evidence_json = _append_evidence(
        proposal.gap_evidence_json,
        {
            "signal": "agent_flagged_uncertain",
            "agent_model": "haiku",
            "uncertain": True,
            "candidates": [
                {
                    "layer": int(c["layer"]),
                    "department": str(c["department"])[:100],
                    "role_key": str(c["role_key"])[:100],
                    "confidence": round(float(c["confidence"]), 4),
                    "rationale": str(c["rationale"])[:500],
                }
                for c in candidates
            ],
            "flagged_at": datetime.utcnow().isoformat(),
        },
    )
    await session.flush()
    return {
        "proposal_id": proposal.id,
        "gap_source": _UNCERTAIN_SOURCE,
        "candidate_count": len(candidates),
    }
