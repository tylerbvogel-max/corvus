"""Characterize checkpoint crash windows using disposable PostgreSQL state.

Run from backend with TENANT_ID=corvus-mind, PYTHONPATH=., and an explicit
CORVUS_TEST_DATABASE_URL. Providers and candidate persistence are replaced;
actual distillation, commits, marker handling and route receipts execute.
The counter represents a repeatable write effect, not a claim of duplicate
production neuron rows. Printed observations are not a repair verdict.
"""

import asyncio
import builtins
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool


def disposable_url(raw_url):
    if not raw_url:
        raise ValueError("CORVUS_TEST_DATABASE_URL is required")
    url = make_url(raw_url)
    if url.drivername != "postgresql+asyncpg" or not (url.database or "").startswith(
        ("corvus_test_", "corvus_migration_")
    ):
        raise ValueError("An explicitly disposable asyncpg PostgreSQL database is required")
    return url


async def run_case(engine, kind):
    from app.observability import jobs
    from app.routers import distill
    from app.services import distiller, llm_provider

    with tempfile.TemporaryDirectory(prefix="corvus-checkpoint-probe-") as tmp:
        root = Path(tmp)
        log = root / "synthetic.jsonl"
        event = json.dumps({"event": "Stop", "distill_ready": True}) + "\n"
        log.write_text(event)
        async with engine.connect() as connection:
            await connection.execute(text("CREATE TEMP TABLE checkpoint_probe (effects integer NOT NULL)"))
            await connection.execute(text("INSERT INTO checkpoint_probe VALUES (0)"))
            await connection.commit()
            async with AsyncSession(bind=connection) as db:
                async def save(session, *args, **kwargs):
                    await session.execute(text("UPDATE checkpoint_probe SET effects = effects + 1"))
                    return {"saved": 1}

                def marker_fault(path, mode="r", *args, **kwargs):
                    if str(path).endswith(".distilled") and "w" in mode:
                        if kind == "cancel-after-commit":
                            raise asyncio.CancelledError("synthetic interruption")
                        raise OSError("synthetic marker write failure")
                    return builtins.open(path, mode, *args, **kwargs)

                find = distiller.find_ready_logs
                attempts = []
                try:
                    with (
                        patch.object(jobs, "RECEIPTS_DIR", root / "receipts"),
                        patch.object(distiller, "find_ready_logs", side_effect=lambda **kw: find(str(root), **kw)),
                        patch.object(llm_provider, "llm_chat", AsyncMock(return_value={"text": json.dumps({"lessons": [], "attributions": [], "recurrences": []})})),
                        patch.object(distiller, "_validate_and_save", save),
                        patch.object(distiller, "_apply_attributions", AsyncMock(return_value={})),
                    ):
                        for attempt in range(2):
                            if kind == "appended-log" and attempt == 1:
                                with log.open("a") as stream:
                                    stream.write(event)
                            observation = {"attempt": attempt + 1}
                            try:
                                if kind == "appended-log":
                                    report = await distill.distill_run(limit=1, min_quiet_minutes=0, db=db)
                                else:
                                    with patch.object(distiller, "open", marker_fault, create=True):
                                        report = await distill.distill_run(limit=1, min_quiet_minutes=0, db=db)
                                observation["maintenance"] = report["maintenance"]
                            except asyncio.CancelledError:
                                observation["cancelled"] = True
                                # Emulate request-session cleanup before retry;
                                # this cannot undo the already completed commit.
                                await db.rollback()
                            observation["persisted_effects"] = await db.scalar(text("SELECT effects FROM checkpoint_probe"))
                            observation["marker_exists"] = Path(str(log) + ".distilled").exists()
                            observation["ready_logs"] = len(find(str(root), min_quiet_minutes=0))
                            attempts.append(observation)
                    return {"case": kind, "attempts": attempts}
                finally:
                    await db.rollback()


async def main():
    raw_url = os.environ.get("CORVUS_TEST_DATABASE_URL", "")
    url = disposable_url(raw_url)
    os.environ["DATABASE_URL"] = raw_url
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        for kind in ("marker-write-failure", "cancel-after-commit", "appended-log"):
            print(json.dumps(await run_case(engine, kind)), flush=True)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
