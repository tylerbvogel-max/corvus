"""Real PostgreSQL checkpoint regressions, opt-in and disposable targets only.

Apply Alembic migrations first. No schema creation fallback or provider calls.
CORVUS_TEST_DATABASE_URL must explicitly name a corvus_test_ / corvus_migration_
database. The regular provider-free unit lane skips these isolated DB cases.
"""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import DistillationCheckpoint, Neuron
from app.services import distillation_progress as progress
from app.services.distillation_inputs import source_id


pytestmark = pytest.mark.skipif(
    not os.environ.get("CORVUS_TEST_DATABASE_URL"),
    reason="requires an explicitly disposable, migrated PostgreSQL target",
)


def event_log(path, events=None):
    events = events or [{"event": "Stop", "distill_ready": True}]
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


@pytest_asyncio.fixture
async def case(tmp_path, monkeypatch):
    url = make_url(os.environ["CORVUS_TEST_DATABASE_URL"])
    if url.drivername != "postgresql+asyncpg" or not (url.database or "").startswith(
        ("corvus_test_", "corvus_migration_")
    ):
        pytest.fail("Refusing a non-disposable database target")
    engine = create_async_engine(url, poolclass=NullPool)
    from app.services import action_bus, mind_corpus

    # Each test gets startup-owned registry state; monkeypatch restores the
    # prior instance afterwards without weakening duplicate-registration checks.
    monkeypatch.setattr(action_bus, "_action_registry", action_bus._ActionRegistry())
    monkeypatch.setattr(mind_corpus, "ACTIONS_LOG", str(tmp_path / "actions.jsonl"))
    monkeypatch.setattr(progress, "stage_lesson_enrichment", AsyncMock())
    log = tmp_path / "synthetic.jsonl"
    event_log(log)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("CREATE TEMP TABLE checkpoint_probe (effects integer NOT NULL)"))
            await connection.execute(text("INSERT INTO checkpoint_probe VALUES (0)"))
            await connection.commit()
            async with AsyncSession(bind=connection, expire_on_commit=False) as db:
                calls = []

                async def stage(session, frozen_path, **context):
                    calls.append({"events": [json.loads(line) for line in
                                             Path(frozen_path).read_text().splitlines()], **context})
                    await session.execute(text("UPDATE checkpoint_probe SET effects = effects + 1"))
                    return {"session_id": "synthetic", "saved": 1, "neuron_ids": []}

                yield db, engine, log, stage, calls
                await db.rollback()
    finally:
        await engine.dispose()


async def effects(db):
    return await db.scalar(text("SELECT effects FROM checkpoint_probe"))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [OSError, asyncio.CancelledError])
async def test_precommit_failure_rolls_back_effect_and_checkpoint(case, failure):
    db, engine, log, stage, calls = case

    async def fail(session, *args, **kwargs):
        await stage(session, *args, **kwargs)
        raise failure("synthetic precommit interruption")

    with pytest.raises(failure):
        await progress.run_checkpointed(db, str(log), fail)
    assert await effects(db) == 0
    assert await db.get(DistillationCheckpoint, source_id(str(log))) is None
    await progress.run_checkpointed(db, str(log), stage)
    assert await effects(db) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [OSError, asyncio.CancelledError])
async def test_postcommit_marker_failure_never_repeats_memory_writes(case, monkeypatch, failure):
    db, engine, log, stage, calls = case
    original = progress._write_marker

    def fail(*args):
        raise failure("synthetic postcommit interruption")

    monkeypatch.setattr(progress, "_write_marker", fail)
    with pytest.raises(failure):
        await progress.run_checkpointed(db, str(log), stage)
    assert await effects(db) == 1
    async with AsyncSession(engine) as reader:
        row = await reader.get(DistillationCheckpoint, source_id(str(log)))
        assert row.state["receipt"]["saved"] == 1
    monkeypatch.setattr(progress, "_write_marker", original)
    assert (await progress.run_checkpointed(db, str(log), stage))["recovered"] is True
    assert await effects(db) == 1
    assert len(calls) == 1
    assert (await progress.progress_status(db, str(log.parent), 0))["ready"] == 0


@pytest.mark.asyncio
async def test_append_advances_once_without_replaying_committed_events(case):
    db, engine, log, stage, calls = case
    await progress.run_checkpointed(db, str(log), stage)
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": "Stop", "distill_ready": True, "new": True}) + "\n")
    assert (await progress.progress_status(db, str(log.parent), 0))["ready"] == 1
    await progress.run_checkpointed(db, str(log), stage)
    await progress.run_checkpointed(db, str(log), stage)
    assert await effects(db) == 2
    assert len(calls) == 2
    assert calls[1]["events"] == [{"event": "Stop", "distill_ready": True, "new": True,
                                   "transcript_path": calls[1]["events"][0]["transcript_path"]}]


