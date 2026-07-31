"""Proposals router — CRUD, approve/reject, apply, provenance chain.

Staged autopilot proposals with full audit trail. Nothing modifies the
neuron graph until a proposal is explicitly approved and applied.
"""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError
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

    Precedence: integrity_* > document_ingest > emergent_queue > autopilot link > manual.
    Source-string classifications take precedence over the autopilot FK
    because integrity/document/emergent proposals are created inside autopilot
    ticks and still carry an autopilot_run_id — but the user-facing producer
    is the upstream source, not the autopilot tick that wrapped it.
    """
    src = p.gap_source or ""
    if src.startswith("integrity_"):
        return "integrity"
    if src == "document_ingest":
        return "document"
    if src == "emergent_queue":
        return "emergent"
    if src == "reconsolidation_quality":
        return "auditor"
    if p.autopilot_run_id is not None:
        return "autopilot"
    return "manual"


def _extract_source_ids(p: AutopilotProposal) -> tuple[int | None, int | None]:
    """Pull (finding_id, scan_id) out of gap_evidence_json for integrity rows.

    Integrity-backed proposals embed the upstream finding_id + scan_id in the
    first evidence dict (see services/integrity/proposals.py). Extracting them
    server-side lets the UI render reverse deep-links without re-parsing JSON.
    Returns (None, None) for non-integrity rows or malformed evidence.
    """
    if not p.gap_evidence_json:
        return None, None
    try:
        raw = json.loads(p.gap_evidence_json)
    except (json.JSONDecodeError, TypeError):
        return None, None
    if not isinstance(raw, list) or not raw:
        return None, None
    first = raw[0]
    if not isinstance(first, dict):
        return None, None
    fid = first.get("finding_id")
    sid = first.get("scan_id")
    finding_id = int(fid) if isinstance(fid, int) else None
    scan_id = int(sid) if isinstance(sid, int) else None
    return finding_id, scan_id


def _proposal_summary(p: AutopilotProposal) -> ProposalOut:
    """Convert proposal model to summary schema."""
    origin = _classify_origin(p)
    finding_id, scan_id = _extract_source_ids(p)
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
        finding_id=finding_id,
        scan_id=scan_id,
    )


def _proposal_detail(p: AutopilotProposal) -> ProposalDetailOut:
    """Convert proposal model to full detail schema."""
    evidence: list[GapEvidenceOut | DocumentEvidenceOut | dict] = []
    if p.gap_evidence_json:
        try:
            raw = json.loads(p.gap_evidence_json)
            for e in raw:
                # Integrity-backed proposals write evidence dicts that have a
                # "signal" field but not the full GapEvidenceOut schema. Fall
                # through to the plain-dict branch on validation failure so
                # the proposal detail endpoint stays functional.
                if "signal" in e:
                    try:
                        evidence.append(GapEvidenceOut(**e))
                    except ValidationError:
                        evidence.append(e)
                elif "source" in e and "document" in e:
                    try:
                        evidence.append(DocumentEvidenceOut(**e))
                    except ValidationError:
                        evidence.append(e)
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


def _apply_origin_filter(stmt, origin: str):
    """Attach the SQL WHERE clause matching _classify_origin precedence.

    Mirrors the Python classifier exactly so list-filtering and badge counts
    agree on which origin a row belongs to.
    """
    is_integrity = AutopilotProposal.gap_source.like("integrity_%")
    is_document = AutopilotProposal.gap_source == "document_ingest"
    is_emergent = AutopilotProposal.gap_source == "emergent_queue"
    is_auditor = AutopilotProposal.gap_source == "reconsolidation_quality"
    if origin == "integrity":
        return stmt.where(is_integrity)
    if origin == "document":
        return stmt.where(is_document)
    if origin == "emergent":
        return stmt.where(is_emergent)
    if origin == "auditor":
        return stmt.where(is_auditor)
    if origin == "autopilot":
        return stmt.where(
            AutopilotProposal.autopilot_run_id.isnot(None),
            ~is_integrity, ~is_document, ~is_emergent, ~is_auditor,
        )
    if origin == "manual":
        return stmt.where(
            AutopilotProposal.autopilot_run_id.is_(None),
            or_(
                AutopilotProposal.gap_source.is_(None),
                and_(~is_integrity, ~is_document, ~is_emergent, ~is_auditor),
            ),
        )
    raise HTTPException(400, f"Unknown origin: {origin!r}")


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
      - integrity: gap_source LIKE 'integrity_%'
      - document:  gap_source == 'document_ingest'
      - emergent:  gap_source == 'emergent_queue'
      - autopilot: autopilot_run_id IS NOT NULL and none of the above
      - manual:    no autopilot link and none of the above source strings
    """
    assert limit > 0, "limit must be positive"
    stmt = select(AutopilotProposal).order_by(AutopilotProposal.id.desc())
    if state:
        stmt = stmt.where(AutopilotProposal.state == state)
    if gap_source:
        stmt = stmt.where(AutopilotProposal.gap_source == gap_source)
    if origin is not None:
        stmt = _apply_origin_filter(stmt, origin)
    stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    return [_proposal_summary(p) for p in result.scalars().all()]


