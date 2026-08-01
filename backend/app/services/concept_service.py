"""Concept neuron service: department-agnostic framework nodes.

Concept neurons represent transdisciplinary frameworks (e.g. Three Horizons,
PDCA, DMAIC) that span multiple departments. They use:
- node_type='concept', layer=-1, department=NULL, parent_id=NULL
- 'instantiates' edge_type connecting to department-specific neurons
- Higher spread decay (0.6) and lower threshold (0.10) than pyramidal edges

Concept neurons act as seed nodes for future community detection (Phase 5),
bootstrapping clusters that Leiden/Louvain then completes organically.
"""

import json

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.middleware.rbac import UserIdentity
from app.models import Neuron, NeuronEdge
from app.tenant import tenant


_CONCEPT_SYSTEM_ACTOR = UserIdentity(
    user_id="concept_service", role="admin", source="system",
)


class ConceptMutationError(RuntimeError):
    """An authoritative concept mutation failed inside the Action Bus."""


def _concept_create_input(
    label: str,
    content: str,
    summary: str | None,
    total_queries: int,
) -> dict:
    return {
        "total_queries": total_queries,
        "reason": f"Create concept neuron: {label}",
        "spec": {
            "parent_id": None,
            "layer": -1,
            "node_type": "concept",
            "label": label,
            "content": content,
            "summary": summary or label,
            "department": None,
            "role_key": None,
            "source_type": "operational",
            "source_origin": "concept",
        },
    }


async def _derive_concept_embedding(
    db: AsyncSession,
    neuron: Neuron,
    label: str,
    content: str,
    summary: str | None,
) -> None:
    """Populate the rebuildable semantic projection after authoritative create."""
    import asyncio
    import concurrent.futures
    from app.services.embedding_service import embed_text

    loop = asyncio.get_running_loop()
    embed_text_str = f"{label} {summary or ''} {content}"
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        vec = await loop.run_in_executor(pool, embed_text, embed_text_str)
    neuron.embedding = json.dumps(vec)
    await db.flush()


async def create_concept_neuron(
    db: AsyncSession,
    label: str,
    content: str,
    summary: str | None = None,
    *,
    actor: UserIdentity | None = None,
    actor_type: str = "system",
) -> Neuron:
    """Create a concept neuron through the governed write boundary.

    Embedding is a rebuildable projection and remains outside the authoritative
    action. The caller owns the outer transaction.
    """
    from app.services.neuron_service import get_system_state
    from app.services import action_bus

    state = await get_system_state(db)
    result = await action_bus.submit(
        db=db,
        kind="neuron.create",
        actor=actor or _CONCEPT_SYSTEM_ACTOR,
        actor_type=actor_type,
        reason=f"Create concept neuron: {label}",
        input_data=_concept_create_input(
            label, content, summary, state.total_queries,
        ),
    )
    if result.state != "applied" or not result.payload:
        raise ConceptMutationError(
            f"neuron.create failed for concept {label!r}: {result.error or result.state}"
        )
    neuron = await db.get(Neuron, result.payload["neuron_id"])
    if neuron is None:
        raise ConceptMutationError(
            f"neuron.create returned missing concept #{result.payload['neuron_id']}"
        )

    await _derive_concept_embedding(db, neuron, label, content, summary)

    return neuron


async def link_concept_to_neurons(
    db: AsyncSession,
    concept_id: int,
    target_ids: list[int],
    weight: float = 0.5,
    concept_label: str | None = None,
    *,
    actor: UserIdentity | None = None,
    actor_type: str = "system",
) -> int:
    """Assert durable ``instantiates`` edges through the Action Bus.

    These are authored topology, unlike later co-firing adjustments. They stay
    promoted at co-fire count 1 to preserve the original concept semantics.
    Returns the number of successfully asserted relationships.
    """
    from app.services import action_bus

    context_text = f"instantiates concept: {concept_label}" if concept_label else None
    created = 0
    for tid in target_ids:
        src, tgt = min(concept_id, tid), max(concept_id, tid)
        result = await action_bus.submit(
            db=db,
            kind="edge.link",
            actor=actor or _CONCEPT_SYSTEM_ACTOR,
            actor_type=actor_type,
            reason=context_text or "Assert concept instantiation",
            input_data={
                "source_id": src,
                "target_id": tgt,
                "weight": weight,
                "co_fire_count": 1,
                "edge_type": "instantiates",
                "source": "concept_seed",
                "context": context_text or "",
                "last_updated_query": 0,
                "storage_tier": "promoted",
            },
        )
        if result.state != "applied":
            raise ConceptMutationError(
                f"edge.link failed for concept {concept_id} -> {tid}: "
                f"{result.error or result.state}"
            )
        created += 1

    await db.flush()
    return created


