"""Tools for the ``neuron_placer`` agent (two-phase document ingest — Phase 2).

Consumes ``AutopilotProposal`` rows written by Phase 1 (Opus extractor in
``document_extractor.py``). Phase 1 writes rows with
``state='artifact'`` and ``gap_source='document_ingest'`` — the neuron_spec
contains only content-side fields (content, summary, node_type, verbatim_quote)
with placement fields (parent_id, layer, department, role_key) left null.

This agent's job is primary placement: for each artifact, find the best
parent in the graph, commit a layer/department/role_key, and promote the
row from ``state='artifact'`` to ``state='proposed'`` so it enters the
normal human-approval queue.

Philosophy:
  - Agent cannot mutate neurons directly — it only writes proposal rows.
  - Humans still approve downstream. The agent's job is to take artifacts
    that have no placement and give them one that a human can quickly
    review.
  - Rationale is required on every tool call (audit trail for every
    placement decision).

Renamed from ``document_ingest_reviewer`` on 2026-04-23 to reflect the
broader primary-placement role. See the plan file in
``~/.claude/plans/now-back-to-the-reactive-cocke.md`` for the full design.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
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
        "List un-placed artifact rows awaiting graph placement "
        "(state='artifact', gap_source='document_ingest'). These are the "
        "outputs of Phase 1 (doc extraction) that need this agent to "
        "decide their parent + layer + department + role_key. "
        "Ordered by id ascending. "
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
            AutopilotProposal.state == "artifact",
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
    chosen placement (parent_id, layer, department, role_key). Returns the
    count actually updated (skips items that are not 'create' actions or
    that aren't in the items_by_id map).
    """
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
        spec["parent_id"] = int(u["parent_id"])
        spec["layer"] = int(u["layer"])
        spec["department"] = str(u["department"])[:100]
        spec["role_key"] = str(u["role_key"])[:100]
        item.neuron_spec_json = json.dumps(spec)
        # Mirror placement on the ProposalItem for the apply path:
        item.target_neuron_id = int(u["parent_id"])
        # Carry the agent's per-item rationale into ProposalItem.reason.
        rationale = u.get("rationale")
        if isinstance(rationale, str) and rationale.strip():
            prefix = f"[neuron_placer] {rationale.strip()[:400]}"
            item.reason = (
                prefix if not item.reason else f"{item.reason}\n{prefix}"
            )[:2000]
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
        "Commit the agent's graph placement for one artifact proposal. "
        "Updates each listed item's neuron_spec_json with the chosen "
        "parent_id/layer/department/role_key, and promotes the proposal "
        "from state='artifact' to state='proposed' so it enters the human "
        "review queue. A per-item rationale is recorded on ProposalItem.reason "
        "so human reviewers see WHY each placement was chosen. "
        "Input: {\"proposal_id\": int, \"item_updates\": "
        "[{\"item_id\": int, \"parent_id\": int, \"layer\": int, "
        "\"department\": str, \"role_key\": str, \"rationale\": str}], "
        "\"confidence\": number (0..1), \"rationale\": str}. "
        "Returns: {\"proposal_id\", \"updated_items\": int, "
        "\"confidence\", \"state\", \"promoted\": bool}."
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
                        "parent_id": {"type": "integer", "minimum": 1},
                        "layer": {"type": "integer", "minimum": 0, "maximum": 5},
                        "department": {"type": "string", "maxLength": 100},
                        "role_key": {"type": "string", "maxLength": 100},
                        "rationale": {"type": "string", "minLength": 10, "maxLength": 400},
                    },
                    "required": [
                        "item_id", "parent_id", "layer",
                        "department", "role_key", "rationale",
                    ],
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

    # Layer 1 — fail-closed validation of every placement field against reality.
    # Catches "agent made up a parent_id" and "agent invented a department name".
    await _validate_placement_updates(session, updates)

    items_by_id = {it.id: it for it in (proposal.items or [])}
    updated = _apply_item_updates(items_by_id, updates)

    rationale = str(inp["rationale"])[:500]
    proposal.llm_reasoning = (
        (proposal.llm_reasoning or "") + f"\n\n[agent:neuron_placer] "
        f"confidence={confidence:.2f} — {rationale}"
    )[:4000]
    proposal.reviewed_by = "agent:neuron_placer"
    proposal.reviewed_at = datetime.utcnow()

    promoted = False
    if proposal.state == "artifact":
        proposal.state = "proposed"
        promoted = True

    proposal.gap_evidence_json = _append_evidence(
        proposal.gap_evidence_json,
        {
            "signal": "agent_placement_committed",
            "agent_model": "sonnet",
            "agent_confidence": round(confidence, 4),
            "reviewed_at": datetime.utcnow().isoformat(),
            "updated_item_count": updated,
            "promoted_from_artifact": promoted,
        },
    )
    await session.flush()
    return {
        "proposal_id": proposal.id,
        "updated_items": updated,
        "confidence": round(confidence, 4),
        "state": proposal.state,
        "promoted": promoted,
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
        (proposal.llm_reasoning or "") + f"\n\n[agent:neuron_placer] "
        f"FLAGGED UNCERTAIN — {rationale}"
    )[:4000]
    proposal.reviewed_by = "agent:neuron_placer"
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


