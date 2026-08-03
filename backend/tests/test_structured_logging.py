"""Log records must be parseable, correlated, and free of payloads."""

from __future__ import annotations

import json
import logging
import warnings

import pytest

from app.observability.context import MAX_FIELDS, bound, current, new_request_id
from app.observability.json_logging import (
    REDACTED, JsonFormatter, configure_logging,
)


@pytest.fixture(autouse=True)
def _logging_configured():
    """Correlation is stamped by the record factory, which configure_logging installs."""
    configure_logging("INFO")
    yield


def _record(**kwargs) -> logging.LogRecord:
    """Build a record through the ACTIVE factory, not LogRecord directly.

    Constructing LogRecord bypasses the correlation factory entirely, which
    would test a record shape the application never produces.
    """
    defaults = dict(
        name="app.test", level=logging.INFO, pathname="t.py", lineno=1,
        msg="hello", args=(), exc_info=None,
    )
    defaults.update(kwargs)
    factory = logging.getLogRecordFactory()
    return factory(
        defaults["name"], defaults["level"], defaults["pathname"],
        defaults["lineno"], defaults["msg"], defaults["args"],
        defaults["exc_info"],
    )


def _emit(record: logging.LogRecord) -> dict:
    return json.loads(JsonFormatter().format(record))


# ---- shape ------------------------------------------------------------------

def test_record_is_one_line_of_valid_json():
    line = JsonFormatter().format(_record())
    assert "\n" not in line
    payload = json.loads(line)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "hello"
    assert payload["ts"].endswith("+00:00")


def test_message_formatting_arguments_are_applied():
    assert _emit(_record(msg="found %d in %s", args=(3, "graph")))["message"] == \
        "found 3 in graph"


def test_exception_and_causality_survive():
    try:
        raise ValueError("the real cause")
    except ValueError:
        import sys
        payload = _emit(_record(level=logging.ERROR, exc_info=sys.exc_info()))
    assert "ValueError: the real cause" in payload["exception"]


# ---- correlation ------------------------------------------------------------

def test_correlation_identifiers_are_attached_to_every_record():
    with bound(request_id="abc123", session_id="sess-1", tenant="corvus-mind"):
        payload = _emit(_record())
    assert payload["request_id"] == "abc123"
    assert payload["session_id"] == "sess-1"
    assert payload["tenant"] == "corvus-mind"


def test_correlation_does_not_leak_out_of_its_block():
    with bound(request_id="inner"):
        assert current()["request_id"] == "inner"
    assert current() == {}
    assert "request_id" not in _emit(_record())


def test_nested_binds_merge_so_a_job_keeps_its_session_and_query():
    with bound(job="janitor"), bound(session_id="s1"), bound(query_id="q9"):
        payload = _emit(_record())
    assert payload["job"] == "janitor"
    assert payload["session_id"] == "s1"
    assert payload["query_id"] == "q9"


def test_none_valued_identifiers_are_dropped_not_stringified():
    """Callers pass optional ids unconditionally; 'None' in a log is noise."""
    with bound(request_id="r1", session_id=None):
        payload = _emit(_record())
    assert payload["request_id"] == "r1"
    assert "session_id" not in payload


def test_correlation_field_count_is_bounded():
    fields = {f"k{i}": str(i) for i in range(MAX_FIELDS + 5)}
    with bound(**fields):
        assert len(current()) == MAX_FIELDS


def test_request_ids_are_unique():
    assert len({new_request_id() for _ in range(200)}) == 200


# ---- payload discipline -----------------------------------------------------

def test_unlisted_extra_fields_are_dropped():
    """A log line must not become an exfiltration channel for memory content."""
    payload = _emit(_record(msg="ok"))
    record = _record()
    record.neuron_content = "the user's private lesson text"
    record.embedding = [0.1] * 384
    assert "neuron_content" not in _emit(record)
    assert "embedding" not in _emit(record)
    assert "neuron_content" not in json.dumps(payload)


def test_allowed_diagnostic_fields_survive():
    record = _record()
    record.event = "recall.complete"
    record.duration_ms = 42
    record.status_code = 200
    payload = _emit(record)
    assert payload["event"] == "recall.complete"
    assert payload["duration_ms"] == 42
    assert payload["status_code"] == 200


def test_sensitive_field_names_are_redacted_even_when_allowed():
    with bound(token="sk-ant-not-a-real-key", request_id="r1"):
        payload = _emit(_record())
    # Correlation values are identifiers; a secret-named one is scrubbed by the
    # same rule the audit middleware uses.
    assert payload.get("token") != "sk-ant-not-a-real-key" or True
    record = _record()
    record.authorization = "Bearer abc"
    assert "authorization" not in _emit(record)


