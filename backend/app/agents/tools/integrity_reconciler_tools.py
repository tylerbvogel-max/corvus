"""Tools for the ``integrity_reconciler`` agent (Phase 4 #204).

Operates on ``contradiction`` IntegrityFinding rows produced by
``conflict_monitor.py``. Each finding references two neurons the scan
believes assert incompatible facts. The agent's job is to weigh
regulatory / recency / credibility signals and propose one of four
resolutions:

  - a_correct    : neuron A wins; B is the mistake
  - b_correct    : neuron B wins; A is the mistake
  - context_added: both can be correct in different contexts; add notes
  - dismissed    : the scan misfired; neurons aren't actually contradictory

Philosophy (same as dedup): LLM-backed pre-review proposes a resolution.
Proposals still flow through the human-approval gate
(AutopilotProposal.state = 'proposed' → 'approved' → 'applied'), so the
agent never directly mutates neurons. Blast radius bounded to the queue.

Every tool requires a `rationale` string in its input — enforces the
acceptance criterion "every action row has non-empty rationale in
input_json."

See `docs/design/aip-phase-4-node-204-integrity-reconciler.md`.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tool_base import register_tool
from app.models import IntegrityFinding, Neuron


# Every tool's input_schema shares this rationale requirement. Extracted
# as a module constant so the schemas stay DRY and the minLength is
# consistent across tools.
_RATIONALE_SCHEMA: dict[str, Any] = {
    "type": "string",
    "minLength": 20,
    "maxLength": 500,
    "description": "One-sentence explanation of why this tool is being called.",
}

_VALID_RESOLUTIONS = ("a_correct", "b_correct", "context_added")


def _iso_or_none(dt: Any) -> str | None:
    return dt.isoformat() if dt is not None else None


def _neuron_dict(n: Neuron) -> dict[str, Any]:
    """Surface the regulatory / recency / credibility signals the agent weighs."""
    assert n is not None, "neuron must be loaded"
    return {
        "id": n.id,
        "label": n.label,
        "department": n.department,
        "role_key": n.role_key,
        "layer": n.layer,
        "summary": n.summary,
        "content": (n.content or "")[:2000],
        # Regulatory signal — authority_level is advisory, 0-N scale set by the tenant.
        "authority_level": getattr(n, "authority_level", None),
        "source_origin": n.source_origin,
        # Recency signals — last_verified is the manual recency stamp; last_accessed_at
        # reflects firing activity; created_at is the fallback birth stamp.
        "created_at": _iso_or_none(getattr(n, "created_at", None)),
        "last_verified": _iso_or_none(getattr(n, "last_verified", None)),
        "last_accessed_at": _iso_or_none(getattr(n, "last_accessed_at", None)),
        # Credibility signals — how often it fires, and how well it scores when it does.
        "invocations": getattr(n, "invocations", 0),
        "avg_utility": float(getattr(n, "avg_utility", 0.0) or 0.0),
        "is_active": getattr(n, "is_active", True),
    }


# ── Read tools ──────────────────────────────────────────────────────────


@register_tool(
    "list_pending_contradictions",
    description=(
        "List open contradiction IntegrityFinding rows awaiting review, "
        "ordered by priority_score desc. Input: "
        "{\"limit\": int (default 5, max 20), \"rationale\": str}. "
        "Returns: {\"findings\": [{\"id\", \"severity\", \"neuron_ids\", "
        "\"description\", \"priority_score\"}], \"count\": int}."
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
async def list_pending_contradictions(
    session: AsyncSession, inp: dict[str, Any],
) -> dict[str, Any]:
    assert isinstance(inp.get("rationale"), str), "rationale is required"
    limit = int(inp.get("limit", 5))
    assert 1 <= limit <= 20, "limit must be 1..20"
    stmt = (
        select(IntegrityFinding)
        .where(and_(
            IntegrityFinding.finding_type == "contradiction",
            IntegrityFinding.status == "open",
        ))
        .order_by(
            IntegrityFinding.priority_score.desc(),
            IntegrityFinding.id.asc(),
        )
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    findings: list[dict[str, Any]] = []
    for f in rows:
        neuron_ids = json.loads(f.neuron_ids_json) if f.neuron_ids_json else []
        findings.append({
            "id": f.id,
            "severity": f.severity,
            "neuron_ids": neuron_ids[:10],
            "description": (f.description or "")[:500],
            "priority_score": float(f.priority_score or 0.0),
        })
    return {"findings": findings, "count": len(findings)}


@register_tool(
    "get_contradiction_detail",
    description=(
        "Get both neurons involved in a contradiction finding, with the "
        "regulatory (authority_level, source_origin), recency (updated_at) "
        "and credibility (invocations, avg_utility) signals the agent "
        "uses to decide which side wins. "
        "Input: {\"finding_id\": int, \"rationale\": str}. "
        "Returns: {\"finding_id\", \"neurons\": [<a>, <b>], \"severity\", "
        "\"description\"}."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "finding_id": {"type": "integer"},
            "rationale": _RATIONALE_SCHEMA,
        },
        "required": ["finding_id", "rationale"],
        "additionalProperties": False,
    },
    is_mutating=False,
)
async def get_contradiction_detail(
    session: AsyncSession, inp: dict[str, Any],
) -> dict[str, Any]:
    assert isinstance(inp.get("rationale"), str), "rationale is required"
    finding_id = int(inp["finding_id"])
    finding = await session.get(IntegrityFinding, finding_id)
    if finding is None:
        raise KeyError(f"IntegrityFinding {finding_id} not found")
    if finding.finding_type != "contradiction":
        raise ValueError(
            f"Finding {finding_id} is of type {finding.finding_type!r}, not contradiction"
        )

    neuron_ids = json.loads(finding.neuron_ids_json) if finding.neuron_ids_json else []
    assert len(neuron_ids) >= 2, (
        f"contradiction finding {finding_id} must reference 2+ neurons"
    )

    neurons_out: list[dict[str, Any]] = []
    for nid in neuron_ids[:2]:
        n = await session.get(Neuron, nid)
        if n is None:
            continue
        neurons_out.append(_neuron_dict(n))
    return {
        "finding_id": finding.id,
        "neurons": neurons_out,
        "severity": finding.severity,
        "description": finding.description,
    }


# ── Mutating tools ──────────────────────────────────────────────────────


@register_tool(
    "propose_contradiction_resolution",
    description=(
        "Record the agent's verdict on a contradiction. Creates an "
        "AutopilotProposal with the chosen resolution; the proposal then "
        "flows through the standard human-approval workflow (agent does "
        "NOT directly mutate). resolution ∈ {a_correct, b_correct, "
        "context_added}. Use context_added if uncertain between a_correct "
        "and b_correct. "
        "Input: {\"finding_id\": int, \"resolution\": str, \"notes\": str, "
        "\"rationale\": str}. "
        "Returns: {\"finding_id\", \"proposal_id\", \"state\": \"proposed\", "
        "\"resolution\"}."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "finding_id": {"type": "integer"},
            "resolution": {"type": "string", "enum": list(_VALID_RESOLUTIONS)},
            "notes": {"type": "string", "maxLength": 2000},
            "rationale": _RATIONALE_SCHEMA,
        },
        "required": ["finding_id", "resolution", "notes", "rationale"],
        "additionalProperties": False,
    },
    is_mutating=True,
)
async def propose_contradiction_resolution(
    session: AsyncSession, inp: dict[str, Any],
) -> dict[str, Any]:
    assert isinstance(inp.get("rationale"), str), "rationale is required"
    finding_id = int(inp["finding_id"])
    resolution = str(inp["resolution"])
    notes = str(inp["notes"])[:2000]
    assert resolution in _VALID_RESOLUTIONS, (
        f"resolution must be one of {_VALID_RESOLUTIONS}, got {resolution!r}"
    )

    # Defensive: verify the finding is actually a contradiction before
    # we mint a proposal. create_integrity_proposal itself loads the
    # finding but won't reject by finding_type.
    finding = await session.get(IntegrityFinding, finding_id)
    if finding is None:
        raise KeyError(f"IntegrityFinding {finding_id} not found")
    if finding.finding_type != "contradiction":
        raise ValueError(
            f"Finding {finding_id} is of type {finding.finding_type!r}, "
            "not contradiction"
        )

    from app.services.integrity.proposals import create_integrity_proposal
    proposal = await create_integrity_proposal(
        db=session,
        finding_id=finding_id,
        resolution=resolution,
        reviewer="agent:integrity_reconciler",
        notes=notes,
    )
    return {
        "finding_id": finding_id,
        "proposal_id": proposal.id,
        "state": proposal.state,
        "resolution": resolution,
    }


@register_tool(
    "dismiss_contradiction",
    description=(
        "Close a contradiction finding without creating a proposal. Use "
        "ONLY when the scan misfired — the two neurons are about different "
        "topics and only superficially look like a contradiction. Records "
        "resolution='dismissed' and status='resolved'. "
        "Input: {\"finding_id\": int, \"notes\": str, \"rationale\": str}. "
        "Returns: {\"finding_id\", \"status\": \"resolved\", "
        "\"resolution\": \"dismissed\"}."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "finding_id": {"type": "integer"},
            "notes": {"type": "string", "maxLength": 2000},
            "rationale": _RATIONALE_SCHEMA,
        },
        "required": ["finding_id", "notes", "rationale"],
        "additionalProperties": False,
    },
    is_mutating=True,
)
async def dismiss_contradiction(
    session: AsyncSession, inp: dict[str, Any],
) -> dict[str, Any]:
    assert isinstance(inp.get("rationale"), str), "rationale is required"
    finding_id = int(inp["finding_id"])
    notes = str(inp["notes"])[:2000]

    finding = await session.get(IntegrityFinding, finding_id)
    if finding is None:
        raise KeyError(f"IntegrityFinding {finding_id} not found")
    if finding.finding_type != "contradiction":
        raise ValueError(
            f"Finding {finding_id} is of type {finding.finding_type!r}, "
            "not contradiction"
        )
    if finding.status not in ("open", "proposed"):
        raise ValueError(
            f"Finding {finding_id} in status {finding.status!r} — cannot close"
        )

    finding.status = "resolved"
    finding.resolution = "dismissed"
    finding.resolved_by = "agent:integrity_reconciler"
    finding.resolved_at = datetime.utcnow()
    # Surface the agent's notes on the finding for future auditors.
    existing = (finding.description or "").rstrip()
    suffix = f"\n\n[agent:integrity_reconciler dismissed] {notes}"
    finding.description = (existing + suffix)[:4000]
    await session.flush()
    return {
        "finding_id": finding_id,
        "status": "resolved",
        "resolution": "dismissed",
    }