@router.get("/stats", response_model=ProposalStatsOut)
async def proposal_stats(db: AsyncSession = Depends(get_db)):
    """Aggregate counts by proposal state, plus pending counts per origin.

    `proposed_by_origin` powers per-producer badges in the frontend nav.
    Runs over the pending set only (state='proposed') so idle producers
    do not advertise stale approved/applied work. Bounded: iterates finite
    rows returned by a single indexed query.
    """
    result = await db.execute(
        select(AutopilotProposal.state, func.count(AutopilotProposal.id))
        .group_by(AutopilotProposal.state)
    )
    counts = {row[0]: row[1] for row in result.all()}
    pending_rows = (await db.execute(
        select(
            AutopilotProposal.autopilot_run_id,
            AutopilotProposal.gap_source,
        ).where(AutopilotProposal.state == "proposed")
    )).all()
    origin_counts: dict[str, int] = {}
    for run_id, src in pending_rows:
        origin = _classify_origin_tuple(run_id, src)
        origin_counts[origin] = origin_counts.get(origin, 0) + 1
    return ProposalStatsOut(
        proposed=counts.get("proposed", 0),
        approved=counts.get("approved", 0),
        rejected=counts.get("rejected", 0),
        applied=counts.get("applied", 0),
        superseded=counts.get("superseded", 0),
        total=sum(counts.values()),
        proposed_by_origin=origin_counts,
    )


def _classify_origin_tuple(run_id: int | None, src: str | None) -> str:
    """Same rules as _classify_origin but against a (run_id, gap_source) tuple.

    Used by /stats so it can aggregate without materializing full ORM rows.
    """
    s = src or ""
    if s.startswith("integrity_"):
        return "integrity"
    if s == "document_ingest":
        return "document"
    if s == "emergent_queue":
        return "emergent"
    if s == "reconsolidation_quality":
        return "auditor"
    if run_id is not None:
        return "autopilot"
    return "manual"


async def _attach_rendered_plans(
    db: AsyncSession, p: AutopilotProposal, detail: ProposalDetailOut,
) -> ProposalDetailOut:
    """Decorate reconsolidate items with the server-rendered review
    projection (mind-fusionplan-preview-ui). Read-only: loads live members
    and renders; an unreadable spec becomes an error object on the item
    rather than failing the whole detail response."""
    from app.services.reconsolidation.apply import parse_reconsolidation_spec
    from app.services.reconsolidation.render import render_fusion_plan

    out_by_id = {i.id: i for i in detail.items}
    for item in (p.items or []):
        if item.action != "reconsolidate" or not item.neuron_spec_json:
            continue
        target = out_by_id.get(item.id)
        if target is None:
            continue
        try:
            plan, plan_hash, member_hash = parse_reconsolidation_spec(
                item.neuron_spec_json)
        except Exception as exc:  # unreadable plan is itself a review fact
            target.rendered_plan = {
                "kind": "reconsolidate",
                "error": f"unreadable fusion plan: {exc}",
            }
            continue
        live = {}
        for mid in plan.member_ids:
            n = await db.get(Neuron, mid)
            if n is not None:
                live[mid] = n
        target.rendered_plan = render_fusion_plan(
            plan, plan_hash, member_hash, live)
    return detail