# ── Discovery tools ─────────────────────────────────────────────────────


@register_tool(
    "search_graph_parents",
    description=(
        "Search the neuron graph for candidate parent neurons an artifact "
        "could land under. Filters by a case-insensitive LIKE on neuron "
        "label and/or exact match on department / role_key / layer. "
        "Only returns active neurons. Ordered by layer ascending then "
        "invocations descending so higher-authority candidates surface "
        "first. Bounded by max_results (capped at 20). "
        "Input: {\"label_pattern\": str (optional, LIKE pattern e.g. 'heat%treatment'), "
        "\"department\": str (optional), \"role_key\": str (optional), "
        "\"max_layer\": int (optional, 0..5), "
        "\"max_results\": int (default 8, max 20), \"rationale\": str}. "
        "Returns: {\"candidates\": [{\"id\", \"label\", \"layer\", "
        "\"department\", \"role_key\", \"summary\", \"sibling_count\"}], "
        "\"count\": int}."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "label_pattern": {"type": "string", "minLength": 1, "maxLength": 200},
            "department": {"type": "string", "maxLength": 100},
            "role_key": {"type": "string", "maxLength": 100},
            "max_layer": {"type": "integer", "minimum": 0, "maximum": 5},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
            "rationale": _RATIONALE_SCHEMA,
        },
        "required": ["rationale"],
        "additionalProperties": False,
    },
    is_mutating=False,
)
async def search_graph_parents(
    session: AsyncSession, inp: dict[str, Any],
) -> dict[str, Any]:
    assert isinstance(inp.get("rationale"), str), "rationale is required"
    max_results = int(inp.get("max_results", 8))
    assert 1 <= max_results <= 20, "max_results must be 1..20"

    clauses = [Neuron.is_active.is_(True)]
    pattern = inp.get("label_pattern")
    if isinstance(pattern, str) and pattern.strip():
        like = pattern.strip()
        clauses.append(or_(
            Neuron.label.ilike(f"%{like}%"),
            Neuron.summary.ilike(f"%{like}%"),
        ))
    dept = inp.get("department")
    if isinstance(dept, str) and dept.strip():
        clauses.append(Neuron.department == dept.strip())
    role = inp.get("role_key")
    if isinstance(role, str) and role.strip():
        clauses.append(Neuron.role_key == role.strip())
    max_layer = inp.get("max_layer")
    if isinstance(max_layer, int):
        clauses.append(Neuron.layer <= int(max_layer))

    stmt = (
        select(Neuron)
        .where(and_(*clauses))
        .order_by(Neuron.layer.asc(), Neuron.invocations.desc().nullslast())
        .limit(max_results)
    )
    parents = (await session.execute(stmt)).scalars().all()

    candidates: list[dict[str, Any]] = []
    # JPL-2: bounded by max_results (≤20).
    for p in parents:
        sib_stmt = (
            select(func.count(Neuron.id))
            .where(and_(Neuron.parent_id == p.id, Neuron.is_active.is_(True)))
        )
        sibling_count = int((await session.execute(sib_stmt)).scalar() or 0)
        candidates.append({
            "id": p.id,
            "label": p.label,
            "layer": p.layer,
            "department": p.department,
            "role_key": p.role_key,
            "summary": (p.summary or "")[:200],
            "sibling_count": sibling_count,
        })
    return {"candidates": candidates, "count": len(candidates)}


# ── Layer 1 validation + Layer 2 read-back ──────────────────────────────


# node_type → allowed graph layers. Soft compatibility guide — not an
# absolute rule (layers 2-5 are the content layers). A metric landing at
# layer 0 or 1 is clearly wrong; a knowledge item can live at 3 or 4.
_NODE_TYPE_LAYER_COMPAT: dict[str, frozenset[int]] = {
    "knowledge": frozenset({2, 3, 4, 5}),
    "process":   frozenset({2, 3, 4}),
    "standard":  frozenset({3, 4}),
    "decision":  frozenset({3, 4}),
    "metric":    frozenset({4, 5}),
    "output":    frozenset({5}),
}


