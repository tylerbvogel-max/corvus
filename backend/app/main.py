"""FastAPI app with lifespan auto-seed."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from app.middleware.audit import AuditMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.middleware.access_gate import AccessGateMiddleware
from sqlalchemy import select, func, text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

from app.config import settings
from app.database import engine, async_session
from app.models import Base, Neuron, Engram, SystemState, BatchJob, SourceDocument, NeuronSourceLink, ManagementReview, ComplianceSnapshot, EvidenceMapping, ObservationQueue
from app.routers import query, neurons, admin, autopilot, performance, provenance, compliance, ingest, chat_sessions, engrams
from app.compliance.router import router as compliance_suite_router
from app.compliance.models import ComplianceSuiteRun, ComplianceProviderResult, ComplianceAttestation  # noqa: F401 — for create_all
from app.seed.loader import load_seed
from app.seed.regulatory_seed import seed_regulatory
from app.tenant import tenant


async def _column_exists(conn, table: str, column: str) -> bool:
    result = await conn.execute(text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = :table AND column_name = :col"
    ), {"table": table, "col": column})
    return result.fetchone() is not None


async def _index_exists(conn, index_name: str) -> bool:
    result = await conn.execute(text(
        "SELECT 1 FROM pg_indexes WHERE indexname = :name"
    ), {"name": index_name})
    return result.fetchone() is not None


async def _table_exists(conn, table: str) -> bool:
    result = await conn.execute(text(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = :table AND table_schema = 'public'"
    ), {"table": table})
    return result.fetchone() is not None


async def _migrate_refinements_and_config(engine):
    """Migrate refinements nullable constraint and autopilot_config columns."""
    async with engine.begin() as conn:
        try:
            if await _table_exists(conn, "neuron_refinements"):
                result = await conn.execute(text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'neuron_refinements' AND column_name = 'query_id'"
                ))
                row = result.fetchone()
                if row and row[0] == "NO":
                    await conn.execute(text(
                        "ALTER TABLE neuron_refinements ALTER COLUMN query_id DROP NOT NULL"
                    ))
                    print("Migrated: neuron_refinements.query_id is now nullable")
        except SQLAlchemyError as e:
            logger.warning("Migration check skipped: %s", e)

    async with engine.begin() as conn:
        try:
            if await _table_exists(conn, "autopilot_config"):
                if not await _column_exists(conn, "autopilot_config", "eval_model"):
                    await conn.execute(text(
                        "ALTER TABLE autopilot_config ADD COLUMN eval_model VARCHAR(20) DEFAULT 'haiku'"
                    ))
                    print("Migrated: added autopilot_config.eval_model")
                if not await _column_exists(conn, "autopilot_config", "max_layer"):
                    await conn.execute(text(
                        "ALTER TABLE autopilot_config ADD COLUMN max_layer INTEGER DEFAULT 5"
                    ))
                    print("Migrated: added autopilot_config.max_layer")
        except SQLAlchemyError as e:
            logger.warning("Autopilot migration skipped: %s", e)


def _abstraction_backfill_sql():
    """Backfill neurons.abstraction_type from node_type (ABSTRACTION_BY_NODE_TYPE)."""
    from app.models import ABSTRACTION_BY_NODE_TYPE
    when_clauses = " ".join(
        f"WHEN '{nt}' THEN '{abstraction}'"
        for nt, abstraction in ABSTRACTION_BY_NODE_TYPE.items()
    )
    return text(
        "UPDATE neurons SET abstraction_type = "
        f"CASE node_type {when_clauses} ELSE NULL END "
        "WHERE abstraction_type IS NULL"
    )


async def _migrate_neuron_and_query_columns(engine):
    """Migrate neuron table columns and queries.model_version."""
    async with engine.begin() as conn:
        try:
            if not await _column_exists(conn, "neurons", "cross_ref_departments"):
                await conn.execute(text(
                    "ALTER TABLE neurons ADD COLUMN cross_ref_departments TEXT"
                ))
                print("Migrated: added neurons.cross_ref_departments")
            if not await _column_exists(conn, "neurons", "standard_date"):
                await conn.execute(text(
                    "ALTER TABLE neurons ADD COLUMN standard_date VARCHAR(20)"
                ))
                print("Migrated: added neurons.standard_date")
            if not await _column_exists(conn, "neurons", "embedding"):
                await conn.execute(text(
                    "ALTER TABLE neurons ADD COLUMN embedding TEXT"
                ))
                print("Migrated: added neurons.embedding")
            if not await _column_exists(conn, "neurons", "authority_level"):
                await conn.execute(text(
                    "ALTER TABLE neurons ADD COLUMN authority_level VARCHAR(30)"
                ))
                print("Migrated: added neurons.authority_level")
            if not await _column_exists(conn, "neurons", "entities"):
                await conn.execute(text(
                    "ALTER TABLE neurons ADD COLUMN entities JSONB"
                ))
                print("Migrated: added neurons.entities")
            if not await _index_exists(conn, "ix_neurons_entities"):
                await conn.execute(text(
                    "CREATE INDEX ix_neurons_entities ON neurons "
                    "USING GIN (entities jsonb_path_ops)"
                ))
                print("Migrated: added ix_neurons_entities GIN index")
            if not await _index_exists(conn, "ix_neurons_content_tsv"):
                # Expression must match recall_lanes.TSV_EXPR or the planner
                # won't use it for the keyword lane.
                await conn.execute(text(
                    "CREATE INDEX ix_neurons_content_tsv ON neurons USING GIN "
                    "(to_tsvector('english', coalesce(label, '') || ' ' || "
                    "coalesce(content, '') || ' ' || coalesce(summary, '')))"
                ))
                print("Migrated: added ix_neurons_content_tsv full-text index")
            if not await _column_exists(conn, "neurons", "abstraction_type"):
                await conn.execute(text(
                    "ALTER TABLE neurons ADD COLUMN abstraction_type VARCHAR(20)"
                ))
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_neurons_abstraction_type "
                    "ON neurons(abstraction_type)"
                ))
                await conn.execute(_abstraction_backfill_sql())
                print("Migrated: added neurons.abstraction_type (backfilled from node_type)")
            if not await _column_exists(conn, "neurons", "centrality"):
                await conn.execute(text(
                    "ALTER TABLE neurons ADD COLUMN centrality FLOAT NOT NULL DEFAULT 0.0"
                ))
                print("Migrated: added neurons.centrality")
            if not await _column_exists(conn, "neurons", "visibility"):
                await conn.execute(text(
                    "ALTER TABLE neurons ADD COLUMN visibility VARCHAR(20)"
                ))
                print("Migrated: added neurons.visibility")
        except SQLAlchemyError as e:
            logger.warning("Neurons migration skipped: %s", e)

    async with engine.begin() as conn:
        try:
            if not await _table_exists(conn, "region_policies"):
                await conn.execute(text("""
                    CREATE TABLE region_policies (
                        id SERIAL PRIMARY KEY,
                        region VARCHAR(100) NOT NULL UNIQUE,
                        display_name VARCHAR(200),
                        description TEXT,
                        scoring_weights JSONB,
                        loop_config JSONB,
                        acl JSONB,
                        write_gate JSONB,
                        projection JSONB,
                        is_active BOOLEAN NOT NULL DEFAULT true,
                        created_at TIMESTAMP DEFAULT now(),
                        updated_at TIMESTAMP DEFAULT now()
                    )
                """))
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_region_policies_region "
                    "ON region_policies(region)"
                ))
                print("Migrated: created region_policies")
            if not await _column_exists(conn, "autopilot_config", "region"):
                await conn.execute(text(
                    "ALTER TABLE autopilot_config ADD COLUMN region VARCHAR(100)"
                ))
                print("Migrated: added autopilot_config.region")
            if not await _column_exists(conn, "integrity_findings", "region"):
                await conn.execute(text(
                    "ALTER TABLE integrity_findings ADD COLUMN region VARCHAR(100)"
                ))
                await conn.execute(text(
                    "CREATE INDEX IF NOT EXISTS ix_integrity_findings_region "
                    "ON integrity_findings(region)"
                ))
                print("Migrated: added integrity_findings.region")
        except SQLAlchemyError as e:
            logger.warning("Region policy migration skipped: %s", e)

    async with engine.begin() as conn:
        try:
            if not await _column_exists(conn, "queries", "model_version"):
                await conn.execute(text(
                    "ALTER TABLE queries ADD COLUMN model_version VARCHAR(100)"
                ))
                print("Migrated: added queries.model_version")
        except SQLAlchemyError as e:
            logger.warning("Query model_version migration skipped: %s", e)


async def _migrate_edge_columns(engine):
    """Migrate edge table columns: scaling indexes, types, provenance, context."""
    async with engine.begin() as conn:
        try:
            if await _table_exists(conn, "neuron_edges"):
                if not await _column_exists(conn, "neuron_edges", "last_updated_query"):
                    await conn.execute(text(
                        "ALTER TABLE neuron_edges ADD COLUMN last_updated_query INTEGER DEFAULT 0"
                    ))
                    print("Migrated: added neuron_edges.last_updated_query")
                if not await _index_exists(conn, "ix_neuron_edges_target_weight"):
                    await conn.execute(text(
                        "CREATE INDEX ix_neuron_edges_target_weight ON neuron_edges(target_id, weight)"
                    ))
                    await conn.execute(text(
                        "CREATE INDEX ix_neuron_edges_source_weight ON neuron_edges(source_id, weight)"
                    ))
                    print("Migrated: added neuron_edges target/source weight indexes")
                if not await _column_exists(conn, "neuron_edges", "edge_type"):
                    await conn.execute(text(
                        "ALTER TABLE neuron_edges ADD COLUMN edge_type VARCHAR(20) DEFAULT 'pyramidal'"
                    ))
                    print("Migrated: added neuron_edges.edge_type")
                if not await _column_exists(conn, "neuron_edges", "source"):
                    await conn.execute(text(
                        "ALTER TABLE neuron_edges ADD COLUMN source VARCHAR(20) DEFAULT 'organic'"
                    ))
                    print("Migrated: added neuron_edges.source")
                if not await _column_exists(conn, "neuron_edges", "last_adjusted"):
                    await conn.execute(text(
                        "ALTER TABLE neuron_edges ADD COLUMN last_adjusted TIMESTAMP DEFAULT now()"
                    ))
                    print("Migrated: added neuron_edges.last_adjusted")
                if not await _column_exists(conn, "neuron_edges", "context"):
                    await conn.execute(text(
                        "ALTER TABLE neuron_edges ADD COLUMN context VARCHAR(300)"
                    ))
                    print("Migrated: added neuron_edges.context")
        except SQLAlchemyError as e:
            logger.warning("Edge migration skipped: %s", e)


async def _migrate_obs_queue_columns(engine):
    """Migrate observation_queue eval columns."""
    async with engine.begin() as conn:
        try:
            if await _table_exists(conn, "observation_queue"):
                if not await _column_exists(conn, "observation_queue", "eval_json"):
                    await conn.execute(text(
                        "ALTER TABLE observation_queue ADD COLUMN eval_json TEXT"
                    ))
                    print("Migrated: added observation_queue.eval_json")
                if not await _column_exists(conn, "observation_queue", "eval_model"):
                    await conn.execute(text(
                        "ALTER TABLE observation_queue ADD COLUMN eval_model VARCHAR(20)"
                    ))
                    print("Migrated: added observation_queue.eval_model")
                if not await _column_exists(conn, "observation_queue", "eval_input_tokens"):
                    await conn.execute(text(
                        "ALTER TABLE observation_queue ADD COLUMN eval_input_tokens INTEGER DEFAULT 0"
                    ))
                    print("Migrated: added observation_queue.eval_input_tokens")
                if not await _column_exists(conn, "observation_queue", "eval_output_tokens"):
                    await conn.execute(text(
                        "ALTER TABLE observation_queue ADD COLUMN eval_output_tokens INTEGER DEFAULT 0"
                    ))
                    print("Migrated: added observation_queue.eval_output_tokens")
        except SQLAlchemyError as e:
            logger.warning("Observation queue eval migration skipped: %s", e)


async def _migrate_create_tables(engine):
    """Create new tables if missing: inhibitory_regulators, source_documents, etc."""
    async with engine.begin() as conn:
        try:
            if not await _table_exists(conn, "inhibitory_regulators"):
                await conn.execute(text("""
                    CREATE TABLE inhibitory_regulators (
                        id SERIAL PRIMARY KEY,
                        region_type VARCHAR(20) NOT NULL,
                        region_value VARCHAR(100) NOT NULL,
                        inhibition_strength FLOAT DEFAULT 0.5,
                        activation_threshold INTEGER DEFAULT 15,
                        max_survivors INTEGER DEFAULT 8,
                        redundancy_cosine_threshold FLOAT DEFAULT 0.92,
                        total_suppressions INTEGER DEFAULT 0,
                        total_activations INTEGER DEFAULT 0,
                        avg_post_suppression_utility FLOAT DEFAULT 0.5,
                        is_active BOOLEAN DEFAULT true,
                        created_at TIMESTAMP DEFAULT now()
                    )
                """))
                print("Migrated: created inhibitory_regulators table")
        except SQLAlchemyError as e:
            logger.warning("Inhibitory regulators migration skipped: %s", e)

    async with engine.begin() as conn:
        try:
            if not await _table_exists(conn, "source_documents"):
                await conn.execute(text("""
                    CREATE TABLE source_documents (
                        id SERIAL PRIMARY KEY,
                        canonical_id VARCHAR(100) UNIQUE NOT NULL,
                        family VARCHAR(50) NOT NULL,
                        version VARCHAR(50),
                        status VARCHAR(20) NOT NULL DEFAULT 'active',
                        authority_level VARCHAR(30) NOT NULL,
                        issuing_body VARCHAR(200),
                        effective_date DATE,
                        url VARCHAR(500),
                        notes TEXT,
                        superseded_by_id INTEGER REFERENCES source_documents(id),
                        created_at TIMESTAMP DEFAULT now()
                    )
                """))
                await conn.execute(text(
                    "CREATE INDEX ix_source_documents_family ON source_documents(family)"
                ))
                print("Migrated: created source_documents table")
        except SQLAlchemyError as e:
            logger.warning("Source documents migration skipped: %s", e)


async def _migrate_create_source_links(engine):
    """Create neuron_source_links table if missing."""
    async with engine.begin() as conn:
        try:
            if not await _table_exists(conn, "neuron_source_links"):
                await conn.execute(text("""
                    CREATE TABLE neuron_source_links (
                        id SERIAL PRIMARY KEY,
                        neuron_id INTEGER NOT NULL REFERENCES neurons(id),
                        source_document_id INTEGER NOT NULL REFERENCES source_documents(id),
                        derivation_type VARCHAR(30) NOT NULL DEFAULT 'references',
                        section_ref VARCHAR(200),
                        review_status VARCHAR(20) NOT NULL DEFAULT 'current',
                        flagged_at TIMESTAMP,
                        reviewed_at TIMESTAMP,
                        reviewed_by VARCHAR(100),
                        link_origin VARCHAR(20) NOT NULL DEFAULT 'auto_detected',
                        created_at TIMESTAMP DEFAULT now()
                    )
                """))
                await conn.execute(text(
                    "CREATE INDEX ix_neuron_source_links_neuron_id ON neuron_source_links(neuron_id)"
                ))
                await conn.execute(text(
                    "CREATE INDEX ix_neuron_source_links_source_document_id ON neuron_source_links(source_document_id)"
                ))
                print("Migrated: created neuron_source_links table")
        except SQLAlchemyError as e:
            logger.warning("Neuron source links migration skipped: %s", e)


async def _migrate_create_queue_and_profiles(engine):
    """Create observation_queue and project_profiles tables if missing."""
    async with engine.begin() as conn:
        try:
            if not await _table_exists(conn, "observation_queue"):
                await conn.execute(text("""
                    CREATE TABLE observation_queue (
                        id SERIAL PRIMARY KEY,
                        source VARCHAR(50) NOT NULL DEFAULT 'corvus',
                        user_id VARCHAR(100) NOT NULL DEFAULT 'anonymous',
                        observation_type VARCHAR(30) NOT NULL,
                        text TEXT NOT NULL,
                        entities_json TEXT DEFAULT '[]',
                        app_context VARCHAR(100),
                        project_path VARCHAR(500),
                        proposed_department VARCHAR(100),
                        proposed_role_key VARCHAR(100),
                        proposed_layer INTEGER DEFAULT 3,
                        similar_neuron_id INTEGER REFERENCES neurons(id),
                        similarity_score FLOAT,
                        status VARCHAR(20) NOT NULL DEFAULT 'queued',
                        created_neuron_id INTEGER REFERENCES neurons(id),
                        created_at TIMESTAMP DEFAULT now()
                    )
                """))
                print("Migrated: created observation_queue table")
        except SQLAlchemyError as e:
            logger.warning("Observation queue migration skipped: %s", e)

    async with engine.begin() as conn:
        try:
            if not await _table_exists(conn, "project_profiles"):
                await conn.execute(text("""
                    CREATE TABLE project_profiles (
                        id SERIAL PRIMARY KEY,
                        project_path VARCHAR(500) UNIQUE NOT NULL,
                        project_name VARCHAR(200),
                        neuron_relevance TEXT DEFAULT '{}',
                        query_count INTEGER DEFAULT 0,
                        last_query_at TIMESTAMP,
                        created_at TIMESTAMP DEFAULT now()
                    )
                """))
                print("Migrated: created project_profiles table")
        except SQLAlchemyError as e:
            logger.warning("Project profiles migration skipped: %s", e)


async def _create_suite_runs_table(engine):
    async with engine.begin() as conn:
        try:
            if not await _table_exists(conn, "compliance_suite_runs"):
                await conn.execute(text("""
                    CREATE TABLE compliance_suite_runs (
                        id SERIAL PRIMARY KEY,
                        started_at TIMESTAMPTZ NOT NULL,
                        completed_at TIMESTAMPTZ,
                        framework_filter VARCHAR(50),
                        provider_filter TEXT,
                        total_providers INTEGER DEFAULT 0,
                        passed INTEGER DEFAULT 0,
                        failed INTEGER DEFAULT 0,
                        skipped INTEGER DEFAULT 0,
                        duration_ms INTEGER DEFAULT 0,
                        triggered_by VARCHAR(50) DEFAULT 'manual',
                        created_at TIMESTAMPTZ DEFAULT now()
                    )
                """))
                print("Migrated: created compliance_suite_runs table")
        except SQLAlchemyError as e:
            logger.warning("compliance_suite_runs migration skipped: %s", e)


async def _create_provider_results_table(engine):
    async with engine.begin() as conn:
        try:
            if not await _table_exists(conn, "compliance_provider_results"):
                await conn.execute(text("""
                    CREATE TABLE compliance_provider_results (
                        id SERIAL PRIMARY KEY,
                        run_id INTEGER NOT NULL REFERENCES compliance_suite_runs(id),
                        provider_id VARCHAR(100) NOT NULL,
                        passed BOOLEAN NOT NULL,
                        detail TEXT,
                        duration_ms INTEGER DEFAULT 0,
                        collected_at TIMESTAMPTZ DEFAULT now()
                    )
                """))
                await conn.execute(text("CREATE INDEX ix_cpr_run_id ON compliance_provider_results(run_id)"))
                await conn.execute(text("CREATE INDEX ix_cpr_provider_id ON compliance_provider_results(provider_id)"))
                print("Migrated: created compliance_provider_results table")
        except SQLAlchemyError as e:
            logger.warning("compliance_provider_results migration skipped: %s", e)


async def _create_attestations_table(engine):
    async with engine.begin() as conn:
        try:
            if not await _table_exists(conn, "compliance_attestations"):
                await conn.execute(text("""
                    CREATE TABLE compliance_attestations (
                        id SERIAL PRIMARY KEY,
                        provider_id VARCHAR(100) NOT NULL,
                        attested_by VARCHAR(200) NOT NULL,
                        attested_at TIMESTAMPTZ NOT NULL,
                        re_attestation_due TIMESTAMPTZ,
                        notes TEXT,
                        superseded_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ DEFAULT now()
                    )
                """))
                await conn.execute(text("CREATE INDEX ix_ca_provider_id ON compliance_attestations(provider_id)"))
                print("Migrated: created compliance_attestations table")
        except SQLAlchemyError as e:
            logger.warning("compliance_attestations migration skipped: %s", e)


async def _migrate_compliance_timestamps(engine):
    async with engine.begin() as conn:
        try:
            for tbl, cols in [
                ("compliance_suite_runs", ["started_at", "completed_at", "created_at"]),
                ("compliance_provider_results", ["collected_at"]),
                ("compliance_attestations", ["attested_at", "re_attestation_due", "superseded_at", "created_at"]),
            ]:
                for col in cols:
                    await conn.execute(text(
                        f"ALTER TABLE {tbl} ALTER COLUMN {col} TYPE TIMESTAMPTZ USING {col} AT TIME ZONE 'UTC'"
                    ))
        except SQLAlchemyError:
            pass  # Already migrated or table doesn't exist yet

    async with engine.begin() as conn:
        try:
            await conn.execute(text(
                "ALTER TABLE compliance_suite_runs ADD COLUMN provider_filter TEXT"
            ))
            print("Migrated: added provider_filter column to compliance_suite_runs")
        except SQLAlchemyError:
            pass  # Already exists


async def _migrate_compliance_suite_tables(engine):
    """Create compliance suite tables if missing: suite_runs, provider_results, attestations."""
    await _create_suite_runs_table(engine)
    await _create_provider_results_table(engine)
    await _create_attestations_table(engine)
    await _migrate_compliance_timestamps(engine)


async def _run_migrations(engine):
    """Run all schema migrations: column additions then table creations."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await _migrate_refinements_and_config(engine)
    await _migrate_neuron_and_query_columns(engine)
    await _migrate_edge_columns(engine)
    await _migrate_obs_queue_columns(engine)
    await _migrate_create_tables(engine)
    await _migrate_create_source_links(engine)
    await _migrate_create_queue_and_profiles(engine)
    await _migrate_compliance_suite_tables(engine)