# NOTE: declared before /{proposal_id} — FastAPI matches routes in order and
# "dedup-clusters" must not be swallowed as a proposal_id.
@router.get("/dedup-clusters")
async def dedup_clusters(
    state: str = "proposed",
    threshold: float = 0.88,
    db: AsyncSession = Depends(get_db),
):
    """Semantic near-duplicate clusters over proposals in `state`.

    Embeds gap_description locally ($0) and greedily clusters by cosine >=
    threshold so reviewers handle one cluster instead of N re-detected
    copies. Advisory: never mutates proposals; the UI uses it for grouping.
    """
    assert 0.0 < threshold <= 1.0, "threshold must be a cosine in (0, 1]"
    from app.services.proposal_dedup import compute_dedup_clusters
    return await compute_dedup_clusters(db, state=state, threshold=threshold)


# NOTE: declared before /{proposal_id} for the same reason as
# /dedup-clusters above: FastAPI route matching is ordered, and a static
# identity endpoint must not be parsed as an integer proposal id.
@router.get("/whoami")
async def whoami(identity: UserIdentity = Depends(resolve_identity)):
    """Return the resolved identity for the current request.

    Frontend uses this to prefill the reviewer name so the free-text field
    on the proposal row is guaranteed equal to the auth user — meaning
    proposal-level audit (reviewed_by/applied_by) matches neuron-level
    action-bus lineage. No divergence possible.
    """
    return {"user_id": identity.user_id, "role": identity.role, "source": identity.source}


@router.get("/{proposal_id}", response_model=ProposalDetailOut)
async def get_proposal(proposal_id: int, db: AsyncSession = Depends(get_db)):
    """Full proposal detail with evidence chain and items."""
    p = await db.get(AutopilotProposal, proposal_id)
    if not p:
        raise HTTPException(404, "Proposal not found")
    return await _attach_rendered_plans(db, p, _proposal_detail(p))


@router.post("/{proposal_id}/review", response_model=ProposalDetailOut)
async def review_proposal(
    proposal_id: int,
    req: ProposalReviewRequest,
    db: AsyncSession = Depends(get_db),
    identity: UserIdentity = Depends(resolve_identity),
):
    """Review a proposal. ONE-step lifecycle (kernel Phase 4, Tyler
    directive 2026-07-17): approve = approve AND apply in a single
    transaction — an approved-but-unapplied proposal can no longer exist
    through this path. If the proposal's recorded old-state drifted, it is
    terminally superseded instead (409). If any apply step fails, the
    whole transaction (approval included) rolls back and the proposal
    stays 'proposed' (500). The reject path is unchanged.

    `reviewed_by` is taken from the resolved auth identity, not the request
    body — this makes the proposal-level audit trail tamper-proof and keeps
    it aligned with action-bus lineage at apply time. The `reviewer` field
    in the request body is accepted for backward compat but ignored.
    """
    from app.services.proposal_apply_service import ProposalApplyError
    from app.services.reconsolidation.lifecycle import (
        ProposalStaleError, approve_and_apply,
    )

    p = await db.get(AutopilotProposal, proposal_id)
    if not p:
        raise HTTPException(404, "Proposal not found")
    if p.state != "proposed":
        raise HTTPException(400, f"Cannot review proposal in state '{p.state}'")

    if req.action != "approve":
        p.state = "rejected"
        p.reviewed_by = identity.user_id
        p.reviewed_at = datetime.utcnow()
        p.review_notes = req.notes
        # If rejecting an integrity proposal, revert linked findings to open
        if p.gap_source and p.gap_source.startswith("integrity_"):
            await _revert_integrity_findings(db, p.id)
        await db.commit()
        await db.refresh(p)
        return await _attach_rendered_plans(db, p, _proposal_detail(p))

    try:
        has_edge_changes = await approve_and_apply(db, p, identity, req.notes)
    except ProposalStaleError as exc:
        await db.commit()  # persist the terminal supersession
        raise HTTPException(
            409, f"Proposal superseded — its recorded old-state drifted: "
                 f"{'; '.join(exc.violations)}")
    except ProposalApplyError as exc:
        await db.rollback()  # approval rolls back with the writes
        raise HTTPException(500, str(exc))

    await db.commit()
    await _post_apply_refresh(db, p, has_edge_changes)
    await db.refresh(p)
    return await _attach_rendered_plans(db, p, _proposal_detail(p))


