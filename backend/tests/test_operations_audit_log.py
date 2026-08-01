"""The audit-trail read surface owned by app/operations/.

Split out of routers/compliance.py by roadmap record 04. Ownership tests live
in test_capability_composition.py; this file covers the queries themselves,
starting with the counting bug the move surfaced.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, func

from app.models import AuditLog
from app.operations.router import audit_log_summary, list_audit_log


class _FakeResult:
    def __init__(self, value=None, rows=None):
        self._value, self._rows = value, rows or []

    def scalar(self):
        return self._value

    def scalar_one_or_none(self):
        # Only the "latest entry" query uses this, and it wants a timestamp.
        return None

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def __iter__(self):
        return iter(self._rows)


class _RecordingSession:
    """Captures every statement so the compiled SQL can be inspected."""

    def __init__(self, scalar_value=0):
        self.statements = []
        self._scalar = scalar_value

    async def execute(self, statement):
        self.statements.append(statement)
        return _FakeResult(value=self._scalar, rows=[])


@pytest.mark.hermetic
@pytest.mark.asyncio
async def test_total_records_does_not_cross_join_the_table_with_itself():
    """Regression: the total was the square of the true row count.

    ``select(func.count(AuditLog.id)).select_from(select(AuditLog).subquery())``
    leaves audit_log in the FROM clause next to the subquery. The result is a
    cartesian product, and on 2026-08-01 it reported 22,816 real rows as
    520,569,856. The compiled statement must name the table exactly once.
    """
    db = _RecordingSession(scalar_value=17)
    result = await audit_log_summary(since=None, db=db)

    # A cartesian product is exactly "more than one element in FROM", so ask
    # SQLAlchemy rather than pattern-matching the rendered string.
    froms = db.statements[0].get_final_froms()
    count_sql = str(db.statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert len(froms) == 1, (
        f"the total-records query selects from {len(froms)} FROM elements "
        f"({[str(f) for f in froms]}), which is a cartesian product:\n{count_sql}"
    )
    assert "anon_" not in count_sql, f"an unjoined subquery is back:\n{count_sql}"
    assert result["total_records"] == 17


@pytest.mark.hermetic
@pytest.mark.asyncio
async def test_total_records_still_honours_the_since_filter():
    """The fix must not drop the time bound the broken version applied."""
    since = "2026-01-01T00:00:00"
    db = _RecordingSession(scalar_value=3)
    await audit_log_summary(since=since, db=db)

    count_sql = str(db.statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "timestamp" in count_sql.lower(), (
        f"since= was accepted but not applied to the total:\n{count_sql}"
    )
    assert "2026-01-01" in count_sql


@pytest.mark.hermetic
@pytest.mark.asyncio
async def test_list_audit_log_applies_each_filter_it_accepts():
    """Every documented filter must reach the WHERE clause."""
    db = _RecordingSession()
    await list_audit_log(
        action="post", endpoint_filter="/recall", since="2026-01-01T00:00:00",
        status_code_min=400, limit=5, offset=2, db=db,
    )
    sql = str(db.statements[0].compile(compile_kwargs={"literal_binds": True})).lower()
    assert "'post'" in sql, "action filter is not upper-cased into the query"
    assert "recall" in sql
    assert "2026-01-01" in sql
    assert "400" in sql
    assert "limit 5" in sql and "offset 2" in sql