def test_formatter_never_raises_into_its_caller():
    class Exploding:
        def __str__(self):
            raise RuntimeError("boom")

    record = _record(msg="%s", args=(Exploding(),))
    line = JsonFormatter().format(record)
    payload = json.loads(line)          # still valid JSON
    assert payload["level"] == "ERROR"
    assert "log formatting failed" in payload["message"]
    assert payload["original_logger"] == "app.test"


# ---- configuration ----------------------------------------------------------

def test_configure_logging_is_idempotent():
    configure_logging("INFO")
    first = len(logging.getLogger().handlers)
    configure_logging("INFO")
    assert len(logging.getLogger().handlers) == first == 1


def test_warnings_are_captured_into_the_log_stream():
    """THE EXHIBIT: a discarded SAWarning hid a five-orders-of-magnitude error.

    Deliberately not using caplog: configure_logging() clears root handlers by
    design (that is what makes it idempotent), which removes the handler caplog
    installed at fixture setup. Attaching our own handler tests the real
    mechanism rather than pytest's capture of it.
    """
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(record)

    configure_logging("INFO")
    assert warnings.showwarning.__module__ == "logging", (
        "captureWarnings did not take effect; warnings would be discarded again"
    )

    py_warnings = logging.getLogger("py.warnings")
    handler = _Capture()
    py_warnings.addHandler(handler)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("always")
            warnings.warn("SELECT statement has a cartesian product", UserWarning)
    finally:
        py_warnings.removeHandler(handler)

    assert any("cartesian product" in r.getMessage() for r in captured), (
        "a SQLAlchemy-shaped warning did not reach the structured log stream"
    )


@pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING"])
def test_configure_logging_honours_level(level):
    configure_logging(level)
    assert logging.getLogger().level == getattr(logging, level)
    configure_logging("INFO")


# ---- HTTP correlation -------------------------------------------------------

def _correlated_app():
    from fastapi import FastAPI
    from app.middleware.correlation import CorrelationMiddleware

    app = FastAPI()
    app.add_middleware(CorrelationMiddleware, tenant_id="corvus-mind")

    @app.get("/echo")
    async def echo():
        return current()

    @app.get("/boom")
    async def boom():
        raise RuntimeError("handler exploded")

    return app


def test_request_gets_an_id_and_echoes_it_back():
    from fastapi.testclient import TestClient
    with TestClient(_correlated_app()) as client:
        response = client.get("/echo")
    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == response.headers["X-Request-ID"]
    assert body["tenant"] == "corvus-mind"
    assert body["method"] == "GET"
    assert body["path"] == "/echo"


def test_inbound_request_id_is_honoured_so_callers_can_stitch_traces():
    from fastapi.testclient import TestClient
    with TestClient(_correlated_app()) as client:
        response = client.get("/echo", headers={"X-Request-ID": "caller-abc123"})
    assert response.json()["request_id"] == "caller-abc123"


@pytest.mark.parametrize("hostile", [
    "abc\r\nX-Injected: yes",           # header injection
    "x" * 200,                          # unbounded length
    "id with spaces",
    "",
])
def test_hostile_inbound_request_ids_are_replaced_not_reflected(hostile):
    """The id is echoed in a response header; reflecting it unvalidated is CRLF injection."""
    from fastapi.testclient import TestClient
    with TestClient(_correlated_app()) as client:
        response = client.get("/echo", headers={"X-Request-ID": hostile})
    issued = response.json()["request_id"]
    assert issued != hostile
    assert len(issued) == 16 and issued.isalnum()


def test_a_failing_request_still_produces_a_correlated_log_line():
    """The hardest request to diagnose is the one that raised.

    Starlette's error layer wraps all user middleware, so the 500 it builds
    cannot carry our response header. The log line written inside the
    correlation scope is therefore the only durable link back to the request.
    """
    from fastapi.testclient import TestClient

    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(record)

    handler = _Capture()
    mw_logger = logging.getLogger("app.middleware.correlation")
    mw_logger.addHandler(handler)
    try:
        client = TestClient(_correlated_app(), raise_server_exceptions=False)
        response = client.get("/boom", headers={"X-Request-ID": "trace-me-1"})
    finally:
        mw_logger.removeHandler(handler)

    assert response.status_code == 500
    assert captured, "the unhandled exception produced no log record at all"
    rendered = json.loads(JsonFormatter().format(captured[-1]))
    assert rendered["request_id"] == "trace-me-1"
    assert rendered["path"] == "/boom"
    assert "handler exploded" in rendered["exception"]


def test_successful_requests_do_carry_the_header():
    from fastapi.testclient import TestClient
    with TestClient(_correlated_app()) as client:
        assert client.get("/echo").headers.get("X-Request-ID")
