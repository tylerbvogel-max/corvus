"""Action: neuron.stats.rebuild — install field-specific inherited signals.

The apply half of kernel Phase 2: the surviving representation's
statistics are SET to the values the approved InheritancePreview computed
by each field's deterministic rule (union-distinct invocations, replayed
utility from the birth prior, evidence-derived authority and dates) —
never summed, maxed, or carried over. The embedding is regenerated from
the FINAL text inside the same transaction (creation-recipe parity) so
recall can never rank new text by an old vector; the returned sha256 is
the freshness receipt the postconditions assert.

avg_utility and authority_level changes land in memory_change_log so the
temporal as-of view stays truthful about when and why the numbers moved.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.rbac import UserIdentity
from app.models import Action, MemoryChangeEvent, Neuron


class NeuronStatsRebuildInput(BaseModel):
    neuron_id: int
    invocations: int = Field(..., ge=0)
    avg_utility: float = Field(..., ge=0.0, le=1.0)
    authority_level: str | None = None
    effective_date: str | None = None       # ISO date
    last_verified: str | None = None        # ISO datetime
    utility_provenance_gaps: list[str] = Field(default_factory=list)
    reason: str | None = None


async def handle_neuron_stats_rebuild(
    payload: NeuronStatsRebuildInput,
    actor: UserIdentity,
    db: AsyncSession,
    action_row: Action,
) -> dict[str, Any]:
    from app.services.embedding_service import embed_text
    from app.services.reconsolidation.inheritance import embedding_input
    from app.services.semantic_prefilter import update_cache_incremental

    neuron = await db.get(Neuron, payload.neuron_id)
    assert neuron is not None, f"neuron {payload.neuron_id} not found"
    reason = (payload.reason or "reconsolidation: field-specific "
              "inheritance rebuild")[:300]

    old_utility = neuron.avg_utility
    if old_utility != payload.avg_utility:
        db.add(MemoryChangeEvent(
            neuron_id=neuron.id, field="avg_utility",
            old_value=None if old_utility is None else str(old_utility),
            new_value=str(payload.avg_utility),
            reason=reason, actor=actor.user_id[:50],
        ))
    if payload.authority_level and payload.authority_level != neuron.authority_level:
        db.add(MemoryChangeEvent(
            neuron_id=neuron.id, field="authority_level",
            old_value=neuron.authority_level,
            new_value=payload.authority_level,
            reason=reason, actor=actor.user_id[:50],
        ))
        neuron.authority_level = payload.authority_level

    neuron.invocations = payload.invocations
    neuron.avg_utility = payload.avg_utility
    if payload.effective_date:
        neuron.effective_date = datetime.date.fromisoformat(payload.effective_date)
    if payload.last_verified:
        neuron.last_verified = datetime.datetime.fromisoformat(payload.last_verified)

    # New identity text, new vector — same transaction, creation-recipe
    # parity, semantic cache refreshed alongside. Fails closed.
    text = embedding_input(neuron.label, neuron.summary, neuron.content)
    loop = asyncio.get_running_loop()
    vec = await loop.run_in_executor(None, embed_text, text)
    assert vec, "embedding must be non-empty"
    neuron.embedding = json.dumps(vec)
    await update_cache_incremental(db, [neuron.id], "neuron")
    await db.flush()

    embedding_sha = hashlib.sha256(neuron.embedding.encode()).hexdigest()
    audit = {
        "neuron_id": neuron.id,
        "invocations": payload.invocations,
        "avg_utility": payload.avg_utility,
        "authority_level": neuron.authority_level,
        "effective_date": payload.effective_date,
        "last_verified": payload.last_verified,
        "embedding_regenerated": True,
        "embedding_sha256": embedding_sha,
        "utility_provenance_gaps": payload.utility_provenance_gaps,
    }
    return {"audit": audit, "payload": audit}
