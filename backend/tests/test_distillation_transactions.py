"""Per-item transaction cleanup without expanding distillation retry policy."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from app.services import distiller


class TransactionSession:
    def __init__(self):
        self.committed = [9]
        self.pending = []
        self.rollback = AsyncMock(side_effect=self._rollback)

    async def _rollback(self):
        self.pending.clear()

    async def commit(self):
        self.committed.extend(self.pending)
        self.pending.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [OSError, ValueError, AssertionError, RuntimeError])
async def test_recoverable_failure_rolls_back_before_next_item(monkeypatch, failure):
    db = TransactionSession()
    monkeypatch.setattr(distiller, "find_ready_logs", lambda **kw: ["first", "second"])

    async def item(session, path):
        if path == "first":
            session.pending.append(1)
            raise failure("SYNTHETIC_ONLY")
        assert session.pending == []
        session.pending.append(2)
        await session.commit()
        return {"session_id": path}

    monkeypatch.setattr(distiller, "distill_log", item)
    report = await distiller.run_distillation(db, limit=2)
    assert db.committed == [9, 2]
    db.rollback.assert_awaited_once()
    assert report["processed"] == 2
    assert report["results"][0]["error"] == "session-processing-failed"
    assert "SYNTHETIC_ONLY" not in str(report)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TypeError("synthetic"), IntegrityError("synthetic", {}, Exception("synthetic"))])
async def test_unexpected_failure_rolls_back_then_propagates(monkeypatch, failure):
    db = TransactionSession()
    monkeypatch.setattr(distiller, "find_ready_logs", lambda **kw: ["first", "second"])

    async def item(session, path):
        session.pending.append(1)
        raise failure

    run = AsyncMock(side_effect=item)
    monkeypatch.setattr(distiller, "distill_log", run)
    with pytest.raises(type(failure)) as caught:
        await distiller.run_distillation(db, limit=2)
    assert caught.value is failure
    assert db.pending == []
    assert db.committed == [9]
    db.rollback.assert_awaited_once()
    assert run.await_count == 1


@pytest.mark.asyncio
async def test_rollback_failure_aborts_instead_of_continuing(monkeypatch):
    db = TransactionSession()
    db.rollback = AsyncMock(side_effect=RuntimeError("synthetic rollback failure"))
    monkeypatch.setattr(distiller, "find_ready_logs", lambda **kw: ["first", "second"])
    run = AsyncMock(side_effect=OSError("synthetic item failure"))
    monkeypatch.setattr(distiller, "distill_log", run)
    with pytest.raises(RuntimeError, match="synthetic rollback failure"):
        await distiller.run_distillation(db, limit=2)
    assert run.await_count == 1
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_earlier_commit_survives_later_failure(monkeypatch):
    db = TransactionSession()
    monkeypatch.setattr(distiller, "find_ready_logs", lambda **kw: ["first", "second"])

    async def item(session, path):
        session.pending.append(1 if path == "first" else 2)
        if path == "second":
            raise OSError("synthetic")
        await session.commit()
        return {"session_id": path}

    monkeypatch.setattr(distiller, "distill_log", item)
    await distiller.run_distillation(db, limit=2)
    assert db.committed == [9, 1]
    assert db.pending == []


@pytest.mark.asyncio
@pytest.mark.parametrize("paths", [[], ["first"]])
async def test_healthy_and_no_work_do_not_rollback(monkeypatch, paths):
    db = TransactionSession()
    monkeypatch.setattr(distiller, "find_ready_logs", lambda **kw: paths)
    monkeypatch.setattr(distiller, "distill_log", AsyncMock(return_value={"session_id": "first"}))
    report = await distiller.run_distillation(db)
    assert report["processed"] == len(paths)
    db.rollback.assert_not_awaited()