@pytest.mark.asyncio
async def test_concurrent_connection_cannot_enter_same_source(case):
    db, engine, log, stage, calls = case
    entered, release = asyncio.Event(), asyncio.Event()

    async def paused(session, *args, **kwargs):
        result = await stage(session, *args, **kwargs)
        entered.set()
        await release.wait()
        return result

    first = asyncio.create_task(progress.run_checkpointed(db, str(log), paused))
    contender = AsyncMock()
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        async with AsyncSession(engine) as second:
            with pytest.raises(RuntimeError, match="source-busy"):
                await progress.run_checkpointed(second, str(log), contender)
        contender.assert_not_awaited()
    finally:
        release.set()
        await asyncio.wait_for(first, timeout=5)
    assert await effects(db) == 1


@pytest.mark.asyncio
async def test_enrichment_failure_preserves_memory_and_retries_only_pending_work(case, monkeypatch):
    db, engine, log, stage, calls = case

    async def needs_enrichment(*args, **kwargs):
        return {**await stage(*args, **kwargs), "neuron_ids": [123]}

    enrich = AsyncMock(side_effect=[OSError("synthetic embedding failure"), None])
    monkeypatch.setattr(progress, "stage_lesson_enrichment", enrich)
    with pytest.raises(OSError):
        await progress.run_checkpointed(db, str(log), needs_enrichment)
    assert await effects(db) == 1
    assert (await progress.progress_status(db, str(log.parent), 0))["ready"] == 1
    await progress.run_checkpointed(db, str(log), needs_enrichment)
    assert len(calls) == 1
    assert enrich.await_count == 2
    row = await db.get(DistillationCheckpoint, source_id(str(log)), populate_existing=True)
    assert row.state["pending"] is False


@pytest.mark.asyncio
async def test_action_delivery_survives_lost_ack_without_duplicate_record(case, monkeypatch):
    db, engine, log, stage, calls = case
    deliver = progress._deliver_action
    attempts = []

    async def with_action(*args, **kwargs):
        return {**await stage(*args, **kwargs), "_actions": [
            {"action": "attribution.reward", "detail": {"neuron_id": 123}}]}

    def lost_ack(*args):
        deliver(*args)
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("synthetic lost acknowledgement")

    monkeypatch.setattr(progress, "_deliver_action", lost_ack)
    with pytest.raises(OSError):
        await progress.run_checkpointed(db, str(log), with_action)
    await progress.run_checkpointed(db, str(log), with_action)
    records = [json.loads(line) for line in (log.parent / "actions.jsonl").read_text().splitlines()
               if line.strip()]
    assert len(records) == 1
    assert records[0]["distillation_event_id"]
    assert len(calls) == 1
    assert await effects(db) == 1


@pytest.mark.asyncio
async def test_legacy_marker_is_preserved_and_explicitly_blocked(case):
    db, engine, log, stage, calls = case
    marker = Path(str(log) + ".distilled")
    marker.write_text('{"events": 1}', encoding="utf-8")
    status = await progress.progress_status(db, str(log.parent), 0)
    assert status["ready"] == 0 and status["blocked"] == 1
    assert status["blocked_inputs"][0]["reason"] == "legacy-boundary-unverified"
    with pytest.raises(ValueError, match="legacy"):
        await progress.run_checkpointed(db, str(log), stage)
    assert marker.read_text() == '{"events": 1}'
    assert not calls


