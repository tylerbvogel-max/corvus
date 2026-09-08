"""Reproduce transaction failures against an explicitly disposable PostgreSQL DB.

Run from backend with TENANT_ID=corvus-mind, PYTHONPATH=., and
CORVUS_TEST_DATABASE_URL set. No provider calls or persistent probe tables are
used. Successful execution means observations were collected, not defects fixed.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool


async def main():
    raw_url = os.environ.get("CORVUS_TEST_DATABASE_URL", "")
    if not raw_url:
        raise ValueError("CORVUS_TEST_DATABASE_URL is required")
    url = make_url(raw_url)
    if url.drivername != "postgresql+asyncpg" or not (url.database or "").startswith(
        ("corvus_test_", "corvus_migration_")
    ):
        raise ValueError("An explicitly disposable asyncpg PostgreSQL database is required")

    # Configure application imports to use the same disposable target, never a
    # default personal database. The route receives our explicit session below.
    os.environ["DATABASE_URL"] = raw_url
    from app.observability import jobs
    from app.routers import distill
    from app.services import distiller, llm_provider

    # PostgreSQL temporary tables belong to physical connections. NullPool closes
    # each connection instead of returning it to a pool for the next case.
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        for kind in ("recoverable", "database-error"):
            with tempfile.TemporaryDirectory(prefix="corvus-distill-probe-") as tmp:
                root = Path(tmp)
                for name in ("first", "second"):
                    (root / f"{name}.jsonl").write_text(
                        json.dumps({"event": "Stop", "distill_ready": True}) + "\n"
                    )
                async with engine.connect() as connection:
                    residue = await connection.scalar(text("SELECT to_regclass('pg_temp.rollback_probe')"))
                    if residue is not None:
                        raise RuntimeError("Fixture isolation failed: temporary table already exists")
                    await connection.execute(text("CREATE TEMP TABLE rollback_probe (id integer PRIMARY KEY)"))
                    await connection.execute(text("INSERT INTO rollback_probe VALUES (9)"))
                    await connection.commit()
                    async with AsyncSession(bind=connection) as db:
                        async def save(session, candidates, injected, session_id, **kwargs):
                            first = session_id.startswith("first")
                            session.info["probe_first"] = first
                            value = 1 if first else 2
                            await session.execute(text("INSERT INTO rollback_probe VALUES (:id)"), {"id": value})
                            if kind == "database-error" and first:
                                await session.execute(text("INSERT INTO rollback_probe VALUES (1)"))
                            return {"saved": 1}

                        async def attribute(session, *args, **kwargs):
                            if kind == "recoverable" and session.info["probe_first"]:
                                raise OSError("SYNTHETIC_ONLY failure after staging")
                            return {}

                        original_find = distiller.find_ready_logs
                        observation = {"case": kind, "fresh_temp_namespace": True}
                        try:
                            with (
                                patch.object(jobs, "RECEIPTS_DIR", root / "receipts"),
                                patch.object(distiller, "find_ready_logs", side_effect=lambda **kw: original_find(str(root), **kw)),
                                patch.object(llm_provider, "llm_chat", AsyncMock(return_value={"text": json.dumps({"lessons": [], "attributions": [], "recurrences": []})})),
                                patch.object(distiller, "_validate_and_save", save),
                                patch.object(distiller, "_apply_attributions", attribute),
                            ):
                                try:
                                    report = await distill.distill_run(limit=2, min_quiet_minutes=0, db=db)
                                    observation["http_status"] = 200
                                    observation["report"] = report
                                except Exception as exc:
                                    observation["http_status"] = getattr(exc, "status_code", None)
                                    observation["route_exception"] = type(exc).__name__
                                try:
                                    observation["rows_before_cleanup"] = list((await db.execute(text("SELECT id FROM rollback_probe ORDER BY id"))).scalars())
                                    observation["session_usable"] = True
                                except Exception as exc:
                                    observation["session_usable"] = False
                                    observation["session_exception"] = type(exc).__name__
                        finally:
                            await db.rollback()
                        print(json.dumps(observation, default=str), flush=True)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