async def _seed_core_data():
    """Auto-seed neurons, regulatory data, and clean up interrupted batch jobs."""
    import asyncio

    # Auto-seed on first run
    async with async_session() as db:
        count = (await db.execute(select(func.count(Neuron.id)))).scalar() or 0
        if count == 0:
            result = await load_seed(db)
            print(f"Auto-seeded: {result}")

    # Seed regulatory department — force re-seed if neuron count below v2 threshold
    async with async_session() as db:
        rcount = (await db.execute(
            select(func.count(Neuron.id)).where(Neuron.department == tenant.regulatory_department_name)
        )).scalar() or 0
        force_reseed = rcount < tenant.reseed_threshold
        if force_reseed:
            print(f"Regulatory neuron count ({rcount}) below v2 threshold — will force re-seed")

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


async def _auto_embed_neurons():
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


async def _seed_engrams():
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


async def _seed_compliance():
    """Seed evidence mappings and take a compliance snapshot if due."""
    # Auto-seed evidence mappings if table is empty
    async with async_session() as db:
        try:
            ev_count = (await db.execute(select(func.count(EvidenceMapping.id)))).scalar() or 0
            if ev_count == 0:
                from app.routers.compliance import _seed_evidence_data
                result = await _seed_evidence_data(db)
                print(f"Auto-seeded evidence mappings: {result}")
        except (SQLAlchemyError, ImportError) as e:
            logger.warning("Evidence map seed skipped: %s", e)

    # Auto-snapshot compliance if none exists or last is >7 days old
    try:
        from app.routers.compliance import maybe_auto_snapshot
        await maybe_auto_snapshot()
    except (SQLAlchemyError, ImportError) as e:
        logger.warning("Auto-snapshot skipped: %s", e)