@pytest.mark.asyncio
async def test_changed_prefix_cannot_advance_or_repeat_effects(case):
    db, engine, log, stage, calls = case
    await progress.run_checkpointed(db, str(log), stage)
    event_log(log, [{"event": "Stop", "distill_ready": True, "rewritten": True}])
    status = await progress.progress_status(db, str(log.parent), 0)
    assert status["ready"] == 0 and status["blocked"] == 1
    with pytest.raises(ValueError, match="prefix-changed"):
        await progress.run_checkpointed(db, str(log), stage)
    assert await effects(db) == 1
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_real_distiller_keeps_auto_queue_and_attribution_once_on_marker_retry(case, monkeypatch):
    db, engine, log, stage, calls = case
    from app.services import distiller, lesson_store, llm_provider
    from app.services.actions.init_registry import init_actions_registry

    init_actions_registry()
    monkeypatch.setattr(lesson_store, "_nearest_active_lesson", AsyncMock(return_value=None))
    namespace = uuid4().hex
    seed = await lesson_store.save_lesson(
        db, lesson="Synthetic observed behavior.", evidence="Synthetic fixture.",
        label=f"Synthetic attribution target {namespace}", scope="Environment", commit=False,
        future_use="Measure attribution retry integrity.", likely_queries="Was attribution repeated?")
    await db.commit()
    target = await db.get(Neuron, seed["neuron_id"])
    before = target.avg_utility or 0.5
    event_log(log, [{"event": "Injection", "labels": [target.label], "neuron_ids": [target.id]},
                    {"event": "Stop", "distill_ready": True}])
    candidates = [
        {"label": f"Synthetic {scope} checkpoint lesson {namespace}", "scope": scope,
         "lesson": "Synthetic fixture demonstrated a transaction boundary.",
         "evidence": "Synthetic verified event.", "future_use": "Inspect transaction integrity.",
         "likely_queries": "Does checkpoint retry duplicate a lesson?"}
        for scope in ("Environment", "Assistant")
    ]
    provider = AsyncMock(return_value={"text": json.dumps({
        "lessons": candidates,
        "attributions": [{"label": target.label, "verdict": "load_bearing", "evidence": "Synthetic result."}],
        "recurrences": []})})
    monkeypatch.setattr(llm_provider, "llm_chat", provider)
    marker = progress._write_marker

    def fail(*args):
        raise OSError("synthetic marker failure")

    monkeypatch.setattr(progress, "_write_marker", fail)
    with pytest.raises(OSError):
        await distiller.distill_log(db, str(log))
    first_count = await db.scalar(text("SELECT count(*) FROM autopilot_proposals WHERE gap_source = 'distiller'"))
    monkeypatch.setattr(progress, "_write_marker", marker)
    result = await distiller.distill_log(db, str(log))
    assert result["saved"] == 2 and result["queued"] == 1
    assert result["recovered"] is True
    assert await db.scalar(text("SELECT count(*) FROM autopilot_proposals WHERE gap_source = 'distiller'")) == first_count
    target = await db.get(Neuron, seed["neuron_id"], populate_existing=True)
    assert target.avg_utility == pytest.approx(min(distiller.ATTRIBUTION_CAP, before + distiller.ATTRIBUTION_REWARD))
    assert len((await db.execute(select(Neuron).where(Neuron.label.in_([c["label"] for c in candidates])))).scalars().all()) == 1
    provider.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_first", [False, True])
async def test_real_enrichment_commits_vectors_and_edges_without_staged_cache_publication(
        case, monkeypatch, fail_first):
    db, engine, log, stage, calls = case
    from app.services import embedding_service, lesson_store, semantic_prefilter
    from app.services.actions.init_registry import init_actions_registry

    init_actions_registry()
    monkeypatch.setattr(lesson_store, "_nearest_active_lesson", AsyncMock(return_value=None))
    monkeypatch.setattr(progress, "stage_lesson_enrichment", lesson_store.stage_lesson_enrichment)
    publish = AsyncMock()
    monkeypatch.setattr(semantic_prefilter, "update_cache_incremental", publish)
    vector = [1.0] + [0.0] * 383
    namespace = uuid4().hex

    async def save(label):
        return await lesson_store.save_lesson(
            db, lesson="Synthetic enrichment evidence.", evidence="Synthetic fixture.",
            label=f"{label} {namespace}", scope="Environment", commit=False,
            future_use="Inspect committed recall projections.", likely_queries="Was enrichment durable?")

    peer = await save("Synthetic embedded peer")
    peer_neuron = await db.get(Neuron, peer["neuron_id"])
    peer_neuron.embedding = json.dumps(vector)
    await db.commit()

    async def save_candidate(session, *args, **kwargs):
        calls.append("candidate")
        saved = await save("Synthetic enrichment candidate")
        return {"session_id": "synthetic", "saved": 1, "neuron_ids": [saved["neuron_id"]]}

    def embed(_value):
        if fail_first and len(calls) == 1 and "failed" not in calls:
            calls.append("failed")
            raise OSError("synthetic local embedding failure")
        return vector

    monkeypatch.setattr(embedding_service, "embed_text", embed)
    if fail_first:
        with pytest.raises(OSError):
            await progress.run_checkpointed(db, str(log), save_candidate)
        async with AsyncSession(engine) as reader:
            checkpoint = await reader.get(DistillationCheckpoint, source_id(str(log)))
            assert checkpoint.state["pending"] is True
            pending_neuron = await reader.get(Neuron, checkpoint.state["receipt"]["neuron_ids"][0])
            assert pending_neuron.embedding is None
    result = await progress.run_checkpointed(db, str(log), save_candidate)
    assert calls.count("candidate") == 1
    publish.assert_not_awaited()
    async with AsyncSession(engine) as reader:
        neuron = await reader.get(Neuron, result["neuron_ids"][0])
        assert json.loads(neuron.embedding) == vector
        src, tgt = sorted([peer["neuron_id"], neuron.id])
        assert await reader.scalar(text(
            "SELECT count(*) FROM neuron_edges WHERE source_id=:src AND target_id=:tgt"
        ), {"src": src, "tgt": tgt}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["precommit", "postcommit"])
