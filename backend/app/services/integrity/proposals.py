"""Create proposals from integrity findings for human review.

Each finding resolution that modifies the graph generates an AutopilotProposal
with appropriate ProposalItems. The proposal flows through the standard
approve → apply workflow. Non-graph resolutions (reviewed, flagged, dismissed)
bypass this and resolve directly.
"""

import json
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AutopilotProposal, ProposalItem, IntegrityFinding, Neuron,
)


# Resolutions that create proposals (graph-modifying)
PROPOSAL_RESOLUTIONS = frozenset({
    "merged", "differentiated", "linked",
    "a_correct", "b_correct", "context_added",
})


async def _load_finding_neurons(
    db: AsyncSession, finding: IntegrityFinding,
) -> dict[int, Neuron]:
    """Load neurons referenced by a finding, up to 10."""
    neuron_ids = json.loads(finding.neuron_ids_json) if finding.neuron_ids_json else []
    neurons = {}
    for nid in neuron_ids[:10]:
        n = await db.get(Neuron, nid)
        if n:
            neurons[nid] = n
    assert len(neurons) > 0, "Finding must reference at least one neuron"
    return neurons


async def create_integrity_proposal(
    db: AsyncSession,
    finding_id: int,
    resolution: str,
    reviewer: str,
    notes: str = "",
) -> AutopilotProposal:
    """Generate a proposal from an integrity finding resolution.

    Returns the created proposal. Sets finding.status to 'proposed'
    and links it to the proposal via finding.proposal_id.
    """
    finding = await db.get(IntegrityFinding, finding_id)
    if not finding:
        raise HTTPException(404, "Finding not found")
    if finding.status not in ("open", "proposed"):
        raise HTTPException(400, f"Cannot propose from finding in status '{finding.status}'")
    if resolution not in PROPOSAL_RESOLUTIONS:
        raise HTTPException(400, f"Resolution '{resolution}' does not generate a proposal")

    neurons = await _load_finding_neurons(db, finding)
    items = _build_items(finding, resolution, neurons, notes)
    assert len(items) > 0, "Resolution must produce at least one proposal item"

    proposal = _create_proposal_record(finding, resolution, reviewer, notes)
    db.add(proposal)
    await db.flush()

    for item_kwargs in items:
        db.add(ProposalItem(proposal_id=proposal.id, **item_kwargs))

    finding.status = "proposed"
    finding.proposal_id = proposal.id
    finding.resolved_by = reviewer
    finding.resolution = resolution

    await db.commit()
    await db.refresh(proposal)
    return proposal


def _evidence_metric_and_threshold(
    finding: IntegrityFinding, detail: dict,
) -> tuple[float, float]:
    """(metric_value, threshold) for GapEvidenceOut, per finding type.

    near_duplicate findings carry the cosine similarity that tripped the
    scan; its threshold is the tenant duplicate threshold. Other finding
    types have no single scalar trigger — fall back to the finding's
    priority score with a zero threshold rather than inventing one.
    """
    from app.config import settings
    if finding.finding_type == "near_duplicate":
        sim = detail.get("cosine_similarity")
        if isinstance(sim, (int, float)):
            return float(sim), float(settings.integrity_duplicate_threshold)
    return float(finding.priority_score or 0.0), 0.0


def _create_proposal_record(
    finding: IntegrityFinding, resolution: str, reviewer: str, notes: str,
) -> AutopilotProposal:
    """Build the AutopilotProposal object for a finding resolution.

    gap_evidence_json satisfies the FULL GapEvidenceOut schema at write time
    (description/metric_value/threshold/neuron_ids) — previously these were
    missing and the proposal-detail endpoint fell back to a plain dict
    (roadmap polish item gov-polish-integrity-evidence-schema).
    """
    detail = json.loads(finding.detail_json) if finding.detail_json else {}
    metric_value, threshold = _evidence_metric_and_threshold(finding, detail)
    neuron_ids = json.loads(finding.neuron_ids_json) if finding.neuron_ids_json else []
    return AutopilotProposal(
        state="proposed",
        gap_source=f"integrity_{finding.finding_type}",
        gap_description=finding.description,
        gap_evidence_json=json.dumps([{
            "signal": finding.finding_type,
            "description": finding.description or "",
            "metric_value": metric_value,
            "threshold": threshold,
            "neuron_ids": neuron_ids,
            "query_ids": [],
            "finding_id": finding.id,
            "scan_id": finding.scan_id,
            "severity": finding.severity,
            "resolution": resolution,
            "reviewer": reviewer,
            "notes": notes,
            "detail": detail,
        }]),
        priority_score=finding.priority_score,
        llm_reasoning=f"Integrity {finding.finding_type} finding #{finding.id} — resolved as '{resolution}' by {reviewer}.",
    )


def _build_items(
    finding: IntegrityFinding,
    resolution: str,
    neurons: dict[int, Neuron],
    notes: str,
) -> list[dict]:
    """Build ProposalItem kwargs for a given finding + resolution."""
    ft = finding.finding_type
    nids = list(neurons.keys())

    if ft == "near_duplicate":
        return _build_duplicate_items(resolution, neurons, nids, notes)
    elif ft == "missing_connection":
        return _build_connection_items(neurons, nids)
    elif ft == "contradiction":
        return _build_contradiction_items(resolution, neurons, nids, notes, finding)
    else:
        raise HTTPException(400, f"No proposal logic for finding type '{ft}'")


