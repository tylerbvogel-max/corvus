"""Operator maintenance sweeps over derived graph state.

Extracted from ``admin.py`` by roadmap record ``durability-file-size-seams``
(04c). These three endpoints share one job: sweep the whole graph and repair
state that is DERIVED rather than authored — edge existence, edge taxonomy,
and embedding vectors. None of them touch authored content, authority, scope,
or the supersession lifecycle; those change only through the ORM and the
action bus, where the evidence gates live.

This module is a governed DELETE writer. ``prune_edges`` is the entry in the
``delete`` list of ``architecture/graph_writers.json`` that ``admin.py`` used
to hold, and it moved here whole — the register was updated in the same
change that moved it, in both directions: this module was added and the stale
``admin.py`` entry was removed. A stale entry would silently reserve a slot a
future writer could inherit without review, which is exactly what
``test_register_has_no_stale_entries`` exists to catch.

``classify_edges`` carries the raw-SQL CASE that
``test_honeypot_a_comparison_inside_case_is_not_an_assignment`` pins as a
regression case: it reads ``src.department = tgt.department`` to CHOOSE an
edge_type, and a naive ``\\bcolumn\\s*=`` scan would call that a write to
``department``. The honeypot's pin was re-aimed here in the same change. The
two lived ~1300 lines apart in admin.py and are deliberately together now:
one file, one job, one register entry, one honeypot.
"""

import asyncio
import json

from fastapi import APIRouter, Depends
from sqlalchemy import bindparam, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Neuron, NeuronEdge, SystemState

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/prune-edges")
async def prune_edges(db: AsyncSession = Depends(get_db)):
    """Prune stale low-weight co-firing edges to control graph density."""
    from app.config import settings

    # Count before
    before = (await db.execute(select(func.count()).select_from(NeuronEdge))).scalar() or 0

    # Get current query count for staleness check
    state = (await db.execute(select(SystemState).where(SystemState.id == 1))).scalar_one_or_none()
    total_queries = state.total_queries if state else 0
    stale_threshold = total_queries - settings.edge_prune_stale_queries

    # Delete edges that have fired only once and are stale
    await db.execute(text(
        "DELETE FROM neuron_edges "
        "WHERE co_fire_count < :min_cofires AND last_updated_query < :stale"
    ), {"min_cofires": settings.edge_prune_min_cofires, "stale": max(0, stale_threshold)})

    # Also prune stale weak edges from JSONB
    min_c = settings.edge_prune_min_cofires
    await db.execute(text("""
        UPDATE neurons
        SET weak_edges = (
            SELECT jsonb_object_agg(key, value)
            FROM jsonb_each(weak_edges)
            WHERE (value->>'c')::int >= :min_c
        )
        WHERE weak_edges IS NOT NULL
    """), {"min_c": min_c})

    await db.commit()

    # Invalidate adjacency cache since edges were deleted
    from app.services.adjacency_cache import invalidate_adjacency_cache
    invalidate_adjacency_cache()

    after = (await db.execute(select(func.count()).select_from(NeuronEdge))).scalar() or 0

    return {
        "status": "pruned",
        "edges_before": before,
        "edges_after": after,
        "edges_removed": before - after,
    }


@router.post("/classify-edges")
async def classify_edges(db: AsyncSession = Depends(get_db)):
    """Classify existing co-firing edges as stellate (intra-department) or pyramidal (cross-department).

    Looks up the department of each edge's source and target neurons.
    Same department = stellate (local processor), different = pyramidal (long-range).
    """
    from app.services.mind_corpus import NON_CONDUCTING_EDGE_TYPES

    # Reclassify conducting edges only. The exclusion is the shared
    # non-conducting CLASS, not a hand-list: the original exclusion named
    # instantiates alone, predating the memory-semantics types, and so
    # overwrote 507 supersedes/evidence-link edges on the production graph
    # on 2026-08-01 (record fix-classify-edges-taxonomy). Deriving from
    # mind_corpus means a fifth non-conducting type cannot recreate that.
    result = await db.execute(text("""
        UPDATE neuron_edges e
        SET edge_type = CASE
            WHEN src.department = tgt.department THEN 'stellate'
            ELSE 'pyramidal'
        END
        FROM neurons src, neurons tgt
        WHERE e.source_id = src.id AND e.target_id = tgt.id
          AND (e.edge_type IS NULL
               OR e.edge_type NOT IN :non_conducting)
        RETURNING e.source_id, e.target_id, e.edge_type
    """).bindparams(bindparam("non_conducting", expanding=True)),
        {"non_conducting": list(NON_CONDUCTING_EDGE_TYPES)})
    rows = result.all()
    await db.commit()

    stellate = sum(1 for _, _, t in rows if t == "stellate")
    pyramidal = sum(1 for _, _, t in rows if t == "pyramidal")

    return {
        "classified": len(rows),
        "stellate": stellate,
        "pyramidal": pyramidal,
        "message": f"Classified {len(rows)} edges: {stellate} stellate, {pyramidal} pyramidal",
    }


@router.post("/embed-neurons")
async def embed_neurons(force: bool = False, db: AsyncSession = Depends(get_db)):
    """Generate semantic embeddings for all neurons.

    Runs sentence-transformers/all-MiniLM-L6-v2 locally to produce 384-dim
    vectors stored on each neuron. These enable semantic similarity scoring
    (cortical topography) instead of keyword matching.

    Pass force=true to re-embed neurons that already have embeddings.
    """
    import concurrent.futures
    from app.services.embedding_service import embed_batch

    # Load neurons needing embeddings
    if force:
        result = await db.execute(
            select(Neuron).where(Neuron.is_active == True)
        )
    else:
        result = await db.execute(
            select(Neuron).where(Neuron.is_active == True, Neuron.embedding.is_(None))
        )
    neurons = result.scalars().all()

    if not neurons:
        return {"embedded": 0, "message": "All neurons already have embeddings"}

    # Build text blobs: label + content (same text used for scoring)
    texts = []
    for n in neurons:
        parts = [n.label or ""]
        if n.content:
            parts.append(n.content[:1000])  # Cap content to avoid huge inputs
        texts.append(" ".join(parts).strip() or "empty")

    # Run embedding in thread pool (CPU-bound, don't block event loop)
    loop = asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        embeddings = await loop.run_in_executor(pool, embed_batch, texts)

    # Store embeddings
    for neuron, emb in zip(neurons, embeddings):
        neuron.embedding = json.dumps(emb)

    await db.commit()

    # Invalidate the semantic prefilter cache so it reloads with new embeddings
    from app.services.semantic_prefilter import invalidate_cache
    invalidate_cache()

    return {
        "embedded": len(neurons),
        "dimensions": len(embeddings[0]) if embeddings else 0,
        "message": f"Embedded {len(neurons)} neurons with 384-dim vectors",
    }