async def test_process_death_releases_source_and_preserves_only_committed_proposals(case, phase):
    db, engine, log, stage, calls = case
    import sys

    child_code = r'''
import asyncio, json, os, sys, time
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from app.services import lesson_store, distillation_progress as progress
from app.services.actions.init_registry import init_actions_registry

async def no_neighbor(*args, **kwargs):
    return None

def pause(proposal_id):
    print("CHECKPOINT_READY:" + str(proposal_id), flush=True)
    time.sleep(60)

async def main():
    init_actions_registry()
    lesson_store._nearest_active_lesson = no_neighbor
    phase, path = sys.argv[1:]
    engine = create_async_engine(os.environ["CORVUS_TEST_DATABASE_URL"], poolclass=NullPool)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        async def stage(session, *args, **kwargs):
            saved = await lesson_store.save_lesson(
                session, lesson="Synthetic killed-worker evidence.", evidence="Synthetic fixture.",
                label="Synthetic process death " + path, scope="Environment",
                authority_level="organizational", commit=False,
                future_use="Verify killed worker recovery.", likely_queries="Did a killed worker commit?")
            if phase == "precommit":
                pause(saved["proposal_id"])
            return {"session_id": "synthetic", "saved": 1, "queued": 1,
                    "proposal_id": saved["proposal_id"], "neuron_ids": []}
        if phase == "postcommit":
            progress._write_marker = lambda path, state: pause(state["receipt"]["proposal_id"])
        await progress.run_checkpointed(db, path, stage)
    await engine.dispose()

asyncio.run(main())
'''
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-c", child_code, phase, str(log),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stderr = b""
    try:
        async def checkpoint_ready():
            while True:
                line = await child.stdout.readline()
                if not line:
                    raise RuntimeError("synthetic child exited before checkpoint")
                if line.startswith(b"CHECKPOINT_READY:"):
                    return int(line.split(b":", 1)[1])

        proposal_id = await asyncio.wait_for(checkpoint_ready(), timeout=30)
    finally:
        if child.returncode is None:
            child.kill()
        _, stderr = await asyncio.wait_for(child.communicate(), timeout=10)
    assert child.returncode < 0, stderr.decode(errors="replace")
    count = await db.scalar(text("SELECT count(*) FROM autopilot_proposals WHERE id=:id"),
                            {"id": proposal_id})
    assert count == int(phase == "postcommit")
    result = await progress.run_checkpointed(db, str(log), stage)
    if phase == "postcommit":
        assert result["recovered"] is True
        assert result["proposal_id"] == proposal_id
        assert not calls
    else:
        assert await effects(db) == 1
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_http_checkpoint_status_and_slo_lifecycle(case, monkeypatch):
    """Real routes and DB progress, with private metric sources replaced."""
    import time

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.database import get_db
    from app.observability import slo
    from app.routers import distill, mind_metrics as metrics_router
    from app.services import distiller, mind_metrics

    db, engine, log, stage, calls = case
    monkeypatch.setattr(distill, "EPISODE_DIR", log.parent)
    monkeypatch.setattr(distiller, "EPISODE_DIR", log.parent)
    monkeypatch.setattr(mind_metrics, "collect_all", AsyncMock(return_value={
        "recall": {"latency_ms": {"p95": 1}, "performance_window": 1, "total": 1},
    }))
    monkeypatch.setattr(slo, "inventory_health", lambda: {
        "counts": {"ok": 1, "late": 0, "failing": 0},
    })

    async def isolated_db():
        yield db

    app = FastAPI()
    app.include_router(distill.router)
    app.include_router(metrics_router.router)
    app.dependency_overrides[get_db] = isolated_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async def observe(ready, blocked, expected):
            quiet = time.time() - 3600
            os.utime(log, (quiet, quiet))
            response = await client.get("/distill/status?min_quiet_minutes=0")
            assert response.status_code == 200
            assert response.json()["ready"] == ready
            assert response.json()["blocked"] == blocked
            response = await client.get("/metrics/mind/slo")
            assert response.status_code == 200
            report = response.json()
            backlog = next(o for o in report["objectives"] if o["id"] == "distill-backlog")
            assert backlog["status"] == expected
            assert report["meeting_all"] is (expected == slo.OK)

        await observe(1, 0, slo.OK)
        await progress.run_checkpointed(db, log, stage)
        await observe(0, 0, slo.OK)
        with log.open("a") as handle:
            handle.write(json.dumps({"event": "Stop", "distill_ready": True}) + "\n")
        await observe(1, 0, slo.OK)
        await progress.run_checkpointed(db, log, stage)
        await observe(0, 0, slo.OK)
        log.write_text(json.dumps({"event": "Stop", "distill_ready": True, "changed": True}) + "\n")
        await observe(0, 1, slo.UNKNOWN)
        assert len(calls) == 2
