"""Synthetic canaries through ASGI, committed audit summaries and error logs."""

import json
import logging
import sqlite3

import httpx
import pytest
from fastapi import FastAPI, Request
from starlette.responses import Response

from app.middleware import audit


CANARY = "SYNTHETIC_ONLY_DO_NOT_PERSIST"
pytestmark = pytest.mark.integration


@pytest.fixture
def audit_app(monkeypatch, tmp_path):
    connection = sqlite3.connect(tmp_path / "audit.sqlite")
    connection.execute("CREATE TABLE summaries (body TEXT, status INTEGER)")
    state = {"fail": False, "commits": 0, "received": []}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def add(self, record):
            self.record = record

        async def commit(self):
            if state["fail"]:
                # Database exceptions can include statement parameters.
                raise RuntimeError("SQL parameters: " + CANARY)
            connection.execute(
                "INSERT INTO summaries VALUES (?, ?)",
                (self.record.request_body_summary, self.record.status_code),
            )
            connection.commit()
            state["commits"] += 1

    monkeypatch.setattr(audit, "async_session", Session)
    app = FastAPI()
    app.add_middleware(audit.AuditMiddleware)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def endpoint(request: Request, path: str):
        body = await request.body()
        state["received"].append(body)
        if path == "raises":
            raise ValueError("synthetic downstream failure")
        return Response(body, status_code=422 if path == "reject" else 200)

    yield app, connection, state
    connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    json.dumps({"outer": {"api_key": CANARY}}).encode(),
    json.dumps({"outer": [{"Authorization": CANARY}, {"password": [CANARY]}]}).encode(),
    json.dumps([{"token": CANARY}, CANARY]).encode(),
    ("token=" + CANARY).encode(),
    json.dumps(CANARY).encode(),
    json.dumps({CANARY: CANARY, "message": CANARY}).encode(),
    b'\xff' + CANARY.encode(),
    b'{"password":"' + CANARY.encode(),
])
async def test_canary_never_reaches_committed_summary(audit_app, body):
    app, connection, state = audit_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/write", content=body)
    assert response.status_code == 200
    assert response.content == body
    assert state["received"] == [body]
    assert state["commits"] == 1
    summary = connection.execute("SELECT body FROM summaries").fetchone()[0]
    assert CANARY not in summary
    assert len(summary.encode("utf-8")) <= audit.MAX_BODY_SUMMARY
    json.loads(summary)


@pytest.mark.asyncio
async def test_database_failure_logs_no_exception_payload_and_recovers(audit_app, caplog):
    app, connection, state = audit_app
    state["fail"] = True
    with caplog.at_level(logging.ERROR, logger=audit.__name__):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/write", json={"api_key": CANARY})
            assert response.status_code == 200
            assert state["commits"] == 0
            assert connection.execute("SELECT count(*) FROM summaries").fetchone()[0] == 0
            state["fail"] = False
            assert (await client.post("/write", json={})).status_code == 200
    assert "Audit log write failed" in caplog.text
    assert CANARY not in caplog.text
    assert all(record.exc_info is None for record in caplog.records if record.name == audit.__name__)
    assert state["commits"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_mutations_and_http_errors_are_audited(audit_app, method):
    app, connection, state = audit_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, "/reject", content=b"{}")
    assert response.status_code == 422
    assert state["commits"] == 1
    assert connection.execute("SELECT status FROM summaries").fetchone()[0] == 422


@pytest.mark.asyncio
async def test_documented_coverage_exclusions(audit_app):
    app, connection, state = audit_app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        await client.get("/read")
        for path in audit.SKIP_ENDPOINTS:
            await client.post(path, content=b"{}")
        with pytest.raises(ValueError, match="synthetic downstream failure"):
            await client.post("/raises", content=b"{}")
    assert state["commits"] == 0


def test_shape_redaction_and_scalar_omission():
    summary = json.loads(audit._redact_body(json.dumps({
        "outer": [{"Api-Key": CANARY, "refresh_token": CANARY}],
        "content": CANARY, "count": 12345,
    }).encode()))
    assert summary["body"]["field_0"][0] == {
        "api_key": "[REDACTED]", "refresh_token": "[REDACTED]",
    }
    assert summary["body"]["field_1"] == "[string]"
    assert summary["body"]["field_2"] == "[number]"
    assert CANARY not in json.dumps(summary)


@pytest.mark.parametrize("body,reason", [
    (b"null", "non_container_json"),
    (b"true", "non_container_json"),
    (b"123", "non_container_json"),
    (b"not json", "invalid_json"),
    (b"\xff", "invalid_json"),
    (b'{"x": NaN}', "invalid_json"),
    (b"[" * 1100 + b"]" * 1100, "invalid_json"),
])
def test_omission_metadata(body, reason):
    assert json.loads(audit._redact_body(body)) == {"bytes": len(body), "omitted": reason}


def test_explicit_limits_and_empty_body():
    assert audit._redact_body(b"") == ""
    oversized = b" " * (audit.MAX_BODY_PARSE_BYTES + 1)
    assert json.loads(audit._redact_body(oversized))["omitted"] == "body_size_limit"
    deep = b"[" * (audit.MAX_BODY_DEPTH + 2) + b"0" + b"]" * (audit.MAX_BODY_DEPTH + 2)
    assert "[depth limit]" in audit._redact_body(deep)
    wide = json.dumps([None] * (audit.MAX_BODY_NODES + 1)).encode()
    assert json.loads(audit._redact_body(wide))["omitted"] == "node_limit"
    large_summary = json.dumps({str(i): None for i in range(audit.MAX_BODY_NODES - 1)}).encode()
    result = audit._redact_body(large_summary)
    assert json.loads(result)["omitted"] == "summary_size_limit"
    assert len(result.encode()) <= audit.MAX_BODY_SUMMARY
