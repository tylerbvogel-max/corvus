"""Action: neuron.refine — update a Neuron field with audit trail.

Used by:
  - Proposal apply (with proposal_id + item_id for ProposalItem back-fill)
  - User-driven apply_refinements endpoint
  - Autopilot _apply_single_update
  - Corvus observation update/merge paths

Supported fields: content, summary, label, is_active, department (region
tag assignment for emergent seeding). Anything else is a no-op (matches
existing behavior).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.rbac import UserIdentity
from app.models import Action, Neuron, NeuronRefinement, ProposalItem


_SUPPORTED_FIELDS = frozenset({"content", "summary", "label", "is_active", "department"})


class NeuronRefineInput(BaseModel):
    target_neuron_id: int
    field: str = Field(..., max_length=50)
    old_value: str = ""
    new_value: str = ""
    query_id: int | None = None
    reason: str | None = None
    # Proposal context — optional; only set when called from proposal apply.
    proposal_id: int | None = None
    item_id: int | None = None


def _apply_field_to_neuron(neuron: Neuron, field: str, new_value: str) -> None:
    """Mutate a single supported field on a Neuron in place."""
    if field == "content":
        neuron.content = new_value
    elif field == "summary":
        neuron.summary = new_value
    elif field == "label":
        neuron.label = new_value
    elif field == "is_active":
        neuron.is_active = new_value.lower() in ("true", "1", "yes")
    elif field == "department":
        neuron.department = new_value or None


def _skipped_audit(payload: NeuronRefineInput, reason: str) -> dict[str, Any]:
    """Build the audit dict for a no-op refine."""
    return {
        "audit": {
            "target_neuron_id": payload.target_neuron_id, "skipped": reason,
            "proposal_id": payload.proposal_id, "item_id": payload.item_id,
        },
        "payload": {"refinement_id": None},
    }


async def handle_neuron_refine(
    payload: NeuronRefineInput,
    actor: UserIdentity,
    db: AsyncSession,
    action_row: Action,
) -> dict[str, Any]:
    """Mutate a single field on a Neuron + write the refinement record."""
    from app.services.reference_hooks import populate_external_references

    neuron = await db.get(Neuron, payload.target_neuron_id)
    if neuron is None:
        return _skipped_audit(payload, "neuron_not_found")
    if payload.field not in _SUPPORTED_FIELDS:
        return _skipped_audit(payload, f"unsupported_field:{payload.field}")

    _apply_field_to_neuron(neuron, payload.field, payload.new_value)
    if payload.field in ("content", "summary"):
        populate_external_references(neuron)

    # label/summary/department/is_active all feed the materialized index -> rebuild.
    from app.services.neuron_index import invalidate_index
    invalidate_index()

    ref_reason = _build_reason(payload)
    ref = NeuronRefinement(
        query_id=payload.query_id, neuron_id=payload.target_neuron_id,
        action="update", field=payload.field,
        old_value=payload.old_value, new_value=payload.new_value,
        reason=ref_reason,
    )
    db.add(ref)
    await db.flush()
    assert ref.id is not None, "NeuronRefinement must have id after flush"

    if payload.item_id is not None:
        item = await db.get(ProposalItem, payload.item_id)
        assert item is not None, f"ProposalItem {payload.item_id} not found"
        item.refinement_id = ref.id
        await db.flush()

    return {
        "audit": {
            "target_neuron_id": payload.target_neuron_id,
            "field": payload.field, "refinement_id": ref.id,
            "proposal_id": payload.proposal_id, "item_id": payload.item_id,
        },
        "payload": {"refinement_id": ref.id},
    }


def _build_reason(payload: NeuronRefineInput) -> str:
    """Derive a human-readable reason string."""
    if payload.reason:
        return payload.reason
    if payload.proposal_id is not None:
        return f"Applied from proposal #{payload.proposal_id}"
    return "Updated via action bus"
