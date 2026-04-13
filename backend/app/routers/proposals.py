"""Proposals router — CRUD, approve/reject, apply, provenance chain.

Staged autopilot proposals with full audit trail. Nothing modifies the
neuron graph until a proposal is explicitly approved and applied.
"""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session
from app.models import (
    AutopilotProposal, ProposalItem, Neuron, NeuronRefinement,
    NeuronSourceLink, EmergentQueue, Query,
)
from app.schemas import (
    ProposalOut, ProposalDetailOut, ProposalItemOut,
    GapEvidenceOut, DocumentEvidenceOut,
    ProposalReviewRequest, ProposalApplyRequest, ProposalStatsOut,
)
from app.services import action_bus
from app.middleware.rbac import UserIdentity, resolve_identity

router = APIRouter(prefix="/admin/proposals", tags=["proposals"])


def _classify_origin(p: AutopilotProposal) -> str:
    """Classify proposal origin for UI filter pills.

    Precedence: autopilot_run_id link > integrity_* prefix > document_ingest > manual.
    """
    if p.autopilot_run_id is not None:
        return "autopilot"
    src = p.gap_source or ""
    if src.startswith("integrity_"):
        return "integrity"
    if src == "document_ingest":
        return "document"
    return "manual"


def _proposal_summary(p: AutopilotProposal) -> ProposalOut:
    """Convert proposal model to summary schema."""
    origin = _classify_origin(p)
    return ProposalOut(
        id=p.id,
        autopilot_run_id=p.autopilot_run_id,
        query_id=p.query_id,
        state=p.state,
        gap_source=p.gap_source,
        gap_description=p.gap_description,
        priority_score=p.priority_score,
        llm_model=p.llm_model,
        eval_overall=p.eval_overall,
        reviewed_by=p.reviewed_by,
        reviewed_at=p.reviewed_at.isoformat() if p.reviewed_at else None,
        applied_at=p.applied_at.isoformat() if p.applied_at else None,
        applied_by=p.applied_by,
        item_count=len(p.items) if p.items else 0,
        origin=origin,
        is_autopilot=(origin == "autopilot"),
        created_at=p.created_at.isoformat() if p.created_at else None,
    )


def _proposal_detail(p: AutopilotProposal) -> ProposalDetailOut:
    """Convert proposal model to full detail schema."""
    evidence: list[GapEvidenceOut | DocumentEvidenceOut | dict] = []
    if p.gap_evidence_json:
        try:
            raw = json.loads(p.gap_evidence_json)
            for e in raw:
                if "signal" in e:
                    evidence.append(GapEvidenceOut(**e))
                elif "source" in e and "document" in e:
                    evidence.append(DocumentEvidenceOut(**e))
                else:
                    evidence.append(e)
        except (json.JSONDecodeError, TypeError):
            pass

    items = [
        ProposalItemOut(
            id=item.id,
            action=item.action,
            target_neuron_id=item.target_neuron_id,
            field=item.field,
            old_value=item.old_value,
            new_value=item.new_value,
            neuron_spec_json=item.neuron_spec_json,
            reason=item.reason,
            created_neuron_id=item.created_neuron_id,
            refinement_id=item.refinement_id,
        )
        for item in (p.items or [])
    ]

    return ProposalDetailOut(
        id=p.id,
        autopilot_run_id=p.autopilot_run_id,
        query_id=p.query_id,
        state=p.state,
        gap_source=p.gap_source,
        gap_description=p.gap_description,
        gap_evidence=evidence,
        priority_score=p.priority_score,
        llm_reasoning=p.llm_reasoning,
        llm_model=p.llm_model,
        prompt_hash=p.prompt_hash,
        eval_overall=p.eval_overall,
        eval_text=p.eval_text,
        reviewed_by=p.reviewed_by,
        reviewed_at=p.reviewed_at.isoformat() if p.reviewed_at else None,
        review_notes=p.review_notes,
        applied_at=p.applied_at.isoformat() if p.applied_at else None,
        applied_by=p.applied_by,
        items=items,
        created_at=p.created_at.isoformat() if p.created_at else None,
        updated_at=p.updated_at.isoformat() if p.updated_at else None,
    )


