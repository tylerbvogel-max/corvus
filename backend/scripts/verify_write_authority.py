#!/usr/bin/env python3
"""Loopback remember/write-gate proof against an explicitly disposable migrated DB.

Uses real lesson staging and Action Bus persistence, replacing only embedding
and near-duplicate provider work. No schema creation, truncation or deletion.
"""

import asyncio
import json
import os
import socket
from unittest.mock import AsyncMock, patch

from sqlalchemy.engine import make_url


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


async def main():
    value = os.environ.get("DATABASE_URL", "")
    require(bool(value), "explicit disposable DATABASE_URL required")
    url = make_url(value)
    require(url.drivername == "postgresql+asyncpg", "asyncpg URL required")
    require(url.host in ("localhost", "127.0.0.1", "::1"), "loopback database required")
    require((url.database or "").startswith(("corvus_test_", "corvus_migration_")),
            "disposable database required")

    import httpx
    import uvicorn
    from fastapi import FastAPI
    from sqlalchemy import func, select, text
    from app.database import async_session, engine
    from app.models import Action, AutopilotProposal, Neuron, ProposalItem
    from app.routers.recall import router
    from app.services import lesson_store, write_gate
    from app.services.actions.init_registry import init_actions_registry

    init_actions_registry()
    app = FastAPI()
    app.include_router(router)
    async with async_session() as db:
        require(await db.scalar(select(func.count()).select_from(AutopilotProposal)) == 0,
                "fresh disposable database required")
        schema = await db.scalar(text("SELECT version_num FROM alembic_version"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    payload = {
        "lesson": "Synthetic authority fixture records a verified test outcome.",
        "evidence": "Disposable loopback integration probe.",
        "label": "synthetic authority fixture",
        "future_use": "Check that authority routing preserves review requirements.",
        "likely_queries": "Which authority writes require human review?",
    }
    try:
        for _ in range(500):
            if server.started or task.done():
                break
            await asyncio.sleep(0.01)
        require(server.started, "isolated HTTP server failed to start")
        with patch.object(lesson_store, "_nearest_active_lesson", AsyncMock(return_value=None)), \
             patch.object(lesson_store, "_embed_and_wire", AsyncMock()):
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=15) as client:
                for authority in ("unrecognized_authority", "", " ", None, [], False):
                    response = await client.post("/remember", json={**payload, "authority_level": authority})
                    require(response.status_code == 422, "invalid HTTP authority was accepted")
                async with async_session() as db:
                    require(await db.scalar(select(func.count()).select_from(AutopilotProposal)) == 0,
                            "invalid HTTP request staged a proposal")
                    try:
                        await lesson_store.save_lesson(db, **payload, authority_level="invalid")
                    except ValueError:
                        pass
                    else:
                        raise RuntimeError("internal invalid authority accepted")
                    require(await db.scalar(select(func.count()).select_from(AutopilotProposal)) == 0,
                            "invalid internal write staged a proposal")

                auto = await client.post("/remember", json=payload)
                require(auto.status_code == 200 and auto.json()["route"] == "auto", "valid default write failed")
                org = await client.post("/remember", json={**payload, "authority_level": "organizational"})
                identity = await client.post("/remember", json={**payload, "scope": " assistant "})
                require(org.status_code == 200 and org.json()["route"] == "queue", "organizational write escaped review")
                require(identity.status_code == 200 and identity.json()["route"] == "queue", "identity write escaped review")

            async with async_session() as db:
                require(await db.scalar(select(func.count()).select_from(Neuron)) == 1,
                        "expected only the allowed informational neuron")
                applied_actions = await db.scalar(select(func.count()).select_from(Action).where(Action.state == "applied"))
                require(applied_actions >= 2, "allowed write did not traverse Action Bus")
                proposal = AutopilotProposal(state="proposed", gap_source="synthetic_authority_probe")
                db.add(proposal)
                await db.flush()
                for authority in ("binding_standard", "invalid"):
                    db.add(ProposalItem(proposal_id=proposal.id, action="create",
                                        neuron_spec_json=json.dumps({"authority_level": authority})))
                await db.commit()
                try:
                    await write_gate.route_proposal(db, proposal, guardrails_passed=None, confidence=None)
                except ValueError:
                    pass
                else:
                    raise RuntimeError("mixed invalid proposal accepted")
                require(proposal.state == "proposed", "invalid proposal changed state")
                require(await db.scalar(select(func.count()).select_from(Neuron)) == 1,
                        "invalid proposal created graph data")
        print(json.dumps({"schema_revision": schema, "invalid_http_rejections": 6,
                          "invalid_internal_write_rejected_before_staging": True,
                          "allowed_informational_neurons": 1, "applied_actions": applied_actions,
                          "organizational_and_identity_queued": True,
                          "mixed_invalid_proposal_rejected": True,
                          "provider_calls": 0}, indent=2))
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=10)
        finally:
            sock.close()
            await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
