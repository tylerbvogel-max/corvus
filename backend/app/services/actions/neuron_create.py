"""Action: neuron.create — create a new Neuron with a NeuronRefinement audit trail.

Used by:
  - Proposal apply (with proposal_id + item_id for ProposalItem back-fill)
  - User-driven apply_refinements endpoint (manual creation)
  - Retained legacy automated-create receipts
  - Corvus observation new-neuron promotion
  - Admin bulk ingest
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.rbac import UserIdentity
from app.models import (
    ABSTRACTION_BY_NODE_TYPE, Action, Neuron, NeuronRefinement, ProposalItem,
)


class NeuronCreateInput(BaseModel):
    spec: dict[str, Any]
    query_id: int | None = None
    total_queries: int = 0
    reason: str | None = None
    # Proposal context — optional; only set when called from proposal apply.
    proposal_id: int | None = None
    item_id: int | None = None


def _build_neuron_from_spec(
    spec: dict[str, Any], item_id: int | None, total_queries: int,
) -> Neuron:
    """Materialize a Neuron ORM object from a spec dict."""
    node_type = spec.get("node_type", "knowledge")
    return Neuron(
        parent_id=spec.get("parent_id"),
        layer=spec.get("layer", 3),
        node_type=node_type,
        abstraction_type=spec.get("abstraction_type")
        or ABSTRACTION_BY_NODE_TYPE.get(node_type),
        label=spec.get("label", ""),
        content=spec.get("content", ""),
        summary=spec.get("summary", ""),
        department=spec.get("department"),
        role_key=spec.get("role_key"),
        is_active=True,
        created_at_query_count=total_queries,
        source_origin=spec.get("source_origin", "autopilot"),
        source_type=spec.get("source_type", "operational"),
        citation=spec.get("citation"),
        source_url=spec.get("source_url"),
        authority_level=spec.get("authority_level"),
        entities=spec.get("entities"),
        proposal_item_id=item_id,
    )


async def handle_neuron_create(
    payload: NeuronCreateInput,
    actor: UserIdentity,
    db: AsyncSession,
    action_row: Action,
) -> dict[str, Any]:
    """Create a Neuron + NeuronRefinement(create), optionally back-fill ProposalItem."""
    from app.services.evidence_frame import enforce as enforce_frame
    from app.services.reference_hooks import populate_external_references

    spec = payload.spec
    # FAIL CLOSED (mind-neuron-evidence-frame): every durable memory enters
    # the graph through this action, so this is the one place that has to
    # hold. Structural scaffolding is exempt by classification inside
    # enforce(); there is no per-write bypass.
    node_type = spec.get("node_type", "knowledge")
    enforce_frame(
        spec.get("content"), node_type=node_type,
        abstraction_type=spec.get("abstraction_type")
        or ABSTRACTION_BY_NODE_TYPE.get(node_type),
        where="neuron.create",
    )
    neuron = _build_neuron_from_spec(spec, payload.item_id, payload.total_queries)
    populate_external_references(neuron)
    db.add(neuron)
    await db.flush()
    assert neuron.id is not None, "Neuron must have id after flush"

    ref_reason = _build_reason(payload)
    ref = NeuronRefinement(
        query_id=payload.query_id, neuron_id=neuron.id,
        action="create", field=None, old_value=None,
        new_value=spec.get("label", ""), reason=ref_reason,
    )
    db.add(ref)
    await db.flush()
    assert ref.id is not None, "NeuronRefinement must have id after flush"

    # Back-fill ProposalItem if this came from a proposal apply.
    if payload.item_id is not None:
        item = await db.get(ProposalItem, payload.item_id)
        assert item is not None, f"ProposalItem {payload.item_id} not found"
        item.created_neuron_id = neuron.id
        item.refinement_id = ref.id
        await db.flush()

    # New neuron -> the materialized NeuronIndex is stale; force a rebuild.
    from app.services.neuron_index import invalidate_index
    invalidate_index()

    return {
        "audit": {
            "created_neuron_id": neuron.id, "refinement_id": ref.id,
            "label": spec.get("label", ""),
            "proposal_id": payload.proposal_id, "item_id": payload.item_id,
        },
        "payload": {"neuron_id": neuron.id, "refinement_id": ref.id},
    }


def _build_reason(payload: NeuronCreateInput) -> str:
    """Derive a human-readable reason string."""
    if payload.reason:
        return payload.reason
    if payload.proposal_id is not None:
        return f"Created from proposal #{payload.proposal_id}"
    return "Created via action bus"
