"""Lifespan steps, lifted out of ``app.main`` so the factory can select them.

Every function here is referenced by name from
:data:`app.composition.capabilities.STARTUP_STEPS` and imported lazily by the
factory. Bodies are unchanged from the pre-factory ``app.main``; the only new
property is that a step whose requiring capabilities are all disabled is never
imported, so its transitive dependencies are never constructed.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.database import async_session
from app.models import BatchJob, Neuron
from app.tenant import tenant

logger = logging.getLogger(__name__)


@asynccontextmanager
async def canonical_startup_lock(engine):
    """Serialize seed mutations across horizontally scaled workers.

    Uvicorn lifespan runs once per worker. PostgreSQL session advisory locks
    turn that otherwise-concurrent initialization into a tenant-database
    critical section; the lock is released automatically if a worker dies.
    """
    async with engine.connect() as lock_connection:
        await lock_connection.execute(text(
            "SELECT pg_advisory_lock("
            "hashtext('corvus-canonical-startup'), hashtext(current_database()))"
        ))
        try:
            yield
        finally:
            await lock_connection.execute(text(
                "SELECT pg_advisory_unlock("
                "hashtext('corvus-canonical-startup'), hashtext(current_database()))"
            ))


def _expected_regulatory_seed_count(tree: list) -> int:
    """Count the department/role/task/system rows represented by a seed tree."""
    expected = 1  # department anchor exists even when a memory tenant has no tree
    for _role_label, _role_key, _refs, _date, tasks in tree:
        expected += 1
        for task_entry in tasks:
            systems = task_entry[4]
            expected += 1 + len(systems)
    return expected


async def seed_core_data():
    """Auto-seed neurons, regulatory data, and clean up interrupted batch jobs."""
    import asyncio

    from app.seed.loader import load_seed
    from app.seed.regulatory_seed import seed_regulatory

    # Auto-seed on first run
    async with async_session() as db:
        count = (await db.execute(select(func.count(Neuron.id)))).scalar() or 0
        if count == 0:
            result = await load_seed(db)
            print(f"Auto-seeded: {result}")

    # Seed regulatory department. Completeness comes from the tenant's actual
    # tree, not a knowledge-tenant threshold: memory tenants intentionally have
    # an empty tree (one department anchor), so a fixed threshold caused every
    # worker restart to delete and recreate that valid row and its history.
    async with async_session() as db:
        rcount = (await db.execute(
            select(func.count(Neuron.id)).where(Neuron.department == tenant.regulatory_department_name)
        )).scalar() or 0
        expected_count = _expected_regulatory_seed_count(tenant.regulatory_tree)
        force_reseed = 0 < rcount < expected_count
        if force_reseed:
            print(
                f"Regulatory neuron count ({rcount}) below tenant seed "
                f"shape ({expected_count}) — will force re-seed"
            )

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: seed_regulatory(force=force_reseed))

    # Mark any batch jobs that were "running" as "interrupted" (server restarted mid-ingest)
    async with async_session() as db:
        result = await db.execute(
            select(BatchJob).where(BatchJob.status == "running")
        )
        interrupted = result.scalars().all()
        for job in interrupted:
            job.status = "interrupted"
            job.step = f"Interrupted at chunk {job.current_chunk}/{job.total_chunks} (server restart)"
        if interrupted:
            await db.commit()
            print(f"Marked {len(interrupted)} batch job(s) as interrupted")


async def auto_embed_neurons():
    """Embed any neurons missing embeddings on startup using batch encoding."""
    import asyncio
    import json

    async with async_session() as db:
        rows = (await db.execute(
            select(Neuron).where(
                Neuron.is_active == True,  # noqa: E712
                (Neuron.embedding == None) | (Neuron.embedding == ""),  # noqa: E711
            )
        )).scalars().all()

        if not rows:
            return

        print(f"Auto-embedding {len(rows)} neurons missing embeddings...")

        from app.services.embedding_service import embed_batch

        texts = [f"{n.label} {n.content or ''}"[:1000] for n in rows]
        loop = asyncio.get_running_loop()
        vectors = await loop.run_in_executor(None, embed_batch, texts)

        for neuron, vec in zip(rows, vectors):
            neuron.embedding = json.dumps(vec)

        await db.commit()
        print(f"Auto-embedded {len(rows)} neurons")

    try:
        from app.services.semantic_prefilter import invalidate_cache
        invalidate_cache()
    except (ImportError, Exception) as e:
        logger.warning("Semantic cache invalidation skipped: %s", e)


async def seed_engrams():
    """Seed engrams from tenant config and auto-embed."""
    from app.seed.engram_loader import load_engram_seeds, auto_embed_engrams

    async with async_session() as db:
        result = await load_engram_seeds(db)
        if result["status"] == "seeded":
            print(f"Seeded {result['count']} engrams")

    async with async_session() as db:
        embedded = await auto_embed_engrams(db)
        if embedded > 0:
            print(f"Auto-embedded {embedded} engrams")


def cleanup_llm_session_transcripts() -> None:
    """Prune old persisted-chat CLI transcripts (~/.claude/projects/-tmp).

    Hero-chat session persistence stores one JSONL transcript per
    conversation in the CLI's session store for cwd=/tmp. They are only
    needed while a conversation can still be resumed; prune anything older
    than the TTL at startup so the store doesn't grow unbounded.
    """
    import time
    from pathlib import Path

    ttl_days = settings.chat_session_transcript_ttl_days
    if ttl_days <= 0:
        return
    store = Path.home() / ".claude" / "projects" / "-tmp"
    if not store.is_dir():
        return
    cutoff = time.time() - ttl_days * 86400
    removed = 0
    for f in sorted(store.glob("*.jsonl"))[:5000]:  # bounded sweep (JPL-2)
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            continue
    if removed:
        print(f"Pruned {removed} expired chat-session transcript(s)")


async def preload_hot_caches():
    """Warm every lazily-loaded hot-path dependency at startup.

    Without this, the FIRST query after a restart pays the full load chain
    inside its own latency (measured 17-39s in stage telemetry): BERT
    embedder ~2-5s, semantic cache, adjacency cache + CSR build. Startup
    absorbs the cost once instead. Gated by settings.preload_on_startup
    (disable for fast dev-reload cycles).
    """
    import time
    if not settings.preload_on_startup:
        return
    t0 = time.monotonic()
    # 1. Sentence-transformer model (sync load; startup is single-threaded)
    from app.services.embedding_service import _get_model
    _get_model()
    # 2. Semantic prefilter cache (neuron + engram embeddings)
    from app.services.semantic_prefilter import ensure_cache_loaded
    async with async_session() as db:
        await ensure_cache_loaded(db)
    # 3. Adjacency cache + CSR view (spread activation / derived hop cap)
    if (
        settings.spread_enabled
        and settings.cache_coherence_mode == "process-local"
    ):
        from app.services.adjacency_cache import ensure_adjacency_loaded, get_adjacency_csr
        async with async_session() as db:
            await ensure_adjacency_loaded(db)
        get_adjacency_csr()
    print(f"Hot caches preloaded in {time.monotonic() - t0:.1f}s")