def _merge_items(survivor: Neuron, duplicate: Neuron) -> list[dict]:
    """Build proposal items to merge duplicate into survivor."""
    merged_content = (
        f"{survivor.content or ''}\n\n"
        f"--- Merged from #{duplicate.id} ({duplicate.label}) ---\n"
        f"{duplicate.content or ''}"
    ).strip()
    return [
        {
            "action": "update",
            "target_neuron_id": survivor.id,
            "field": "content",
            "old_value": survivor.content or "",
            "new_value": merged_content,
            "reason": f"Merge duplicate #{duplicate.id} content into survivor #{survivor.id}",
        },
        {
            "action": "update",
            "target_neuron_id": duplicate.id,
            "field": "is_active",
            "old_value": "true",
            "new_value": "false",
            "reason": f"Deactivate duplicate — content merged into #{survivor.id}",
        },
    ]


def _build_duplicate_items(
    resolution: str,
    neurons: dict[int, Neuron],
    nids: list[int],
    notes: str,
) -> list[dict]:
    """Build items for near_duplicate findings."""
    assert len(nids) >= 2, "Duplicate finding must reference at least 2 neurons"
    n_a = neurons[nids[0]]
    n_b = neurons[nids[1]]

    if resolution == "merged":
        survivor, duplicate = (n_a, n_b) if n_a.invocations >= n_b.invocations else (n_b, n_a)
        return _merge_items(survivor, duplicate)

    elif resolution == "differentiated":
        context_a = notes or f"distinct from #{n_b.id} ({n_b.label})"
        context_b = notes or f"distinct from #{n_a.id} ({n_a.label})"
        return [
            {
                "action": "update",
                "target_neuron_id": n_a.id,
                "field": "content",
                "old_value": n_a.content or "",
                "new_value": f"{n_a.content or ''}\n\n[Scope note: {context_a}]",
                "reason": f"Add distinguishing context vs #{n_b.id}",
            },
            {
                "action": "update",
                "target_neuron_id": n_b.id,
                "field": "content",
                "old_value": n_b.content or "",
                "new_value": f"{n_b.content or ''}\n\n[Scope note: {context_b}]",
                "reason": f"Add distinguishing context vs #{n_a.id}",
            },
        ]

    raise HTTPException(400, f"Unknown duplicate resolution '{resolution}'")


def _build_connection_items(
    neurons: dict[int, Neuron],
    nids: list[int],
) -> list[dict]:
    """Build items for missing_connection findings."""
    assert len(nids) >= 2, "Connection finding must reference at least 2 neurons"
    return [
        {
            "action": "link",
            "neuron_spec_json": json.dumps({
                "source_id": nids[0],
                "target_id": nids[1],
                "initial_weight": 0.15,
                "edge_type": "pyramidal",
                "source": "integrity_completion",
                "context": f"Semantic similarity detected between #{nids[0]} and #{nids[1]}",
            }),
            "reason": f"Create missing connection between #{nids[0]} and #{nids[1]}",
        },
    ]


def _build_contradiction_items(
    resolution: str,
    neurons: dict[int, Neuron],
    nids: list[int],
    notes: str,
    finding: IntegrityFinding,
) -> list[dict]:
    """Build items for contradiction findings."""
    assert len(nids) >= 2, "Contradiction finding must reference at least 2 neurons"
    n_a = neurons[nids[0]]
    n_b = neurons[nids[1]]

    if resolution == "a_correct":
        return [
            {
                "action": "update",
                "target_neuron_id": n_b.id,
                "field": "is_active",
                "old_value": "true",
                "new_value": "false",
                "reason": f"Deactivate — contradicts #{n_a.id} which was determined correct",
            },
        ]

    elif resolution == "b_correct":
        return [
            {
                "action": "update",
                "target_neuron_id": n_a.id,
                "field": "is_active",
                "old_value": "true",
                "new_value": "false",
                "reason": f"Deactivate — contradicts #{n_b.id} which was determined correct",
            },
        ]

    elif resolution == "context_added":
        detail = json.loads(finding.detail_json) if finding.detail_json else {}
        analysis = detail.get("analysis", notes or "Scope clarification added")
        return [
            {
                "action": "update",
                "target_neuron_id": n_a.id,
                "field": "content",
                "old_value": n_a.content or "",
                "new_value": f"{n_a.content or ''}\n\n[Scope: {analysis}]",
                "reason": f"Add scope context to distinguish from #{n_b.id}",
            },
            {
                "action": "update",
                "target_neuron_id": n_b.id,
                "field": "content",
                "old_value": n_b.content or "",
                "new_value": f"{n_b.content or ''}\n\n[Scope: {analysis}]",
                "reason": f"Add scope context to distinguish from #{n_a.id}",
            },
        ]

    raise HTTPException(400, f"Unknown contradiction resolution '{resolution}'")
