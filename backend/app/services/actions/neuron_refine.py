"""Action: neuron.refine — update a Neuron field with audit trail.

Used by:
  - Proposal apply (with proposal_id + item_id for ProposalItem back-fill)
  - User-driven apply_refinements endpoint
  - Retained legacy automated-refinement receipts
  - Corvus observation update/merge paths

Supported fields include identity text, lifecycle/scope fields, and
created_at_query_count for audited repair of query-age provenance. Anything
else is a no-op (matches existing behavior).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.rbac import UserIdentity
from app.models import Action, Neuron, NeuronRefinement, ProposalItem


# node_type/source_origin added for reference-class graduation proposals
# (mind-reference-class): approving a queued reference_promotion proposal
# flips node_type reference->lesson and source_origin document->
# document_promoted through this same audited apply tree.
# superseded_by added for graph-lint fusion proposals: approving a dedup
# proposal must fully absorb the duplicate (deactivate + supersede), not
# just deactivate it.
_SUPPORTED_FIELDS = frozenset({
    "content", "summary", "label", "is_active", "department",
    "node_type", "source_origin", "superseded_by",
    "created_at_query_count",
})


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
    elif field == "node_type":
        neuron.node_type = new_value
    elif field == "source_origin":
        neuron.source_origin = new_value
    elif field == "superseded_by":
        neuron.superseded_by = int(new_value) if new_value.strip() else None
    elif field == "created_at_query_count":
        neuron.created_at_query_count = int(new_value)


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

    # TOUCHED-NEURON RULE (mind-neuron-evidence-frame): legacy neurons are
    # not bulk-backfilled, but anything this action REWRITES must exit
    # framed. Two doors lead to an unframed durable memory and both are
    # shut here:
    #   content -> the incoming body must satisfy the contract;
    #   node_type -> promoting a non-memory node (e.g. a document
    #     reference) into a durable memory class must not launder unframed
    #     prose into a lesson, so the body it will carry is checked too.
    # Metadata-only refines (is_active, superseded_by, department, ...) are
    # untouched: they do not rewrite the evidence body, and gating them
    # would brick janitor lifecycle work over content it never authored.
    from app.services.evidence_frame import enforce as enforce_frame
    if payload.field == "content":
        enforce_frame(
            payload.new_value, node_type=neuron.node_type,
            abstraction_type=neuron.abstraction_type,
            where="neuron.refine:content",
        )
    elif payload.field == "node_type":
        enforce_frame(
            neuron.content, node_type=payload.new_value,
            abstraction_type=neuron.abstraction_type,
            where="neuron.refine:node_type-promotion",
        )

    old_department = neuron.department
    text_changed = (payload.field in ("content", "summary", "label")
                    and payload.new_value != payload.old_value)
    _apply_field_to_neuron(neuron, payload.field, payload.new_value)
    if payload.field in ("content", "summary"):
        populate_external_references(neuron)

    # KERNEL RULE (mind-reconsolidation-kernel Phase 2): refined text must
    # never ride the old semantic vector. Frozen NVM receipt: #1099 rewrote
    # #57's label/summary/content and recall kept ranking the new text by
    # the pre-refinement embedding — no embed call existed anywhere in
    # actions/. Only neurons already in semantic space regenerate;
    # creation-time embedding stays lesson_store._embed_created's job.
    embedding_regenerated = False
    if text_changed and neuron.embedding is not None:
        await _regenerate_embedding(db, neuron)
        embedding_regenerated = True

    # EDGE-RETYPING POLICY (graph lint, decided 2026-07-16): edge_type
    # stellate/pyramidal encodes same-region vs cross-region AT LINK TIME,
    # so a department change silently invalidates it and would drift the
    # compiler's same-scope clustering. Every department change through
    # this action — lint rescopes included — retypes the neuron's durable
    # topological edges against its NEW region. Memory-semantics edges
    # (supersedes / scoped-by / evidence-link) are assertions, not
    # topology, and are never touched. Weak-tier edges are reapable
    # statistics and are left to organic refresh.
    retyped = 0
    if payload.field == "department" and neuron.department != old_department:
        retyped = await _retype_edges_for_region(db, neuron)

    # label/summary/department/is_active all feed the materialized index -> rebuild.
    from app.services.neuron_index import invalidate_index
    invalidate_index()

    # Temporal parity (kill-temporal-kg): lifecycle fields mutated through
    # proposal apply must land in memory_change_log too, or /janitor/as-of
    # reconstructs a belief state that never existed.
    if payload.field in ("is_active", "superseded_by"):
        from app.models import MemoryChangeEvent
        db.add(MemoryChangeEvent(
            neuron_id=neuron.id, field=payload.field,
            old_value=payload.old_value or None,
            new_value=payload.new_value or None,
            reason=(payload.reason or f"neuron.refine by {actor.user_id}")[:300],
            actor=actor.user_id[:50],
        ))

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
            "edges_retyped": retyped,
            "embedding_regenerated": embedding_regenerated,
        },
        "payload": {"refinement_id": ref.id},
    }


async def _regenerate_embedding(db: AsyncSession, neuron: Neuron) -> None:
    """New text, new vector — in the SAME transaction, with the semantic
    cache refreshed alongside. Fails closed: a refine that cannot
    re-embed must not commit stale-vector state."""
    from app.services.embedding_service import embed_text
    from app.services.reconsolidation.inheritance import embedding_input
    from app.services.semantic_prefilter import update_cache_incremental

    text = embedding_input(neuron.label, neuron.summary, neuron.content)
    loop = asyncio.get_running_loop()
    vec = await loop.run_in_executor(None, embed_text, text)
    assert vec, "embedding must be non-empty"
    neuron.embedding = json.dumps(vec)
    await update_cache_incremental(db, [neuron.id], "neuron")


async def _retype_edges_for_region(db: AsyncSession, neuron: Neuron) -> int:
    """Re-derive stellate/pyramidal on the neuron's durable edges after a
    region change. Bounded by the neuron's promoted degree (JPL-2)."""
    from sqlalchemy import or_, select
    from app.models import NeuronEdge

    edges = (await db.execute(
        select(NeuronEdge).where(
            or_(NeuronEdge.source_id == neuron.id,
                NeuronEdge.target_id == neuron.id),
            NeuronEdge.edge_type.in_(("stellate", "pyramidal")),
        )
    )).scalars().all()
    retyped = 0
    for edge in edges:
        peer_id = edge.target_id if edge.source_id == neuron.id else edge.source_id
        peer = await db.get(Neuron, peer_id)
        if peer is None:
            continue
        expected = "stellate" if peer.department == neuron.department else "pyramidal"
        if edge.edge_type != expected:
            edge.edge_type = expected
            retyped += 1
    return retyped


def _build_reason(payload: NeuronRefineInput) -> str:
    """Derive a human-readable reason string."""
    if payload.reason:
        return payload.reason
    if payload.proposal_id is not None:
        return f"Applied from proposal #{payload.proposal_id}"
    return "Updated via action bus"