async def _post_apply_refresh(
    db: AsyncSession, p: AutopilotProposal, has_edge_changes: bool,
) -> None:
    """Post-commit refreshes: process caches and (for reconsolidations)
    compiled skill/charter projections whose source lessons were retired.
    Disk artifacts refresh only after the transaction is durable."""
    if has_edge_changes:
        from app.services.adjacency_cache import invalidate_adjacency_cache
        invalidate_adjacency_cache()
    from app.services.reconsolidation.lifecycle import find_reconsolidation_item
    item = find_reconsolidation_item(p)
    if item is None:
        return
    from app.services.reconsolidation.apply import parse_reconsolidation_spec
    from app.services.skill_compiler import (
        refresh_projections_after_reconsolidation,
    )
    plan, _ph, _mh = parse_reconsolidation_spec(item.neuron_spec_json)
    refreshed = await refresh_projections_after_reconsolidation(
        db, sorted(plan.member_ids))
    if refreshed.get("db_changed"):
        await db.commit()


@router.post("/{proposal_id}/apply", response_model=ProposalDetailOut)
async def apply_proposal(
    proposal_id: int,
    req: ProposalApplyRequest,
    db: AsyncSession = Depends(get_db),
    identity: UserIdentity = Depends(resolve_identity),
):
    """Apply an approved proposal — writes neurons/updates to the graph.

    LEGACY path: review (approve) now applies in the same transaction, so
    this endpoint only serves rows approved before the one-step lifecycle.
    Old-state revalidation runs first — a drifted proposal is terminally
    superseded (409) instead of applying against dead state (Phase 4A).

    Dispatch lives in proposal_apply_service (shared with the tiered write
    gate's auto route): all per-item writes pass through the action bus as
    child actions of a single `proposal.apply` root action.
    """
    from app.services.proposal_apply_service import (
        ProposalApplyError, apply_approved_proposal,
    )
    from app.services.reconsolidation.lifecycle import (
        mark_superseded, revalidate_items,
    )

    p = await db.get(AutopilotProposal, proposal_id)
    if not p:
        raise HTTPException(404, "Proposal not found")
    if p.state != "approved":
        raise HTTPException(400, f"Cannot apply proposal in state '{p.state}' — must be 'approved'")

    stale = await revalidate_items(db, p)
    if stale:
        mark_superseded(
            db, p, reason="stale at apply time: " + "; ".join(stale)[:800],
            actor_id=identity.user_id)
        await db.commit()
        raise HTTPException(
            409, f"Proposal superseded — its recorded old-state drifted: "
                 f"{'; '.join(stale)}")

    try:
        has_edge_changes = await apply_approved_proposal(db, p, identity, actor_type="user")
    except ProposalApplyError as exc:
        await db.rollback()
        raise HTTPException(500, str(exc))

    await db.commit()
    await _post_apply_refresh(db, p, has_edge_changes)

    await db.refresh(p)
    return await _attach_rendered_plans(db, p, _proposal_detail(p))


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
