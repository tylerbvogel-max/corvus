"""Same-job exclusion: tasks, real HTTP, processes, and killed-owner recovery."""

import asyncio
import os
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from app.database import get_db
from app.observability import jobs
from app.observability.job_locks import JobBusy, job_run_lock
from app.routers import auditor, compile as compiler_route, distill, janitor


@asynccontextmanager
async def client_for(app, live):
    if not live:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://synthetic") as client:
            yield client
        return
    sock = socket.socket()
    task = None
    server = None
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen(32)
        server = uvicorn.Server(uvicorn.Config(
            app, lifespan="off", log_config=None, access_log=False))
        task = asyncio.create_task(server.serve(sockets=[sock]))
        for _ in range(200):
            if server.started or task.done():
                break
            await asyncio.sleep(0.01)
        assert server.started
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{sock.getsockname()[1]}",
        ) as client:
            yield client
    finally:
        if server:
            server.should_exit = True
        if task:
            await asyncio.wait_for(task, 5)
        sock.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("live", [False, pytest.param(True, marks=pytest.mark.integration)])
@pytest.mark.parametrize("module,function,job,report", [
    (distill, "run_distillation", "distill", {"ready": 0, "processed": 0, "results": []}),
    (auditor, "run_audit", "auditor", {"skipped": "synthetic evidence clock"}),
    (janitor, "run_janitors", "janitor", {"decay": {"skipped": "synthetic"}}),
    (compiler_route, "run_compile", "compile", {"clusters": 0,
        "composition_attempted": 0, "composition_failed": 0, "emitted": [],
        "retracted": [], "reconciled_ghosts": [], "charter": {"path": None}}),
])
async def test_http_contender_does_not_execute_or_replace_receipt(
    tmp_path, monkeypatch, live, module, function, job, report,
):
    monkeypatch.setattr(jobs, "RECEIPTS_DIR", tmp_path / "receipts")
    prior = {"job": job, "outcome": "ok", "last_success": "synthetic-prior"}
    jobs.write_receipt(job, prior)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def batch(*args, **kwargs):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return report

    monkeypatch.setattr(module, function, batch)
    app = FastAPI()
    app.include_router(module.router)

    async def fake_db():
        yield object()

    app.dependency_overrides[get_db] = fake_db
    async with client_for(app, live) as client:
        first = asyncio.create_task(client.post(f"/{job}/run"))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            contender = await asyncio.wait_for(client.post(f"/{job}/run"), 2)
            assert contender.status_code == 409
            assert contender.json() == {"detail": "maintenance-job-busy"}
            assert calls == 1
            assert jobs.read_receipt(job) == prior
        finally:
            release.set()
            response = await first
        assert response.status_code == 200
        assert jobs.read_receipt(job)["outcome"] == "no-work"
        assert (await client.post(f"/{job}/run")).status_code == 200
        assert calls == 2


def test_cross_process_exclusion_and_killed_owner_recovery(tmp_path):
    code = """
import sys, time
from pathlib import Path
from app.observability.job_locks import job_run_lock
with job_run_lock('distill', Path(sys.argv[1])):
    print('owned', flush=True)
    time.sleep(60)
"""
    process = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True)
    try:
        assert process.stdout.readline().strip() == "owned"
        with pytest.raises(JobBusy):
            with job_run_lock("distill", tmp_path):
                pytest.fail("second process entered owned job")
        process.kill()
        process.wait(timeout=5)
        with job_run_lock("distill", tmp_path):
            pass
        assert (tmp_path / "distill.lock").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()


def test_normal_exception_and_distinct_job_ownership(tmp_path):
    with job_run_lock("distill", tmp_path):
        with job_run_lock("janitor", tmp_path):
            pass
        with pytest.raises(JobBusy):
            with job_run_lock("distill", tmp_path):
                pass
    with pytest.raises(RuntimeError):
        with job_run_lock("distill", tmp_path):
            raise RuntimeError("synthetic failure")
    with job_run_lock("distill", tmp_path):
        pass


@pytest.mark.asyncio
async def test_cancellation_releases_owner(tmp_path):
    entered = asyncio.Event()

    async def owner():
        with job_run_lock("distill", tmp_path):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(owner())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with job_run_lock("distill", tmp_path):
        pass


def test_symlink_target_is_not_followed(tmp_path):
    target = tmp_path / "target"
    target.write_text("synthetic untouched")
    (tmp_path / "distill.lock").symlink_to(target)
    with pytest.raises(OSError):
        with job_run_lock("distill", tmp_path):
            pass
    assert target.read_text() == "synthetic untouched"


@pytest.mark.parametrize("job", ["../escape", "", None, "a/b"])
def test_invalid_identity_rejected_before_file_creation(tmp_path, job):
    with pytest.raises(ValueError):
        with job_run_lock(job, tmp_path / "absent"):
            pass
    assert not (tmp_path / "absent").exists()


def test_lock_io_failure_is_safe_http_error(tmp_path, monkeypatch):
    from fastapi import HTTPException
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("SYNTHETIC_ONLY")
    monkeypatch.setattr(jobs, "RECEIPTS_DIR", blocker)
    with pytest.raises(HTTPException) as failure:
        with jobs.scheduled_http_run("distill", "synthetic"):
            pytest.fail("lock failure allowed job execution")
    assert failure.value.status_code == 503
    assert failure.value.detail == "maintenance-job-failed"
