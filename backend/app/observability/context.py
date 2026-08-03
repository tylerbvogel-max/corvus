"""Correlation identifiers carried alongside a unit of work.

A log line is only useful if you can pivot from it to everything else that
happened for the same request, session, query, or job. Passing identifiers
through every function signature is not viable in a codebase this size, so they
live in a ``ContextVar``: set once at the edge, read by the log formatter, and
automatically isolated per task by asyncio.

One dict rather than one variable per identifier, so a new correlation key —
``proposal_id`` was not foreseen when this was written — needs no change here.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator


# Empty default, never mutated in place: every bind replaces the mapping so a
# child task cannot retroactively edit its parent's correlation state.
_CORRELATION: ContextVar[dict[str, str]] = ContextVar(
    "corvus_correlation", default={},
)

# Bounded so a caller cannot turn the log record into a payload channel.
MAX_FIELDS = 12
MAX_VALUE_CHARS = 200


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def current() -> dict[str, str]:
    """The correlation identifiers in scope right now."""
    return dict(_CORRELATION.get())


def _coerce(fields: dict[str, Any]) -> dict[str, str]:
    coerced: dict[str, str] = {}
    for key, value in fields.items():
        if value is None:
            continue
        text = str(value)
        coerced[key] = text[:MAX_VALUE_CHARS]
    return coerced


@contextmanager
def bound(**fields: Any) -> Iterator[dict[str, str]]:
    """Add correlation identifiers for the duration of the block.

    Nesting merges rather than replaces, because a job that handles a session
    that runs a query should carry all three. ``None`` values are dropped so a
    caller can pass an optional id unconditionally.
    """
    merged = {**_CORRELATION.get(), **_coerce(fields)}
    if len(merged) > MAX_FIELDS:
        # Truncation is deterministic (insertion order) and silent by design:
        # a logging path must never raise into the caller it is describing.
        merged = dict(list(merged.items())[:MAX_FIELDS])
    token = _CORRELATION.set(merged)
    try:
        yield merged
    finally:
        _CORRELATION.reset(token)