def _cleanup_llm_session_transcripts() -> None:
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


async def _preload_hot_caches():
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
    if settings.spread_enabled:
        from app.services.adjacency_cache import ensure_adjacency_loaded, get_adjacency_csr
        async with async_session() as db:
            await ensure_adjacency_loaded(db)
        get_adjacency_csr()
    print(f"Hot caches preloaded in {time.monotonic() - t0:.1f}s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _run_migrations(engine)
    # Load compliance suite registry (frameworks + providers)
    from app.compliance.registry import load_all as load_compliance_registry
    load_compliance_registry()
    # Register action bus handlers (AIP governance roadmap, pattern #1)
    from app.services.actions.init_registry import init_actions_registry
    init_actions_registry()
    await _seed_core_data()
    await _auto_embed_neurons()
    await _seed_engrams()
    await _seed_compliance()
    await _preload_hot_caches()
    _cleanup_llm_session_transcripts()
    # AIP Phase 1.5 GTM-B: start remote MCP session manager
    from app.mcp_http import mcp_lifespan
    async with mcp_lifespan():
        yield


app = FastAPI(
    title=tenant.display_name,
    description=tenant.description,
    version="0.1.0",
    lifespan=lifespan,
)

# CORS origins: from CORS_ORIGINS env var, or auto-derived from PORT
_cors_origins = (
    [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if settings.cors_origins
    else [f"http://localhost:5173", f"http://localhost:{settings.port}"]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Security headers middleware — defense-in-depth headers on all responses
# Addresses: NIST 800-53 AC-12/SC-10/SC-28, CMMC 3.1.11/3.13.9, SOC 2 CC6.1
app.add_middleware(SecurityHeadersMiddleware)

# Access gate middleware — shared key authentication when CORVUS_ACCESS_KEY is set
app.add_middleware(AccessGateMiddleware)

# Audit logging middleware — logs all POST/PUT/DELETE/PATCH to audit_log table
# Addresses: NIST 800-53 AU-2/AU-3/AU-12, CMMC 3.3.1, SOC 2 CC7.2
app.add_middleware(AuditMiddleware)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch all unhandled exceptions and return sanitized JSON error.

    SI-11: Error messages must not reveal system implementation details.
    """
    if isinstance(exc, HTTPException):
        raise exc
    status = 504 if "timed out" in str(exc).lower() else 500
    # Log the real error server-side, return generic message to client
    logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=status,
        content={"detail": "Internal server error"},
    )


from app.routers import recall as recall_router
from app.routers import capabilities as capabilities_router
from app.routers import roadmap_ledgers as roadmap_ledgers_router
from app.routers import distill as distill_router
from app.routers import janitor as janitor_router
from app.routers import compile as compile_router
from app.routers import mind_metrics as mind_metrics_router
from app.routers import auditor as auditor_router

app.include_router(query.router)
app.include_router(recall_router.router)
app.include_router(capabilities_router.router)
app.include_router(roadmap_ledgers_router.router)
app.include_router(distill_router.router)
app.include_router(janitor_router.router)
app.include_router(compile_router.router)
app.include_router(mind_metrics_router.router)
app.include_router(auditor_router.router)
app.include_router(neurons.router)
app.include_router(admin.router)
app.include_router(autopilot.router)
from app.routers import proposals
app.include_router(proposals.router)
app.include_router(proposals.provenance_router)
app.include_router(performance.router)
app.include_router(provenance.router)
app.include_router(compliance.router)
app.include_router(ingest.router)
app.include_router(chat_sessions.router)
app.include_router(engrams.router)
app.include_router(compliance_suite_router)
from app.routers import document_ingest
app.include_router(document_ingest.router)
from app.routers import reference as reference_router
app.include_router(reference_router.router)
from app.routers import integrity
app.include_router(integrity.router)
from app.routers import seeding
app.include_router(seeding.router)
from app.routers import regions
app.include_router(regions.router)
from app.routers import tool_definitions
app.include_router(tool_definitions.router)
from app.routers import lineage
app.include_router(lineage.router)
from app.routers import v1
app.include_router(v1.router)
from app.routers import eval_runs as eval_runs_router
app.include_router(eval_runs_router.router)
# AIP Phase 4 Pattern #208: agent registry + run history surface.
from app.routers import agents as agents_router
app.include_router(agents_router.router)
# AIP Phase 1.5 GTM-B: remote MCP transport at /mcp.
# Mount as raw ASGI — the MCP session manager writes the full HTTP
# response itself, so we cannot use a request-handler-style route
# (would double-send). `app.mount` binds the path prefix to the ASGI
# app directly without any request wrapping. Mount's path regex only
# matches ``/mcp/`` (trailing slash + suffix), so we add a method-
# agnostic 307 redirect from ``/mcp`` → ``/mcp/`` for MCP clients that
# configure the bare URL.
from fastapi.responses import RedirectResponse
from app.mcp_http import mcp_asgi_endpoint
app.mount("/mcp", mcp_asgi_endpoint)


@app.api_route("/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
async def _mcp_slash_redirect() -> RedirectResponse:
    """Preserve method (307) when forwarding bare ``/mcp`` to ``/mcp/``."""
    return RedirectResponse(url="/mcp/", status_code=307)


@app.get("/tenant")
async def get_tenant():
    """Return tenant configuration for the frontend."""
    return {
        "tenant_id": tenant.tenant_id,
        "display_name": tenant.display_name,
        "description": tenant.description,
        "seed_prompts": tenant.seed_prompts,
        "memory_surface": tenant.memory_surface_enabled,
    }


@app.get("/tenants")
async def list_tenants():
    """Return all available tenants with their default URLs."""
    import yaml as _yaml

    tenants_dir = Path(__file__).resolve().parent.parent / "tenants"
    result = []
    for td in sorted(tenants_dir.iterdir()):
        yaml_path = td / "tenant.yaml"
        if not td.is_dir() or not yaml_path.exists():
            continue
        with open(yaml_path) as f:
            cfg = _yaml.safe_load(f)
        result.append({
            "tenant_id": td.name,
            "display_name": cfg.get("display_name", td.name),
            "default_port": cfg.get("default_port"),
        })
    return result


@app.get("/health")
async def health():
    """Return system health status with neuron count and total queries."""
    async with async_session() as db:
        neuron_count = (await db.execute(select(func.count(Neuron.id)))).scalar() or 0
        state = (await db.execute(select(SystemState).where(SystemState.id == 1))).scalar_one_or_none()
        total_queries = state.total_queries if state else 0

    return {
        "status": "ok",
        "neuron_count": neuron_count,
        "total_queries": total_queries,
    }


# Serve frontend static files if built
frontend_dist = Path(__file__).parent.parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    assets_dir = frontend_dist / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    # SPA catch-all — must NOT match API prefixes
    _api_prefixes = ("/neurons", "/queries", "/query", "/context", "/eval-scores", "/admin", "/health", "/tenant", "/tenants", "/docs", "/openapi", "/ingest", "/models", "/chat", "/learning-analytics", "/roadmap-ledgers", "/v1", "/mcp")

    def _is_api_path(path: str) -> bool:
        return bool(path) and any(path.startswith(p.lstrip("/")) for p in _api_prefixes)

    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        """Serve the SPA frontend, falling back to index.html for client-side routes."""
        # Serve real static files first (logos, images, etc.)
        if full_path:
            file_path = frontend_dist / full_path
            if file_path.exists() and file_path.is_file():
                return FileResponse(str(file_path))
        # Never intercept API paths — let them 404 naturally
        if _is_api_path(full_path):
            raise HTTPException(status_code=404, detail="Not found")
        # SPA fallback
        return FileResponse(str(frontend_dist / "index.html"))