async def cofire_concept_neurons(
    db: AsyncSession,
    fired_neuron_ids: list[int],
    query_offset: int,
) -> list[int]:
    """When neurons fire, check if any are linked to concept neurons via 'instantiates' edges.

    If so, boost the concept neuron's co-firing with all other fired neurons that
    share the same concept. This strengthens the concept hub over time.

    Returns list of concept neuron IDs that were co-fired.
    """
    if len(fired_neuron_ids) < 2:
        return []

    # Find concept neurons linked to any fired neuron
    result = await db.execute(text("""
        SELECT DISTINCT n.id
        FROM neurons n
        JOIN neuron_edges e ON (
            (e.source_id = n.id AND e.target_id = ANY(:ids))
            OR (e.target_id = n.id AND e.source_id = ANY(:ids))
        )
        WHERE n.node_type = 'concept' AND n.is_active = true
          AND e.edge_type = 'instantiates'
    """), {"ids": fired_neuron_ids})
    concept_ids = [row[0] for row in result.all()]

    if not concept_ids:
        return []

    # For each concept, strengthen edges between it and all fired neurons
    # Uses tiered storage: promoted edges in table, weak in JSONB
    from app.services.edge_tier import (
        get_weak_edge, upsert_weak_edge, maybe_promote,
    )
    for cid in concept_ids:
        for fid in fired_neuron_ids:
            if fid == cid:
                continue
            src, tgt = min(cid, fid), max(cid, fid)
            # Try UPDATE in promoted table first
            result = await db.execute(text(
                "UPDATE neuron_edges "
                "SET co_fire_count = co_fire_count + 1, "
                "    weight = LEAST(1.0, (co_fire_count + 1) / 20.0), "
                "    last_updated_query = :qoff, "
                "    source = CASE WHEN source = 'bootstrap' THEN 'organic' ELSE source END, "
                "    last_adjusted = now() "
                "WHERE source_id = :src AND target_id = :tgt "
                "RETURNING weight, co_fire_count"
            ), {"src": src, "tgt": tgt, "qoff": query_offset})
            row = result.one_or_none()
            if row:
                continue
            # Not in table — handle in JSONB
            entry = await get_weak_edge(db, src, tgt)
            new_c = (entry.get("c", 0) + 1) if entry else 1
            new_w = min(1.0, (new_c + 1) / 20.0)
            data = {"w": new_w, "t": "instantiates", "c": new_c, "s": "organic", "q": query_offset}
            await upsert_weak_edge(db, src, tgt, data)
            await maybe_promote(db, src, tgt, new_w, new_c)

    return concept_ids


async def get_concept_neurons(db: AsyncSession) -> list[dict]:
    """List all concept neurons with their instantiation edge counts."""
    result = await db.execute(
        select(Neuron).where(Neuron.node_type == "concept").order_by(Neuron.id)
    )
    concepts = list(result.scalars().all())

    output = []
    for c in concepts:
        # Count instantiation edges
        edge_result = await db.execute(text("""
            SELECT COUNT(*) FROM neuron_edges
            WHERE edge_type = 'instantiates'
              AND (source_id = :cid OR target_id = :cid)
        """), {"cid": c.id})
        edge_count = edge_result.scalar() or 0

        output.append({
            "id": c.id,
            "label": c.label,
            "summary": c.summary,
            "content": c.content,
            "invocations": c.invocations,
            "avg_utility": c.avg_utility,
            "instantiation_edges": edge_count,
            "is_active": c.is_active,
        })

    return output


async def seed_three_horizons(db: AsyncSession) -> dict:
    """Seed the Three Horizons framework. Delegates to seed_all_concepts for just this one."""
    results = await seed_all_concepts(db, only=["Three Horizons Framework"])
    if results["seeded"]:
        return {"status": "seeded", **results["seeded"][0]}
    if results["skipped"]:
        return {"status": "already_seeded", "message": results["skipped"][0]}
    return {"status": "error", "message": "Unknown error"}


# ── Concept Neuron Registry — loaded from tenant config ──

CONCEPT_DEFINITIONS: list[dict] = tenant.concept_definitions


