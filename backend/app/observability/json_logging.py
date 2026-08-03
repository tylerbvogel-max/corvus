"""One-line JSON log records with correlation identifiers attached.

Corvus had no logging configuration at all: 60 log calls across 30 modules
inherited whatever root handler happened to exist, which under uvicorn means
unstructured text with no request, session, or job identity. Nothing could be
correlated after the fact, and warnings were discarded entirely — that is how
``/admin/audit-log/summary`` reported 520,569,856 rows instead of 22,816 for
the life of the endpoint while SQLAlchemy emitted a cartesian-product warning
on every single call that nothing consumed.

Two deliberate limits, both from this record's acceptance criteria:

* Telemetry carries IDENTIFIERS, not payloads. ``extra`` is whitelisted rather
  than merged wholesale, so a caller cannot turn a log line into an exfiltration
  channel for memory content, and known-sensitive keys are redacted by name.
* Logging never raises. A formatter that throws inside an exception handler
  destroys the diagnostic it was called to preserve.
"""

from __future__ import annotations

import datetime as _datetime
import json
import logging
import warnings

from app.observability.context import current


# Mirrors app/middleware/audit.py's REDACT_FIELDS so the two agree about what
# must never be persisted; divergence here would be a quiet policy split.
REDACT_FIELDS = frozenset({
    "password", "secret", "token", "api_key", "apikey", "authorization",
    "cookie", "set-cookie", "credential",
})
REDACTED = "[REDACTED]"

# Where the record factory parks the correlation snapshot. Underscored so it
# cannot collide with a caller's ``extra=`` key.
CORRELATION_ATTR = "_corvus_correlation"

# Only these ``extra=`` keys reach the record. Anything else is dropped, which
# is the safe direction: a missing diagnostic field is a nuisance, a leaked
# memory payload is a breach.
ALLOWED_EXTRA = frozenset({
    "event", "outcome", "duration_ms", "status_code", "method", "path",
    "count", "reason", "job", "tenant", "capability", "state",
})

# Attributes LogRecord always carries; anything outside this set was passed by
# the caller through ``extra=``.
_RESERVED = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)))


def _scrub(key: str, value: object) -> object:
    if key.lower() in REDACT_FIELDS:
        return REDACTED
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)[:200]


class JsonFormatter(logging.Formatter):
    """Render one log record as a single line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            payload = self._payload(record)
        except Exception as exc:  # noqa: BLE001 - see module docstring
            payload = {
                "ts": _datetime.datetime.now(_datetime.timezone.utc).isoformat(),
                "level": "ERROR",
                "logger": "app.observability.json_logging",
                "message": f"log formatting failed: {type(exc).__name__}: {exc}",
                "original_logger": getattr(record, "name", "?"),
            }
        return json.dumps(payload, default=str, separators=(",", ":"))

    def _payload(self, record: logging.LogRecord) -> dict[str, object]:
        payload: dict[str, object] = {
            "ts": _datetime.datetime.fromtimestamp(
                record.created, _datetime.timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Correlation first-class, not nested: these are the pivot keys an
        # operator greps by. Read from the record, where the factory stamped it
        # at CREATION time — reading current() here instead would attach
        # whatever context happens to be live at FORMAT time, which is a
        # different request under any queued or deferred handler.
        payload.update(getattr(record, CORRELATION_ATTR, None) or {})

        for key, value in vars(record).items():
            if key in _RESERVED or key not in ALLOWED_EXTRA:
                continue
            payload[key] = _scrub(key, value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return payload


def _install_correlation_factory() -> None:
    """Stamp the live correlation onto every record as it is created.

    A formatter cannot do this: formatting happens later, possibly on another
    task or a logging thread, where the ContextVar holds a different request's
    identifiers or none at all. Wrapping the factory binds the correlation to
    the record at the only moment it is provably correct.
    """
    existing = logging.getLogRecordFactory()
    if getattr(existing, "_corvus_correlation_factory", False):
        return

    def factory(*args, **kwargs):
        record = existing(*args, **kwargs)
        setattr(record, CORRELATION_ATTR, current())
        return record

    factory._corvus_correlation_factory = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)


def configure_logging(level: str = "INFO") -> None:
    """Install JSON logging process-wide, including uvicorn's own loggers.

    Idempotent: repeated calls replace handlers rather than stacking them, so
    a reload or a test that reconfigures does not double every line.
    """
    _install_correlation_factory()

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn installs its own handlers at import; without this its access and
    # error lines stay unstructured and the HTTP half of criterion 1 fails.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    # THE EXHIBIT: warnings were discarded, so a cartesian-product SAWarning
    # fired on every call to an endpoint that was wrong by five orders of
    # magnitude and nobody saw it. Route them into the same structured stream.
    #
    # The False/True pair is load-bearing, not cargo cult. captureWarnings(True)
    # stashes the original showwarning once and then becomes a NO-OP; anything
    # that later restores showwarning — every `warnings.catch_warnings()` block
    # that was entered before capture was installed, pytest's own per-test
    # wrapper — silently un-captures warnings for the rest of the process, and
    # a second call cannot fix it. Toggling off first drops the stash so this
    # always reinstalls. Found by a test that asserted the capture was live.
    logging.captureWarnings(False)
    logging.captureWarnings(True)
    warnings.simplefilter("default")
