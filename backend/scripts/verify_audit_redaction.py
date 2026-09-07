#!/usr/bin/env python3
"""Prove audit redaction over loopback HTTP and a migrated disposable PostgreSQL DB.

Requires an explicit DATABASE_URL with a corvus_test_* or corvus_migration_* name.
Adds synthetic audit rows; does not create, migrate, truncate or drop a schema.
"""

import asyncio
import io
import json
import logging
import os
import socket

from sqlalchemy.engine import make_url


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


async def main():
    value = os.environ.get("DATABASE_URL", "")
    require(bool(value), "explicit disposable DATABASE_URL required")
    url = make_url(value)
    require(url.drivername == "postgresql+asyncpg", "asyncpg PostgreSQL URL required")
    require(url.host in ("127.0.0.1", "localhost", "::1"), "loopback database required")
    require((url.database or "").startswith(("corvus_test_", "corvus_migration_")),
            "disposable database name required")

    import httpx
    import uvicorn
    from fastapi import FastAPI, Request
    from sqlalchemy import select, text
    from starlette.responses import Response

    from app.database import async_session, engine
    from app.middleware import audit
    from app.models import AuditLog

    async with engine.connect() as connection:
        schema = (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalar_one()
    app = FastAPI()
    app.add_middleware(audit.AuditMiddleware)

    @app.post("/synthetic-audit-proof")
    async def echo(request: Request):
        return Response(await request.body())

    canary = "SYNTHETIC_ONLY_DO_NOT_PERSIST"
    bodies = [
        json.dumps({"outer": {"api_key": canary}}).encode(),
        json.dumps([{"token": canary}, canary]).encode(),
        ("token=" + canary).encode(),
        json.dumps(canary).encode(),
        json.dumps({canary: canary}).encode(),
        b"\xff" + canary.encode(),
        json.dumps({"content": canary * 3000}).encode(),
    ]
    logs = io.StringIO()
    handler = logging.StreamHandler(logs)
    audit.logger.addHandler(handler)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(500):
            if server.started or task.done():
                break
            await asyncio.sleep(0.01)
        require(server.started, "isolated server did not start")
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            for body in bodies:
                response = await client.post("/synthetic-audit-proof", content=body)
                require(response.status_code == 200 and response.content == body,
                        "middleware changed downstream response")

            class FailedSession:
                async def __aenter__(self):
                    raise RuntimeError("synthetic SQL parameters: " + canary)

                async def __aexit__(self, *args):
                    pass

            audit.async_session = FailedSession
            response = await client.post("/synthetic-audit-proof", content=bodies[0])
            require(response.status_code == 200, "audit failure changed response")
            audit.async_session = async_session
            response = await client.post("/synthetic-audit-proof", content=b"{}")
            require(response.status_code == 200, "recovery request failed")

        async with async_session() as session:
            rows = (await session.scalars(select(AuditLog).where(
                AuditLog.endpoint == "/synthetic-audit-proof",
            ))).all()
        require(len(rows) == len(bodies) + 1, "expected a fresh disposable audit proof database")
        for row in rows:
            summary = row.request_body_summary
            require(canary not in summary, "canary reached PostgreSQL audit summary")
            require(len(summary.encode()) <= audit.MAX_BODY_SUMMARY, "summary exceeds budget")
            json.loads(summary)
        require(canary not in logs.getvalue(), "canary reached audit error log")
        require("Audit log write failed" in logs.getvalue(), "missing failure diagnostic")
        print(json.dumps({
            "schema_revision": schema, "database": url.database,
            "http_requests": len(bodies) + 2, "persisted_rows": len(rows),
            "body_canary_leaks": 0, "audit_error_log_canary_leaks": 0,
            "failure_response_preserved": True, "recovery_persisted": True,
            "scope": "isolated middleware and PostgreSQL; not full application auth",
        }, indent=2))
    finally:
        audit.async_session = async_session
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=10)
        finally:
            sock.close()
            audit.logger.removeHandler(handler)
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