async def _match_and_link(
    db: AsyncSession,
    concept_id: int,
    defn: dict,
) -> tuple[list[int], list[int], int]:
    """Match neurons to a concept definition and create instantiation edges.

    Pattern matching:
    - direct_patterns: matched against label and summary (weight 0.5)
    - content_patterns: matched against label, summary, AND content (weight 0.3)
    - role_filters: list of role_key values — all neurons with that role are linked (weight 0.3)

    Returns (direct_ids, content_ids, edges_created).
    """
    direct_clauses = []
    content_clauses = []
    role_clauses = []
    params: dict = {"concept_id": concept_id}

    for i, pat in enumerate(defn.get("direct_patterns", [])):
        pname = f"dp_{i}"
        params[pname] = pat
        direct_clauses.append(f"lower(label) LIKE :{pname} OR lower(summary) LIKE :{pname}")

    # content_patterns now also match against label (catches short-label neurons like "Queuing Theory")
    for i, pat in enumerate(defn.get("content_patterns", [])):
        pname = f"cp_{i}"
        params[pname] = pat
        content_clauses.append(
            f"lower(content) LIKE :{pname} OR lower(label) LIKE :{pname} OR lower(summary) LIKE :{pname}"
        )

    for i, role in enumerate(defn.get("role_filters", [])):
        pname = f"rf_{i}"
        params[pname] = role
        role_clauses.append(f"role_key = :{pname}")

    direct_expr = " OR ".join(direct_clauses) if direct_clauses else "false"
    content_expr = " OR ".join(content_clauses) if content_clauses else "false"
    role_expr = " OR ".join(role_clauses) if role_clauses else "false"
    all_expr = " OR ".join(filter(None, [
        f"({direct_expr})" if direct_clauses else None,
        f"({content_expr})" if content_clauses else None,
        f"({role_expr})" if role_clauses else None,
    ]))

    if not all_expr:
        return [], [], 0

    result = await db.execute(text(f"""
        SELECT id,
            CASE WHEN {direct_expr} THEN 'direct' ELSE 'content' END AS match_type
        FROM neurons
        WHERE is_active = true
          AND node_type != 'concept'
          AND id != :concept_id
          AND ({all_expr})
    """), params)
    matches = result.all()

    direct_ids = [r[0] for r in matches if r[1] == "direct"]
    content_ids = [r[0] for r in matches if r[1] == "content"]

    concept_label = defn.get("label")
    edges_created = 0
    if direct_ids:
        edges_created += await link_concept_to_neurons(db, concept_id, direct_ids, weight=0.5, concept_label=concept_label)
    if content_ids:
        edges_created += await link_concept_to_neurons(db, concept_id, content_ids, weight=0.3, concept_label=concept_label)

    return direct_ids, content_ids, edges_created


async def relink_existing_concepts(db: AsyncSession) -> dict:
    """Re-run pattern matching for all existing concept neurons.

    This catches neurons missed by the original seeding due to pattern gaps
    (e.g., content_patterns not matching against label field).
    New edges are created via upsert — existing edges keep their weight if higher.
    """
    results = []

    for defn in CONCEPT_DEFINITIONS:
        # Find existing concept neuron
        existing = await db.execute(
            select(Neuron).where(
                Neuron.node_type == "concept",
                Neuron.label == defn["label"],
            )
        )
        concept = existing.scalar_one_or_none()
        if not concept:
            continue

        direct_ids, content_ids, edges_created = await _match_and_link(db, concept.id, defn)
        if edges_created > 0:
            results.append({
                "concept_neuron_id": concept.id,
                "label": concept.label,
                "direct_matches": len(direct_ids),
                "content_matches": len(content_ids),
                "new_edges": edges_created,
            })

    if results:
        await db.commit()
        from app.services.adjacency_cache import invalidate_adjacency_cache
        from app.services.semantic_prefilter import invalidate_cache
        invalidate_adjacency_cache()
        invalidate_cache()

    return {
        "relinked": results,
        "total_concepts_updated": len(results),
        "total_new_edges": sum(r["new_edges"] for r in results),
    }


async def _seed_one_concept(
    db: AsyncSession,
    defn: dict,
) -> dict | None:
    """Seed a single concept neuron from its definition. Returns result dict or None if already exists."""

    # Check if already seeded (match on label)
    existing = await db.execute(
        select(Neuron).where(
            Neuron.node_type == "concept",
            Neuron.label == defn["label"],
        )
    )
    if existing.scalar_one_or_none():
        return None

    # Create concept neuron
    concept = await create_concept_neuron(
        db,
        label=defn["label"],
        content=defn["content"],
        summary=defn["summary"],
    )

    direct_ids, content_ids, edges_created = await _match_and_link(db, concept.id, defn)

    return {
        "concept_neuron_id": concept.id,
        "label": concept.label,
        "direct_matches": len(direct_ids),
        "content_matches": len(content_ids),
        "edges_created": edges_created,
    }


async def seed_all_concepts(
    db: AsyncSession,
    only: list[str] | None = None,
) -> dict:
    """Seed all (or specified) concept neurons from CONCEPT_DEFINITIONS.

    Args:
        only: If provided, only seed concepts whose labels are in this list.

    Returns dict with 'seeded' (list of results) and 'skipped' (already existing).
    """
    seeded = []
    skipped = []

    definitions = CONCEPT_DEFINITIONS
    if only:
        only_lower = {o.lower() for o in only}
        definitions = [d for d in definitions if d["label"].lower() in only_lower]

    for defn in definitions:
        result = await _seed_one_concept(db, defn)
        if result is None:
            skipped.append(f"{defn['label']} (already exists)")
        else:
            seeded.append(result)

    if seeded:
        await db.commit()
        # Authoritative graph and derived semantic projections both changed.
        from app.services.adjacency_cache import invalidate_adjacency_cache
        from app.services.semantic_prefilter import invalidate_cache
        invalidate_adjacency_cache()
        invalidate_cache()

    total_edges = sum(r["edges_created"] for r in seeded)
    return {
        "seeded": seeded,
        "skipped": skipped,
        "total_new_concepts": len(seeded),
        "total_skipped": len(skipped),
        "total_edges_created": total_edges,
    }