@router.get("/", response_model=list[ProposalOut])
async def list_proposals(
    state: str | None = None,
    gap_source: str | None = None,
    origin: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List proposals, optionally filtered by state, gap_source, or origin.

    `origin` is the high-level bucket shown in the UI filter pills:
      - autopilot: autopilot_run_id IS NOT NULL
      - integrity: gap_source LIKE 'integrity_%'
      - document:  gap_source == 'document_ingest'
      - manual:    everything else (no autopilot link, no integrity/document source)
    """
    assert limit > 0, "limit must be positive"
    stmt = select(AutopilotProposal).order_by(AutopilotProposal.id.desc())
    if state:
        stmt = stmt.where(AutopilotProposal.state == state)
    if gap_source:
        stmt = stmt.where(AutopilotProposal.gap_source == gap_source)
    if origin == "autopilot":
        stmt = stmt.where(AutopilotProposal.autopilot_run_id.isnot(None))
    elif origin == "integrity":
        stmt = stmt.where(AutopilotProposal.gap_source.like("integrity_%"))
    elif origin == "document":
        stmt = stmt.where(AutopilotProposal.gap_source == "document_ingest")
    elif origin == "manual":
        stmt = stmt.where(
            AutopilotProposal.autopilot_run_id.is_(None),
            or_(
                AutopilotProposal.gap_source.is_(None),
                and_(
                    ~AutopilotProposal.gap_source.like("integrity_%"),
                    AutopilotProposal.gap_source != "document_ingest",
                ),
            ),
        )
    elif origin is not None:
        raise HTTPException(400, f"Unknown origin: {origin!r}")
    stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return [_proposal_summary(p) for p in result.scalars().all()]


@router.get("/stats", response_model=ProposalStatsOut)
async def proposal_stats(db: AsyncSession = Depends(get_db)):
    """Aggregate counts by proposal state."""
    result = await db.execute(
        select(AutopilotProposal.state, func.count(AutopilotProposal.id))
        .group_by(AutopilotProposal.state)
    )
    counts = {row[0]: row[1] for row in result.all()}
    return ProposalStatsOut(
        proposed=counts.get("proposed", 0),
        approved=counts.get("approved", 0),
        rejected=counts.get("rejected", 0),
        applied=counts.get("applied", 0),
        total=sum(counts.values()),
    )


@router.get("/{proposal_id}", response_model=ProposalDetailOut)
async def get_proposal(proposal_id: int, db: AsyncSession = Depends(get_db)):
    """Full proposal detail with evidence chain and items."""
    p = await db.get(AutopilotProposal, proposal_id)
    if not p:
        raise HTTPException(404, "Proposal not found")
    return _proposal_detail(p)


@router.post("/{proposal_id}/review", response_model=ProposalDetailOut)
async def review_proposal(
    proposal_id: int,
    req: ProposalReviewRequest,
    db: AsyncSession = Depends(get_db),
):
    """Approve or reject a proposal. Only 'proposed' state proposals can be reviewed."""
    p = await db.get(AutopilotProposal, proposal_id)
    if not p:
        raise HTTPException(404, "Proposal not found")
    if p.state != "proposed":
        raise HTTPException(400, f"Cannot review proposal in state '{p.state}'")

    p.state = "approved" if req.action == "approve" else "rejected"
    p.reviewed_by = req.reviewer
    p.reviewed_at = datetime.utcnow()
    p.review_notes = req.notes

    # If rejecting an integrity proposal, revert linked findings to open
    if p.state == "rejected" and p.gap_source and p.gap_source.startswith("integrity_"):
        await _revert_integrity_findings(db, p.id)

    await db.commit()
    await db.refresh(p)
    return _proposal_detail(p)


async def _submit_rescale_child(
    db: AsyncSession, item: ProposalItem, p: AutopilotProposal,
    identity: UserIdentity, root_action_id: int,
) -> None:
    """Route a 'rescale' ProposalItem through the edge.rescale action."""
    assert item.neuron_spec_json is not None, "rescale item must have spec"
    spec = json.loads(item.neuron_spec_json)
    result = await action_bus.submit(
        db=db, kind="edge.rescale", actor=identity, actor_type="user",
        source_proposal_id=p.id, parent_action_id=root_action_id,
        reason=item.reason,
        input_data={
            "source_id": spec["source_id"],
            "target_id": spec["target_id"],
            "new_weight": spec["new_weight"],
        },
    )
    if result.state != "applied":
        raise HTTPException(
            500, f"edge.rescale child action failed: {result.state} ({result.error})",
        )


async def _submit_link_child(
    db: AsyncSession, item: ProposalItem, p: AutopilotProposal,
    identity: UserIdentity, root_action_id: int,
) -> None:
    """Route a 'link' ProposalItem through the edge.link action."""
    assert item.neuron_spec_json is not None, "link item must have spec"
    spec = json.loads(item.neuron_spec_json)
    result = await action_bus.submit(
        db=db, kind="edge.link", actor=identity, actor_type="user",
        source_proposal_id=p.id, parent_action_id=root_action_id,
        reason=item.reason,
        input_data={
            "source_id": spec["source_id"],
            "target_id": spec["target_id"],
            "weight": spec.get("initial_weight", 0.15),
            "co_fire_count": 1,
            "edge_type": spec.get("edge_type", "pyramidal"),
            "source": spec.get("source", "integrity_completion"),
            "context": spec.get("context", ""),
        },
    )
    if result.state != "applied":
        raise HTTPException(
            500, f"edge.link child action failed: {result.state} ({result.error})",
        )


async def _submit_create_child(
    db: AsyncSession, item: ProposalItem, p: AutopilotProposal,
    total_queries: int, identity: UserIdentity, root_action_id: int,
) -> None:
    """Route a 'create' ProposalItem through the neuron.create action."""
    assert item.neuron_spec_json is not None
    spec = json.loads(item.neuron_spec_json)
    result = await action_bus.submit(
        db=db, kind="neuron.create", actor=identity, actor_type="user",
        source_proposal_id=p.id, parent_action_id=root_action_id,
        reason=item.reason,
        input_data={
            "proposal_id": p.id, "item_id": item.id, "spec": spec,
            "query_id": p.query_id, "total_queries": total_queries,
            "reason": item.reason,
        },
    )
    if result.state != "applied":
        raise HTTPException(
            500, f"neuron.create child action failed: {result.state} ({result.error})",
        )


async def _submit_refine_child(
    db: AsyncSession, item: ProposalItem, p: AutopilotProposal,
    identity: UserIdentity, root_action_id: int,
) -> None:
    """Route an 'update' or 'merge' ProposalItem through the neuron.refine action."""
    result = await action_bus.submit(
        db=db, kind="neuron.refine", actor=identity, actor_type="user",
        source_proposal_id=p.id, parent_action_id=root_action_id,
        reason=item.reason,
        input_data={
            "proposal_id": p.id, "item_id": item.id,
            "target_neuron_id": item.target_neuron_id,
            "field": item.field or "",
            "old_value": item.old_value or "",
            "new_value": item.new_value or "",
            "query_id": p.query_id,
            "reason": item.reason,
        },
    )
    if result.state != "applied":
        raise HTTPException(
            500, f"neuron.refine child action failed: {result.state} ({result.error})",
        )


async def _dispatch_proposal_items(
    db: AsyncSession, items: list[ProposalItem], p: AutopilotProposal,
    total_queries: int, identity: UserIdentity, root_action_id: int,
) -> bool:
    """Run every ProposalItem through the right write path. Returns has_edge_changes."""
    has_edge_changes = False
    for item in items:
        if item.action == "create" and item.neuron_spec_json:
            await _submit_create_child(
                db, item, p, total_queries, identity, root_action_id,
            )
        elif item.action in ("update", "merge") and item.target_neuron_id:
            await _submit_refine_child(db, item, p, identity, root_action_id)
        elif item.action == "rescale" and item.neuron_spec_json:
            await _submit_rescale_child(db, item, p, identity, root_action_id)
            has_edge_changes = True
        elif item.action == "link" and item.neuron_spec_json:
            await _submit_link_child(db, item, p, identity, root_action_id)
            has_edge_changes = True
    return has_edge_changes


@router.post("/{proposal_id}/apply", response_model=ProposalDetailOut)
async def apply_proposal(
    proposal_id: int,
    req: ProposalApplyRequest,
    db: AsyncSession = Depends(get_db),
    identity: UserIdentity = Depends(resolve_identity),
):
    """Apply an approved proposal — writes neurons/updates to the graph.

    All per-item writes pass through the action bus as child actions of a
    single `proposal.apply` root action (AIP governance roadmap, pattern #1,
    Step 2). Edge mutations (rescale/link) are still direct calls — Step 3.
    """
    p = await db.get(AutopilotProposal, proposal_id)
    if not p:
        raise HTTPException(404, "Proposal not found")
    if p.state != "approved":
        raise HTTPException(400, f"Cannot apply proposal in state '{p.state}' — must be 'approved'")

    from app.services.neuron_service import get_system_state
    state = await get_system_state(db)

    items = list(p.items or [])
    root_result = await action_bus.submit(
        db=db, kind="proposal.apply", actor=identity, actor_type="user",
        source_proposal_id=p.id,
        input_data={
            "proposal_id": p.id, "item_count": len(items),
            "applied_by": req.applied_by,
        },
    )
    if root_result.state != "applied":
        raise HTTPException(
            500, f"proposal.apply root action failed: {root_result.state} ({root_result.error})",
        )

    has_edge_changes = await _dispatch_proposal_items(
        db, items, p, state.total_queries, identity, root_result.action_id,
    )

    p.state = "applied"
    p.applied_at = datetime.utcnow()
    p.applied_by = req.applied_by

    if p.gap_source and p.gap_source.startswith("integrity_"):
        await _resolve_integrity_findings(db, p.id, req.applied_by)

    await db.commit()

    if has_edge_changes:
        from app.services.adjacency_cache import invalidate_adjacency_cache
        invalidate_adjacency_cache()

    await db.refresh(p)
    return _proposal_detail(p)


# ── Integrity finding sync ───────────────────────────────────────────


async def _revert_integrity_findings(
    db: AsyncSession, proposal_id: int,
) -> None:
    """When an integrity proposal is rejected, revert linked findings to open."""
    from app.models import IntegrityFinding
    stmt = select(IntegrityFinding).where(IntegrityFinding.proposal_id == proposal_id)
    result = await db.execute(stmt)
    for finding in result.scalars().all():
        finding.status = "open"
        finding.proposal_id = None
        finding.resolution = None
        finding.resolved_by = None
        finding.resolved_at = None


async def _resolve_integrity_findings(
    db: AsyncSession, proposal_id: int, applied_by: str,
) -> None:
    """When an integrity proposal is applied, mark linked findings as resolved."""
    from app.models import IntegrityFinding
    stmt = select(IntegrityFinding).where(IntegrityFinding.proposal_id == proposal_id)
    result = await db.execute(stmt)
    for finding in result.scalars().all():
        finding.status = "resolved"
        finding.resolved_at = datetime.utcnow()


# ── Provenance chain ─────────────────────────────────────────────────

provenance_router = APIRouter(prefix="/admin/neurons", tags=["provenance"])


def _serialize_proposal(p: AutopilotProposal) -> dict:
    """Serialize proposal for provenance response."""
    evidence: list = []
    if p.gap_evidence_json:
        try:
            evidence = json.loads(p.gap_evidence_json)
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "id": p.id, "state": p.state,
        "gap_source": p.gap_source, "gap_description": p.gap_description,
        "gap_evidence": evidence, "priority_score": p.priority_score,
        "llm_reasoning": p.llm_reasoning, "llm_model": p.llm_model,
        "prompt_hash": p.prompt_hash,
        "eval_overall": p.eval_overall, "eval_text": p.eval_text,
        "reviewed_by": p.reviewed_by,
        "reviewed_at": p.reviewed_at.isoformat() if p.reviewed_at else None,
        "review_notes": p.review_notes,
        "applied_at": p.applied_at.isoformat() if p.applied_at else None,
        "applied_by": p.applied_by,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


async def _get_refinements(db: AsyncSession, neuron_id: int) -> list[dict]:
    """Load refinement history for a neuron."""
    refs_result = await db.execute(
        select(NeuronRefinement)
        .where(NeuronRefinement.neuron_id == neuron_id)
        .order_by(NeuronRefinement.id)
    )
    return [
        {"id": r.id, "action": r.action, "field": r.field,
         "old_value": r.old_value, "new_value": r.new_value,
         "reason": r.reason, "query_id": r.query_id,
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in refs_result.scalars().all()
    ]


async def _get_source_links(db: AsyncSession, neuron_id: int) -> list[dict]:
    """Load source document links for a neuron."""
    links_result = await db.execute(
        select(NeuronSourceLink)
        .where(NeuronSourceLink.neuron_id == neuron_id)
        .order_by(NeuronSourceLink.id)
    )
    return [
        {"id": lk.id, "source_document_id": lk.source_document_id,
         "derivation_type": lk.derivation_type, "section_ref": lk.section_ref,
         "review_status": lk.review_status, "link_origin": lk.link_origin}
        for lk in links_result.scalars().all()
    ]


@provenance_router.get("/{neuron_id}/provenance")
async def neuron_provenance(neuron_id: int, db: AsyncSession = Depends(get_db)):
    """Full provenance chain: neuron → proposal → gap evidence → approval."""
    neuron = await db.get(Neuron, neuron_id)
    if not neuron:
        raise HTTPException(404, "Neuron not found")

    result: dict = {
        "neuron": {
            "id": neuron.id, "label": neuron.label, "layer": neuron.layer,
            "node_type": neuron.node_type, "department": neuron.department,
            "source_origin": neuron.source_origin, "source_type": neuron.source_type,
            "citation": neuron.citation, "authority_level": neuron.authority_level,
            "created_at": neuron.created_at.isoformat() if neuron.created_at else None,
        },
        "proposal_item": None,
        "proposal": None,
        "refinements": await _get_refinements(db, neuron_id),
        "source_links": await _get_source_links(db, neuron_id),
    }

    if neuron.proposal_item_id:
        item = await db.get(ProposalItem, neuron.proposal_item_id)
        if item:
            result["proposal_item"] = {
                "id": item.id, "action": item.action, "reason": item.reason,
                "proposal_id": item.proposal_id, "neuron_spec_json": item.neuron_spec_json,
            }
            proposal = await db.get(AutopilotProposal, item.proposal_id)
            if proposal:
                result["proposal"] = _serialize_proposal(proposal)

    return result