async def _validate_placement_updates(
    session: AsyncSession, updates: list[dict[str, Any]],
) -> None:
    """Layer 1 guardrail: every placement update must reference reality.

    Raises ValueError on the first invalid field, with a message the agent
    can use to retry. Bounded by len(updates) which is schema-capped.
    """
    # Collect all referenced parent_ids + (dept, role) pairs for batched check.
    parent_ids = {int(u["parent_id"]) for u in updates if "parent_id" in u}
    dept_role_pairs = {
        (str(u.get("department", "")), str(u.get("role_key", "")))
        for u in updates
    }
    valid_parents = await _fetch_valid_parents(session, parent_ids)
    known_depts, known_roles_by_dept = await _fetch_known_dept_roles(session)

    for u in updates:
        pid = int(u.get("parent_id") or 0)
        if pid <= 0 or pid not in valid_parents:
            raise ValueError(
                f"Unknown or inactive parent_id={pid}. "
                f"Use search_graph_parents to find a real candidate."
            )
        dept = str(u.get("department", "")).strip()
        if dept and dept not in known_depts:
            raise ValueError(
                f"Unknown department={dept!r}. Known departments: "
                f"{sorted(known_depts)[:20]}"
            )
        role = str(u.get("role_key", "")).strip()
        if dept and role and role not in known_roles_by_dept.get(dept, set()):
            raise ValueError(
                f"role_key={role!r} not found under department={dept!r}. "
                f"Known roles there: "
                f"{sorted(known_roles_by_dept.get(dept, set()))[:10]}"
            )
        layer = int(u.get("layer", -1))
        node_type = str(valid_parents.get(pid, {}).get("node_type", "knowledge"))
        _validate_layer_for_node_type(layer, node_type)


async def _fetch_valid_parents(
    session: AsyncSession, parent_ids: set[int],
) -> dict[int, dict[str, Any]]:
    """Return {id: {layer, node_type, department}} for active neurons."""
    if not parent_ids:
        return {}
    stmt = select(Neuron).where(
        and_(Neuron.id.in_(parent_ids), Neuron.is_active.is_(True))
    )
    rows = (await session.execute(stmt)).scalars().all()
    return {
        n.id: {
            "layer": n.layer,
            "node_type": n.node_type,
            "department": n.department,
        }
        for n in rows
    }


async def _fetch_known_dept_roles(
    session: AsyncSession,
) -> tuple[set[str], dict[str, set[str]]]:
    """Return (departments, {dept -> role_keys}) from active neurons."""
    stmt = select(Neuron.department, Neuron.role_key).where(Neuron.is_active.is_(True))
    rows = (await session.execute(stmt)).all()
    depts: set[str] = set()
    roles_by_dept: dict[str, set[str]] = {}
    for dept, role in rows:
        if dept:
            depts.add(dept)
            if role:
                roles_by_dept.setdefault(dept, set()).add(role)
    return depts, roles_by_dept


def _validate_layer_for_node_type(layer: int, node_type: str) -> None:
    """Soft compatibility check between layer and node_type."""
    compat = _NODE_TYPE_LAYER_COMPAT.get(node_type)
    if compat is None:
        return  # unknown node_type — skip check, let downstream validate
    if layer not in compat:
        raise ValueError(
            f"layer={layer} is not compatible with node_type={node_type!r}. "
            f"Allowed layers: {sorted(compat)}"
        )


@register_tool(
    "get_placement_status",
    description=(
        "Read-back tool for Layer 2 verification. Returns the CURRENT "
        "persisted state of a document-ingest proposal: its state value, "
        "the committed parent/layer/department/role_key (null before "
        "placement), and reviewer metadata. The neuron_placer agent MUST "
        "call this after refine_ingest_classification or "
        "flag_ingest_uncertain so its done-envelope reflects observed DB "
        "reality, not the model's self-report. "
        "Input: {\"proposal_id\": int, \"rationale\": str}. "
        "Returns: {\"proposal_id\", \"state\", \"gap_source\", "
        "\"placement\": {\"parent_id\", \"parent_label\", \"layer\", "
        "\"department\", \"role_key\"} | null, \"reviewed_by\", "
        "\"reviewed_at\"}."
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
async def get_placement_status(
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
            f"not a document-ingest proposal"
        )

    placement: dict[str, Any] | None = None
    items = getattr(proposal, "items", None) or []
    if items:
        first = items[0]
        if first.neuron_spec_json:
            try:
                spec = json.loads(first.neuron_spec_json)
            except json.JSONDecodeError:
                spec = {}
            parent_id = spec.get("parent_id")
            parent_label = None
            if isinstance(parent_id, int):
                parent = await session.get(Neuron, parent_id)
                parent_label = parent.label if parent is not None else None
            if any(spec.get(k) is not None for k in ("parent_id", "layer", "department", "role_key")):
                placement = {
                    "parent_id": parent_id,
                    "parent_label": parent_label,
                    "layer": spec.get("layer"),
                    "department": spec.get("department"),
                    "role_key": spec.get("role_key"),
                }

    return {
        "proposal_id": proposal.id,
        "state": proposal.state,
        "gap_source": proposal.gap_source,
        "placement": placement,
        "reviewed_by": proposal.reviewed_by,
        "reviewed_at": _iso_or_none(proposal.reviewed_at),
    }
