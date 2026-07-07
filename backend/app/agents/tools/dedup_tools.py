"""Tools for the ``dedup`` agent (Pattern #202 A-Dedup).

Operates on ``near_duplicate`` IntegrityFinding rows produced by
``pattern_separation.py``. Each finding references two neurons the
scan thinks might be duplicates. The agent's job is to classify each
finding as:

  - merged: the two neurons are the same thing; call
    ``create_integrity_proposal(resolution="merged")`` so the standard
    human-approved merge workflow runs.
  - differentiated: the two are distinct; surface context notes and
    mark the finding resolved.
  - dismissed: the scan was wrong (e.g., two separate standards that
    happen to share vocabulary); just close the finding with notes.

Philosophy: the dedup agent is an LLM-backed pre-review that proposes a
resolution. Proposals still flow through the existing human-approval gate
(AutopilotProposal.state = 'proposed' → 'approved' → 'applied'), so the
agent never directly mutates neurons. The blast radius is bounded to the
proposal queue itself.

Tools follow a hybrid gate: embedding-cosine is cheap and deterministic
(0.95+ is almost certainly a duplicate; <0.75 almost certainly distinct),
and the semantic LLM compare handles the 0.75–0.95 borderline.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.tool_base import register_tool
from app.models import IntegrityFinding, Neuron
from app.services.embedding_service import cosine_similarity
from app.services.llm_provider import llm_chat


# ── Read tools ──


@register_tool(
    "list_pending_duplicates",
    description=(
        "List open near_duplicate IntegrityFinding rows awaiting review. "
        "Input: {\"limit\": int (default 20)}. "
        "Returns: {\"findings\": [{\"id\": int, \"severity\": str, \"neuron_ids\": [int], \"description\": str}, ...]}"
    ),
    input_schema={
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}},
        "additionalProperties": False,
    },
    is_mutating=False,
)
async def list_pending_duplicates(session: AsyncSession, inp: dict[str, Any]) -> dict[str, Any]:
    limit = int(inp.get("limit", 20))
    assert 1 <= limit <= 100, "limit must be 1..100"
    stmt = (
        select(IntegrityFinding)
        .where(and_(
            IntegrityFinding.finding_type == "near_duplicate",
            IntegrityFinding.status == "open",
        ))
        .order_by(IntegrityFinding.priority_score.desc(), IntegrityFinding.id.asc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    findings = []
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
    "get_finding_detail",
    description=(
        "Get the full detail of one near_duplicate finding — both neurons' "
        "label, content, summary, department, role_key, source_origin. "
        "Input: {\"finding_id\": int}. "
        "Returns: {\"finding_id\": int, \"neurons\": [<neuron_dict>, ...], "
        "\"severity\": str, \"description\": str} — neurons may contain "
        "fewer than 2 entries if a referenced neuron was deleted."
    ),
    input_schema={
        "type": "object",
        "properties": {"finding_id": {"type": "integer"}},
        "required": ["finding_id"],
        "additionalProperties": False,
    },
    is_mutating=False,
)
async def get_finding_detail(session: AsyncSession, inp: dict[str, Any]) -> dict[str, Any]:
    finding_id = int(inp["finding_id"])
    finding = await session.get(IntegrityFinding, finding_id)
    if finding is None:
        raise KeyError(f"IntegrityFinding {finding_id} not found")
    if finding.finding_type != "near_duplicate":
        raise ValueError(
            f"Finding {finding_id} is of type {finding.finding_type!r}, not near_duplicate"
        )

    neuron_ids = json.loads(finding.neuron_ids_json) if finding.neuron_ids_json else []
    assert len(neuron_ids) >= 2, f"near_duplicate finding {finding_id} must reference 2+ neurons"

    neurons_out = []
    for nid in neuron_ids[:2]:
        n = await session.get(Neuron, nid)
        if n is None:
            continue
        neurons_out.append({
            "id": n.id,
            "label": n.label,
            "content": (n.content or "")[:2000],
            "summary": n.summary,
            "department": n.department,
            "role_key": n.role_key,
            "layer": n.layer,
            "source_origin": n.source_origin,
            "authority_level": n.authority_level,
            "invocations": n.invocations,
        })
    return {
        "finding_id": finding.id,
        "neurons": neurons_out,
        "severity": finding.severity,
        "description": finding.description,
    }


@register_tool(
    "compute_embedding_similarity",
    description=(
        "Compute cosine similarity between two neurons' stored embeddings. "
        "Cheap, deterministic — use as first-pass gate. "
        "Interpret: >=0.95 almost-certainly duplicate, <=0.75 almost-certainly distinct, "
        "0.75..0.95 borderline (use compare_neurons_semantic). "
        "Input: {\"neuron_a_id\": int, \"neuron_b_id\": int}. "
        "Returns: {\"similarity\": float, \"decision_zone\": str}"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "neuron_a_id": {"type": "integer"},
            "neuron_b_id": {"type": "integer"},
        },
        "required": ["neuron_a_id", "neuron_b_id"],
        "additionalProperties": False,
    },
    is_mutating=False,
)
async def compute_embedding_similarity(session: AsyncSession, inp: dict[str, Any]) -> dict[str, Any]:
    a_id = int(inp["neuron_a_id"])
    b_id = int(inp["neuron_b_id"])
    n_a = await session.get(Neuron, a_id)
    n_b = await session.get(Neuron, b_id)
    if n_a is None or n_b is None:
        raise KeyError(f"Neuron(s) not found: a={a_id}, b={b_id}")
    if not n_a.embedding or not n_b.embedding:
        return {
            "similarity": None,
            "decision_zone": "unavailable",
            "reason": "one or both neurons have no stored embedding",
        }
    vec_a = json.loads(n_a.embedding)
    vec_b = json.loads(n_b.embedding)
    sim = float(cosine_similarity(vec_a, vec_b))
    if sim >= 0.95:
        zone = "high_confidence_duplicate"
    elif sim <= 0.75:
        zone = "high_confidence_distinct"
    else:
        zone = "borderline"
    return {"similarity": sim, "decision_zone": zone}


@register_tool(
    "compare_neurons_semantic",
    description=(
        "LLM-backed semantic comparison of two neurons. Use only for borderline "
        "cases (0.75..0.95 embedding similarity) to avoid cost. "
        "Returns a classification: duplicate | distinct | needs_human, with rationale. "
        "Input: {\"neuron_a_id\": int, \"neuron_b_id\": int}. "
        "Returns: {\"classification\": str, \"rationale\": str, \"cost_usd\": float}"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "neuron_a_id": {"type": "integer"},
            "neuron_b_id": {"type": "integer"},
        },
        "required": ["neuron_a_id", "neuron_b_id"],
        "additionalProperties": False,
    },
    is_mutating=False,
)
async def compare_neurons_semantic(session: AsyncSession, inp: dict[str, Any]) -> dict[str, Any]:
    a_id = int(inp["neuron_a_id"])
    b_id = int(inp["neuron_b_id"])
    n_a = await session.get(Neuron, a_id)
    n_b = await session.get(Neuron, b_id)
    if n_a is None or n_b is None:
        raise KeyError(f"Neuron(s) not found: a={a_id}, b={b_id}")

    prompt = (
        "You are a classifier. Given two knowledge-graph nodes, decide whether they "
        "represent the same concept, distinct concepts, or a case needing human "
        "judgment. Reply with exactly one JSON object:\n"
        '{"classification": "duplicate"|"distinct"|"needs_human", "rationale": "..."}\n\n'
        f"NODE A (#{n_a.id}):\n"
        f"  label: {n_a.label}\n  department: {n_a.department}\n"
        f"  summary: {n_a.summary or ''}\n  content: {(n_a.content or '')[:1500]}\n\n"
        f"NODE B (#{n_b.id}):\n"
        f"  label: {n_b.label}\n  department: {n_b.department}\n"
        f"  summary: {n_b.summary or ''}\n  content: {(n_b.content or '')[:1500]}\n"
    )
    # Opus for graph-mutation-adjacent judgment (quality-first policy for
    # backend maintenance — cheap models are for end-user query paths).
    result = await llm_chat(
        system_prompt="You classify knowledge-graph duplicates. Reply only with JSON.",
        user_message=prompt,
        max_tokens=400,
        model="opus",
    )
    text = str(result.get("text", "")).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        import re
        m = re.search(r"\{.*\}", text, re.DOTALL)
        parsed = json.loads(m.group(0)) if m else {"classification": "needs_human", "rationale": text[:300]}

    classification = parsed.get("classification", "needs_human")
    if classification not in ("duplicate", "distinct", "needs_human"):
        classification = "needs_human"
    return {
        "classification": classification,
        "rationale": str(parsed.get("rationale", ""))[:1000],
        "cost_usd": float(result.get("cost_usd", 0.0)),
    }


# ── Mutating tools ──


@register_tool(
    "mark_duplicate",
    description=(
        "Record the agent's verdict that two neurons are duplicates. Creates an "
        "AutopilotProposal with resolution='merged' which then flows through the "
        "standard human-approval workflow (agent does NOT directly merge). "
        "Input: {\"finding_id\": int, \"notes\": str}. "
        "Returns: {\"finding_id\": int, \"proposal_id\": int, \"state\": \"proposed\"}"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "finding_id": {"type": "integer"},
            "notes": {"type": "string", "maxLength": 2000},
        },
        "required": ["finding_id", "notes"],
        "additionalProperties": False,
    },
    is_mutating=True,
)
async def mark_duplicate(session: AsyncSession, inp: dict[str, Any]) -> dict[str, Any]:
    from app.services.integrity.proposals import create_integrity_proposal

    finding_id = int(inp["finding_id"])
    notes = str(inp["notes"])[:2000]
    proposal = await create_integrity_proposal(
        db=session,
        finding_id=finding_id,
        resolution="merged",
        reviewer="agent:dedup",
        notes=notes,
    )
    return {
        "finding_id": finding_id,
        "proposal_id": proposal.id,
        "state": proposal.state,
    }


@register_tool(
    "mark_reviewed_as_unique",
    description=(
        "Record the agent's verdict that two neurons are distinct. "
        "resolution='dismissed' closes the finding directly (scan misfire) "
        "and returns status='resolved'. resolution='differentiated' creates "
        "a differentiate proposal for human review and returns "
        "status='proposed' — the finding closes when the proposal is decided. "
        "Input: {\"finding_id\": int, \"resolution\": \"differentiated\"|\"dismissed\", \"notes\": str}. "
        "Returns: {\"finding_id\": int, \"status\": \"resolved\"|\"proposed\", \"resolution\": str}"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "finding_id": {"type": "integer"},
            "resolution": {"type": "string", "enum": ["differentiated", "dismissed"]},
            "notes": {"type": "string", "maxLength": 2000},
        },
        "required": ["finding_id", "resolution", "notes"],
        "additionalProperties": False,
    },
    is_mutating=True,
)
async def mark_reviewed_as_unique(session: AsyncSession, inp: dict[str, Any]) -> dict[str, Any]:
    finding_id = int(inp["finding_id"])
    resolution = str(inp["resolution"])
    notes = str(inp["notes"])[:2000]
    assert resolution in ("differentiated", "dismissed"), (
        f"resolution must be 'differentiated' or 'dismissed', got {resolution!r}"
    )

    finding = await session.get(IntegrityFinding, finding_id)
    if finding is None:
        raise KeyError(f"IntegrityFinding {finding_id} not found")
    if finding.status not in ("open", "proposed"):
        raise ValueError(f"Finding {finding_id} in status {finding.status!r} — cannot close")

    if resolution == "differentiated":
        # Differentiated findings go through the proposal workflow (adds context notes to neurons).
        from app.services.integrity.proposals import create_integrity_proposal
        proposal = await create_integrity_proposal(
            db=session,
            finding_id=finding_id,
            resolution="differentiated",
            reviewer="agent:dedup",
            notes=notes,
        )
        return {
            "finding_id": finding_id,
            "status": "proposed",
            "resolution": "differentiated",
            "proposal_id": proposal.id,
        }

    # Dismissed — close directly without a proposal.
    finding.status = "resolved"
    finding.resolution = "dismissed"
    finding.resolved_by = "agent:dedup"
    finding.resolved_at = datetime.utcnow()
    await session.flush()
    return {
        "finding_id": finding_id,
        "status": "resolved",
        "resolution": "dismissed",
    }
