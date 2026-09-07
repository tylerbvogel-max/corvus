"""Synthetic batches exercise real HTTP routes, persisted receipts and SLOs."""

import asyncio
import json
import logging
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from app.database import get_db
from app.observability import jobs
from app.observability.job_outcomes import BatchOutcome, summarize_report
from app.observability.slo import collect_signals
from app.routers import auditor, compile as compiler_route, distill, janitor
from app.services import distiller, skill_compiler

CANARY = "SYNTHETIC_ONLY_DO_NOT_LOG"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "RECEIPTS_DIR", tmp_path / "receipts")
    application = FastAPI()
    for module in (distill, auditor, janitor, compiler_route):
        application.include_router(module.router)

    async def fake_db():
        yield object()

    application.dependency_overrides[get_db] = fake_db
    return application


def distill_report(succeeded, failed, skipped=0):
    return {"ready": succeeded + failed + skipped,
            "processed": succeeded + failed,
            "results": ([{"session_id": "synthetic-success"}] * succeeded
                        + [{"session_id": "synthetic-failure", "error": CANARY}] * failed)}


@pytest.mark.asyncio
@pytest.mark.parametrize("succeeded,failed,skipped,outcome", [
    (2, 0, 0, "ok"), (1, 1, 3, "partial"), (0, 2, 0, "error"),
    (0, 0, 0, "no-work"),
])
async def test_distill_http_receipt_and_slo(app, monkeypatch, caplog,
                                         succeeded, failed, skipped, outcome):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(distill, "run_distillation", AsyncMock(
        return_value=distill_report(succeeded, failed, skipped)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://synthetic") as client:
        response = await client.post("/distill/run")
    assert response.status_code == 200
    batch = response.json()["maintenance"]
    assert batch == BatchOutcome("sessions", succeeded, failed, skipped).as_dict()
    receipt = jobs.read_receipt("distill")
    assert receipt["outcome"] == outcome
    assert receipt["detail"]["batch"] == batch
    assert bool(receipt["last_success"]) is (failed == 0)
    health = jobs.inventory_health()
    assert health["counts"]["failing"] == int(failed > 0)
    assert collect_signals(None, None, health)["scheduled-jobs"] == int(failed > 0)
    assert CANARY not in json.dumps(receipt)
    assert CANARY not in repr([r.__dict__ for r in caplog.records])
    completed = [r for r in caplog.records if getattr(r, "event", None)
                 in {"job.complete", "job.failed"}]
    assert completed[-1].outcome == outcome


@pytest.mark.asyncio
async def test_thrown_exception_and_successful_recovery(app, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    batch = AsyncMock(return_value=distill_report(1, 0))
    monkeypatch.setattr(distill, "run_distillation", batch)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://synthetic",
    ) as client:
        assert (await client.post("/distill/run")).status_code == 200
        prior = jobs.read_receipt("distill")["last_success"]
        batch.side_effect = RuntimeError(CANARY)
        response = await client.post("/distill/run")
        assert response.status_code == 503
        assert response.json() == {"detail": "maintenance-job-failed"}
        receipt = jobs.read_receipt("distill")
        assert receipt["outcome"] == "error"
        assert receipt["counts_known"] is False
        assert receipt["last_success"] == prior
        assert "batch" not in receipt["detail"]
        assert CANARY not in json.dumps(receipt)
        assert CANARY not in repr([r.__dict__ for r in caplog.records])
        batch.side_effect = None
        batch.return_value = distill_report(0, 0)
        assert (await client.post("/distill/run")).status_code == 200
        receipt = jobs.read_receipt("distill")
        assert receipt["outcome"] == "no-work"
        assert receipt["last_success"] != prior
        assert jobs.inventory_health()["counts"]["failing"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("module,function,path,report,outcome", [
    (auditor, "run_audit", "auditor", {"candidate_funnel": {
        "critic_batch": 2, "dropped_by_critic_cap": 1}, "verdict_failures": 1}, "partial"),
    (auditor, "run_audit", "auditor", {"candidate_funnel": {
        "critic_batch": 1}, "verdict_failures": 1}, "error"),
    (auditor, "run_audit", "auditor", {"skipped": "evidence clock"}, "no-work"),
    (janitor, "run_janitors", "janitor", {"consolidation": {
        "judged": [{"verdict": "error", "detail": CANARY}]}, "decay": {}}, "partial"),
    (janitor, "run_janitors", "janitor", {"delivery": {"unjudged": 1}}, "error"),
    (janitor, "run_janitors", "janitor", {"decay": {"skipped": "clock"}}, "no-work"),
    (compiler_route, "run_compile", "compile", {
        "clusters": 2, "composition_attempted": 2, "composition_failed": 1,
        "emitted": [{}], "retracted": [], "reconciled_ghosts": [],
        "charter": {"path": None}}, "partial"),
])
async def test_sibling_returned_failures(app, monkeypatch, module, function,
                                       path, report, outcome):
    monkeypatch.setattr(module, function, AsyncMock(return_value=report))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://synthetic") as client:
        response = await client.post(f"/{path}/run")
    assert response.status_code == 200
    assert response.json()["maintenance"]["outcome"] == outcome
    receipt = jobs.read_receipt(path)
    assert receipt["outcome"] == outcome
    assert CANARY not in json.dumps(receipt)


@pytest.mark.parametrize("value", [-1, True, 1.5, "1", None])
def test_invalid_counts_rejected_without_asserts(value):
    with pytest.raises(ValueError):
        BatchOutcome("sessions", failed=value)


@pytest.mark.parametrize("job,report", [
    ("distill", {"ready": 0, "processed": 1, "results": [{}]}),
    ("distill", {"ready": 1, "processed": 1, "results": [CANARY]}),
    ("distill", {"ready": 1, "processed": 1, "results": []}),
    ("auditor", {"candidate_funnel": {"critic_batch": 0}, "verdict_failures": 1}),
    ("janitor", {CANARY: {}}),
    ("compile", {}),
])
def test_malformed_reports_fail_closed(job, report):
    with pytest.raises(ValueError) as exc:
        summarize_report(job, report)
    assert CANARY not in str(exc.value)


@pytest.mark.asyncio
async def test_distiller_sanitizes_caught_session_failure(monkeypatch):
    monkeypatch.setattr(distiller, "find_ready_logs", Mock(return_value=["synthetic.jsonl"]))
    monkeypatch.setattr(distiller, "distill_log", AsyncMock(side_effect=RuntimeError(CANARY)))
    report = await distiller.run_distillation(object(), limit=1)
    assert report["results"][0]["error"] == "session-processing-failed"
    assert CANARY not in json.dumps(report)


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [True, False])
async def test_compiler_counts_failed_composition_without_real_io(monkeypatch, failed):
    lesson = SimpleNamespace(id=1, dormant_at=None, department="Projects")
    monkeypatch.setattr(skill_compiler, "_load_lessons", AsyncMock(return_value=[lesson]))
    monkeypatch.setattr(skill_compiler, "find_clusters", Mock(return_value=[[lesson]] if failed else []))
    monkeypatch.setattr(skill_compiler, "_load_manifest", Mock(return_value=[]))
    monkeypatch.setattr(skill_compiler, "_reconcile_manifest", Mock(return_value=([], [])))
    monkeypatch.setattr(skill_compiler, "_stale_entries", AsyncMock(return_value=[]))
    monkeypatch.setattr(skill_compiler, "_compose", AsyncMock(return_value=None))
    monkeypatch.setattr(skill_compiler, "compile_charter", AsyncMock(return_value={"path": None, "skipped": "synthetic"}))
    monkeypatch.setattr(skill_compiler, "_self_model_growth_check", AsyncMock())
    monkeypatch.setattr(skill_compiler, "_save_manifest", Mock())
    monkeypatch.setattr(skill_compiler, "_log_action", Mock())
    report = await skill_compiler.run_compile(AsyncMock())
    batch = summarize_report("compile", report)
    assert batch.failed == int(failed)
    assert batch.outcome == ("error" if failed else "no-work")


def test_wrapper_drops_arbitrary_detail_and_exception_content(app, caplog):
    caplog.set_level(logging.INFO)
    with jobs.scheduled_run("distill", "synthetic") as detail:
        detail["error"] = CANARY
        detail["mode"] = CANARY
        detail["passes"] = [CANARY, "decay"]
    assert CANARY not in json.dumps(jobs.read_receipt("distill"))
    assert CANARY not in repr([r.__dict__ for r in caplog.records])


@pytest.mark.parametrize("outcome", ["partial", "error", "unreadable", "unknown"])
def test_non_success_outcome_is_never_healthy(outcome):
    assert jobs.job_health(jobs.JOBS_BY_NAME["distill"], {
        "outcome": outcome, "last_success": "2099-01-01T00:00:00+00:00",
    })["status"] == "failing"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_http_server_never_logs_batch_exception(app, monkeypatch, caplog):
    """ASGITransport cannot catch Uvicorn's unhandled-exception log path.

    This owns an ephemeral loopback socket and temporary receipt directory.
    All four real routes run with replaced batches and a fake DB dependency;
    no personal database, filesystem projection, or provider is reachable.
    """
    caplog.set_level(logging.INFO)
    modules = [(distill, "run_distillation", "distill"),
               (auditor, "run_audit", "auditor"),
               (janitor, "run_janitors", "janitor"),
               (compiler_route, "run_compile", "compile")]
    for module, function, _ in modules:
        monkeypatch.setattr(module, function, AsyncMock(side_effect=RuntimeError(CANARY)))
    sock = socket.socket()
    server = None
    task = None
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(32)
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(
            app, log_config=None, access_log=True, lifespan="off"))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        for _ in range(200):
            if server.started or task.done():
                break
            await asyncio.sleep(0.01)
        assert server.started, "isolated HTTP server failed to start"
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            for _, _, job in modules:
                response = await client.post(f"/{job}/run")
                assert response.status_code == 503
                assert response.json() == {"detail": "maintenance-job-failed"}
                receipt = jobs.read_receipt(job)
                assert receipt["outcome"] == "error"
                assert receipt["counts_known"] is False
                assert CANARY not in json.dumps(receipt)
            # A subsequent request on the same server is a healthy no-work
            # completion, not a poisoned connection or a stuck failure state.
            monkeypatch.setattr(distill, "run_distillation", AsyncMock(
                return_value=distill_report(0, 0)))
            response = await client.post("/distill/run")
            assert response.status_code == 200
            assert jobs.read_receipt("distill")["outcome"] == "no-work"
        assert CANARY not in caplog.text
        assert CANARY not in repr([record.__dict__ for record in caplog.records])
        assert not any(record.exc_info for record in caplog.records)
    finally:
        if server is not None:
            server.should_exit = True
        if task is not None:
            await asyncio.wait_for(task, timeout=5)
        sock.close()
