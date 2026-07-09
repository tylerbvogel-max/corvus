"""Config-driven query-telemetry retention (Tier-1 compliance, FedRAMP AU-11).

The queries table and its operational telemetry grow without bound (571 rows
and counting on the dev tenant). This purge deletes Query rows older than
``settings.query_retention_days`` together with their per-query telemetry
children — while PRESERVING audit-bearing artifacts: actions, output
violations, autopilot proposals, and eval-run cases are detached
(query FK -> NULL), never deleted. Retention must reduce data exposure
without destroying audit evidence; eval runs additionally stay immutable
(Pattern #3), so their case rows lose only the query link.

Disabled by default (query_retention_days = 0): purging history is a
tenant-policy decision, not a default. When enabled it rides the autopilot
/tick heartbeat (see routers/autopilot._run_retention_if_due) and can be
invoked manually via POST /admin/retention/purge.

Failure behavior: work is batched (retention_purge_batch) with a hard
iteration cap per pass (JPL-2) and committed per batch — an interrupted pass
leaves the DB consistent and the next pass resumes where it stopped.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

# Operational telemetry deleted WITH the query (child rows keyed by query_id).
PURGE_CHILD_TABLES = (
    "neuron_firings",
    "engram_firings",
    "propagation_log",
    "synaptic_learning_events",
    "eval_scores",
    "neuron_refinements",
    "autopilot_runs",
)

# Audit-bearing artifacts detached (FK -> NULL), NEVER deleted by retention.
DETACH_TABLES = (
    ("autopilot_proposals", "query_id"),
    ("actions", "source_query_id"),
    ("output_violations", "query_id"),
    ("eval_run_cases", "query_id"),
)

# Hard cap on batches per pass (JPL-2 bounded loop). At the default batch of
# 500 this purges up to 10k queries per pass; the heartbeat finishes the rest.
MAX_BATCHES_PER_PASS = 20


def retention_cutoff(now: datetime | None = None) -> datetime | None:
    """UTC cutoff before which query telemetry is past retention; None when
    retention is disabled (query_retention_days <= 0)."""
    days = settings.query_retention_days
    if days <= 0:
        return None
    base = now or datetime.now(timezone.utc)
    assert base.tzinfo is not None, "cutoff arithmetic requires an aware datetime"
    return base - timedelta(days=days)


async def _purge_batch(db: AsyncSession, cutoff: datetime, batch: int) -> int:
    """Purge one batch of expired queries; returns how many were deleted."""
    assert batch > 0, "batch size must be positive"
    rows = await db.execute(
        sa_text("SELECT id FROM queries WHERE created_at < :cutoff ORDER BY id LIMIT :n"),
        {"cutoff": cutoff.replace(tzinfo=None), "n": batch},
    )
    ids = [r[0] for r in rows.all()]
    if not ids:
        return 0
    params = {"ids": ids}
    for table, column in DETACH_TABLES:
        await db.execute(
            sa_text(f"UPDATE {table} SET {column} = NULL WHERE {column} = ANY(:ids)"),
            params,
        )
    for table in PURGE_CHILD_TABLES:
        await db.execute(
            sa_text(f"DELETE FROM {table} WHERE query_id = ANY(:ids)"), params,
        )
    await db.execute(sa_text("DELETE FROM queries WHERE id = ANY(:ids)"), params)
    await db.commit()
    return len(ids)


async def run_retention_purge(db: AsyncSession, now: datetime | None = None) -> dict:
    """Purge query telemetry past the retention window. Returns a summary dict.

    Also prunes citation_hop_sessions older than the cutoff — documented
    "ephemeral by intent" on the model; queries hold the only reference.
    """
    cutoff = retention_cutoff(now)
    if cutoff is None:
        return {"status": "disabled", "queries_purged": 0}

    purged = 0
    batches = 0
    while batches < MAX_BATCHES_PER_PASS:
        batches += 1
        n = await _purge_batch(db, cutoff, settings.retention_purge_batch)
        if n == 0:
            break
        purged += n

    hops = await db.execute(
        sa_text("DELETE FROM citation_hop_sessions WHERE created_at < :cutoff"),
        {"cutoff": cutoff.replace(tzinfo=None)},
    )
    await db.commit()

    summary = {
        "status": "completed" if batches < MAX_BATCHES_PER_PASS else "partial",
        "queries_purged": purged,
        "hop_sessions_purged": hops.rowcount or 0,
        "cutoff": cutoff.isoformat(),
    }
    assert summary["queries_purged"] >= 0, "purge count must be non-negative"
    if purged:
        logger.info("Retention purge: %s", summary)
    return summary
