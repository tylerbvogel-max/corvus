"""Query-telemetry retention (fwd-tier1, FedRAMP AU-11).

Unit-level checks on the retention policy surface: the cutoff derivation,
the disabled-by-default posture, and the audit-preservation contract (which
tables are purged vs detached). The destructive SQL path is verified against
a throwaway database, not here — the suite runs DB-less.
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.services.retention import (
    DETACH_TABLES,
    MAX_BATCHES_PER_PASS,
    PURGE_CHILD_TABLES,
    retention_cutoff,
    run_retention_purge,
)


def test_retention_disabled_by_default():
    """Purging history is a tenant-policy decision — never a default."""
    assert settings.query_retention_days == 0
    assert retention_cutoff() is None


@pytest.mark.asyncio
async def test_disabled_retention_purges_nothing_without_touching_db(monkeypatch):
    monkeypatch.setattr(settings, "query_retention_days", 0)

    class ExplodingDB:  # any DB call would be a policy violation
        def __getattr__(self, name):
            raise AssertionError("disabled retention must not touch the database")

    result = await run_retention_purge(ExplodingDB())
    assert result == {"status": "disabled", "queries_purged": 0}


def test_cutoff_is_window_days_before_now(monkeypatch):
    monkeypatch.setattr(settings, "query_retention_days", 90)
    now = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    cutoff = retention_cutoff(now)
    assert cutoff == now - timedelta(days=90)
    assert cutoff.tzinfo is not None


def test_audit_tables_are_detached_never_purged():
    """The compliance contract: retention reduces telemetry exposure but must
    not destroy audit evidence. Actions, violations, proposals, and immutable
    eval-run cases are detached (FK -> NULL) — asserting membership here makes
    moving one to the delete list a deliberate, reviewed act."""
    detach_names = {t for t, _col in DETACH_TABLES}
    assert detach_names == {
        "autopilot_proposals", "actions", "output_violations", "eval_run_cases",
    }
    assert detach_names.isdisjoint(PURGE_CHILD_TABLES)
    assert "queries" not in PURGE_CHILD_TABLES, "queries row deletion is explicit, not a child"


def test_purge_pass_is_bounded():
    assert 0 < MAX_BATCHES_PER_PASS <= 100, "purge pass must stay bounded (JPL-2)"
    assert settings.retention_purge_batch > 0
