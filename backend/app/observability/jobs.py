"""Scheduled-job inventory and run receipts.

THE CANONICAL SILENT FAILURE. A retired autopilot timer once failed every five
minutes without useful notice. The same shape was still live when this was
written, and it was measured rather than assumed:

    ExecStart=/usr/bin/curl -s --max-time 600 -X POST http://localhost:8005/janitor/run

``curl -s`` without ``--fail`` exits 0 on an HTTP error — verified: a 405 from
this very service returned exit code 0, while ``curl -sf`` returned 22. Every
one of the four scheduled jobs therefore reported ``Result=success`` to systemd
no matter what the endpoint answered, and no unit carried ``OnFailure=``. Three
gaps compounding: the runner could not see failure, systemd was told success,
and nothing would have alerted even if it had been told otherwise.

systemd's ``Result=success`` means "the command ran", never "the work
succeeded". So the health signal cannot live in systemd — it has to be a
receipt the job itself writes, recording what it did, whether it worked, and
what went wrong if it did not.

The inventory is DECLARATIVE and machine-checked against what is actually
installed, because an inventory that drifts is worse than none: it reads as
coverage while describing a system that no longer exists.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from app.observability.context import bound
from app.observability.job_outcomes import BatchOutcome, exception_reason, safe_detail
from app.observability.job_locks import JobBusy, job_run_lock

logger = logging.getLogger(__name__)


RECEIPTS_DIR = Path(
    os.environ.get("CORVUS_JOB_RECEIPTS_DIR",
                   Path.home() / ".corvus-mind" / "job-receipts")
)

# A run is late once it has missed its cadence by this factor. Deliberately
# generous: the point is to catch "stopped running", not to page on jitter.
# Labelled a hypothesis per this record's prompt — revisit against observed
# run intervals rather than defending the number.
LATE_MULTIPLIER = 2.5


@dataclass(frozen=True)
class ScheduledJob:
    """One scheduled unit of work and everything an operator needs at 3am."""

    name: str
    unit: str
    owner: str
    cadence: str
    cadence_seconds: int | None
    endpoint: str
    overlap_policy: str
    retry_contract: str
    idempotence: str
    health_signal: str
    remediation: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# Every ENABLED timer belongs here. test_job_inventory.py reconciles this list
# against the units actually installed, in both directions, so a timer added
# without an entry fails the suite and an entry whose timer was removed does
# too.
HTTP_OVERLAP_POLICY = (
    "Same-job HTTP runs use a nonblocking OS file lock across workers sharing "
    "one local receipt directory; contenders get 409 without overwriting "
    "receipts. Different jobs and direct service calls are not serialized. "
    "Systemd only serializes its own unit."
)

JOB_INVENTORY: tuple[ScheduledJob, ...] = (
    ScheduledJob(
        name="distill",
        unit="corvus-mind-distill.timer",
        owner="memory-ingestion",
        cadence="every 30 minutes (OnUnitActiveSec=30min, OnBootSec=15min)",
        cadence_seconds=30 * 60,
        endpoint="POST /distill/run",
        overlap_policy=HTTP_OVERLAP_POLICY,
        retry_contract="No retry. The next tick is the retry, 30 minutes later.",
        idempotence=(
            "Boolean .distilled markers suppress whole sessions, including appended "
            "content. Database commit and marker writing are not atomic; retry "
            "idempotence is not established. See checkpoint-integrity follow-up."
        ),
        health_signal="A receipt with outcome=ok written within LATE_MULTIPLIER cadences.",
        remediation=(
            "Check the Claude CLI is reachable (this job spends Opus via "
            "subprocess) and that ~/.claude/projects episode logs are readable. "
            "Re-run: curl -sf -X POST localhost:8005/distill/run"
        ),
    ),
    ScheduledJob(
        name="janitor",
        unit="corvus-mind-janitor.timer",
        owner="maintenance-integrity",
        cadence="every 6 hours (OnUnitActiveSec=6h, OnBootSec=30min)",
        cadence_seconds=6 * 3600,
        endpoint="POST /janitor/run",
        overlap_policy=HTTP_OVERLAP_POLICY,
        retry_contract="No retry; the next 6-hour tick is the retry.",
        idempotence=(
            "Idempotent per pass. Decay and plasticity ride the DISTILLED-SESSION "
            "clock, not wall time, so a repeated run with no new evidence is a "
            "no-op rather than a second decay."
        ),
        health_signal="A receipt with outcome=ok written within LATE_MULTIPLIER cadences.",
        remediation=(
            "Inspect the report at GET /janitor/status and the structured logs "
            "for job=janitor. Passes are independently selectable, so bisect "
            "with ?consolidation=false&staleness=false&... to isolate one."
        ),
    ),
    ScheduledJob(
        name="auditor",
        unit="corvus-mind-auditor.timer",
        owner="maintenance-integrity",
        cadence="every 12 hours (OnUnitActiveSec=12h, OnBootSec=45min)",
        cadence_seconds=12 * 3600,
        endpoint="POST /auditor/run?mode=auto",
        overlap_policy=HTTP_OVERLAP_POLICY,
        retry_contract="No retry; the next 12-hour tick is the retry.",
        idempotence=(
            "Proposes only. The auditor never applies its own findings, so a "
            "duplicate run can at worst re-propose, and proposals dedupe."
        ),
        health_signal="A receipt with outcome=ok written within LATE_MULTIPLIER cadences.",
        remediation=(
            "Scheduled doubt runs on evidence time; a skipped run with no new "
            "distilled sessions is CORRECT, not a failure. Check logs for "
            "job=auditor before assuming breakage."
        ),
    ),
    ScheduledJob(
        name="compile",
        unit="corvus-mind-compile.timer",
        owner="skill-projection",
        cadence="every 24 hours (OnUnitActiveSec=24h, OnBootSec=45min)",
        cadence_seconds=24 * 3600,
        endpoint="POST /compile/run",
        overlap_policy=HTTP_OVERLAP_POLICY,
        retry_contract="No retry; the next daily tick is the retry.",
        idempotence=(
            "Rewrites ~/.claude/skills/mind-* from the current graph. Running "
            "twice produces the same files; it is a projection, not an append."
        ),
        health_signal="A receipt with outcome=ok written within LATE_MULTIPLIER cadences.",
        remediation=(
            "Compilation spends Opus through the Claude CLI. Verify the CLI "
            "answers, then re-run and diff ~/.claude/skills for unexpected churn."
        ),
    ),
)

JOBS_BY_NAME: dict[str, ScheduledJob] = {job.name: job for job in JOB_INVENTORY}

# Installed timers that are deliberately NOT capability-driven jobs. Naming
# them here is what lets the reconciliation test be strict about everything
# else instead of ignoring unknown units.
NON_JOB_UNITS: frozenset[str] = frozenset({
    "corvus-backup.timer",          # host-level backup, no HTTP target
    "corvus-trial-recheck.timer",   # one-shot pathway-trial recheck (2026-08-04)
    "corvus-autopilot.timer",       # legacy; retired autopilot, kept installed
})


# ---- receipts ---------------------------------------------------------------

def _receipt_path(job: str) -> Path:
    return RECEIPTS_DIR / f"{job}.json"


def read_receipt(job: str) -> dict[str, Any] | None:
    path = _receipt_path(job)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A corrupt receipt is itself a finding, but it must not take down the
        # endpoint that reports on it.
        return {"job": job, "outcome": "unreadable",
                "detail": "receipt file exists but could not be parsed"}


def write_receipt(job: str, receipt: dict[str, Any]) -> None:
    RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _receipt_path(job)
    # Write-then-rename: a crash mid-write must not leave a truncated receipt
    # that reads as "job ran and produced nonsense".
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(receipt, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@contextmanager
def job_receipt(job: str, tenant: str) -> Iterator[dict[str, Any]]:
    """Record actual batch outcomes; a healthy no-work check is not a write.

    Interrupted batches have unknown counts. Never serialize an exception or
    arbitrary caller details into the operational receipt.
    """
    started = _now()
    previous = read_receipt(job) or {}
    detail: dict[str, Any] = {}
    try:
        yield detail
    except Exception as exc:
        finished = _now()
        metadata = safe_detail(detail)
        metadata.pop("batch", None)
        write_receipt(job, {
            "job": job, "tenant": tenant, "outcome": "error",
            "started_at": started.isoformat(), "finished_at": finished.isoformat(),
            "duration_ms": round((finished - started).total_seconds() * 1000, 3),
            "exception": exception_reason(exc), "counts_known": False,
            "last_success": previous.get("last_success"),
            "remediation": JOBS_BY_NAME[job].remediation,
            "detail": metadata,
        })
        raise
    finished = _now()
    batch = detail.get("batch")
    outcome = batch.outcome if isinstance(batch, BatchOutcome) else "ok"
    healthy = outcome in {"ok", "no-work"}
    write_receipt(job, {
        "job": job, "tenant": tenant, "outcome": outcome,
        "started_at": started.isoformat(), "finished_at": finished.isoformat(),
        "duration_ms": round((finished - started).total_seconds() * 1000, 3),
        "exception": None if healthy else "returned-item-failure",
        "counts_known": isinstance(batch, BatchOutcome),
        "last_success": finished.isoformat() if healthy else previous.get("last_success"),
        "remediation": "" if healthy else JOBS_BY_NAME[job].remediation,
        "detail": safe_detail(detail),
    })


@contextmanager
def scheduled_run(job: str, tenant: str) -> Iterator[dict[str, Any]]:
    """Correlate the run and log only content-safe, truthful outcome fields."""
    with bound(job=job):
        logger.info("scheduled job starting", extra={"event": "job.start", "job": job})
        try:
            with job_receipt(job, tenant) as detail:
                yield detail
        except Exception as exc:
            logger.error("scheduled job FAILED", extra={
                "event": "job.failed", "job": job, "outcome": "error",
                "reason": exception_reason(exc),
            })
            raise
        receipt = read_receipt(job) or {}
        outcome = receipt.get("outcome", "unreadable")
        healthy = outcome in {"ok", "no-work"}
        log = logger.info if healthy else logger.error
        log("scheduled job complete" if healthy else "scheduled job FAILED", extra={
            "event": "job.complete" if healthy else "job.failed",
            "job": job, "outcome": outcome,
            "duration_ms": receipt.get("duration_ms"),
            "reason": None if healthy else "returned-item-failure",
        })


@contextmanager
def scheduled_http_run(job: str, tenant: str) -> Iterator[dict[str, Any]]:
    """HTTP adapter: retain internal exceptions without leaking them to ASGI.

    The job wrapper records a sanitized failure before this boundary converts
    the exception to a handled HTTP error. A generic 500 handler is too late:
    Starlette rethrows server errors, allowing Uvicorn to log their contents.
    Authentication and request validation dependencies remain outside this
    context and retain their existing semantics.
    """
    from fastapi import HTTPException

    try:
        if job not in JOBS_BY_NAME:
            raise ValueError("unsupported scheduled HTTP job")
        with job_run_lock(job, RECEIPTS_DIR / "locks"):
            with scheduled_run(job, tenant) as detail:
                yield detail
    except JobBusy:
        # The contender did not run: do not replace the owner's receipt with
        # a fabricated failure, or conceal its eventual successful completion.
        raise HTTPException(status_code=409, detail="maintenance-job-busy") from None
    except Exception:
        raise HTTPException(status_code=503, detail="maintenance-job-failed") from None


def job_health(job: ScheduledJob, receipt: dict[str, Any] | None,
               now: datetime | None = None) -> dict[str, Any]:
    """Judge one job from its receipt: ok, late, failing, or never-run."""
    now = now or _now()
    entry: dict[str, Any] = {**job.as_dict(), "receipt": receipt}

    if receipt is None:
        entry["status"] = "never-run"
        entry["detail"] = "no receipt has ever been written for this job"
        return entry

    if receipt.get("outcome") not in {"ok", "no-work"}:
        entry["status"] = "failing"
        entry["detail"] = receipt.get("exception") or "job reported an error"
        return entry

    last_success = receipt.get("last_success")
    if not last_success:
        entry["status"] = "never-run"
        entry["detail"] = "receipt records no successful run"
        return entry

    if job.cadence_seconds:
        try:
            stamp = datetime.fromisoformat(last_success)
        except ValueError:
            entry["status"] = "failing"
            entry["detail"] = f"unparseable last_success: {last_success!r}"
            return entry
        deadline = stamp + timedelta(seconds=job.cadence_seconds * LATE_MULTIPLIER)
        if now > deadline:
            entry["status"] = "late"
            entry["detail"] = (
                f"last success {stamp.isoformat()} is more than "
                f"{LATE_MULTIPLIER}x the {job.cadence} cadence ago"
            )
            return entry

    entry["status"] = "ok"
    entry["detail"] = f"last success {last_success}"
    return entry


def inventory_health(now: datetime | None = None) -> dict[str, Any]:
    """The whole scheduled surface, judged. Feeds GET /metrics/mind/jobs."""
    entries = [job_health(job, read_receipt(job.name), now) for job in JOB_INVENTORY]
    unhealthy = [e for e in entries if e["status"] != "ok"]
    return {
        "jobs": entries,
        "counts": {
            status: sum(1 for e in entries if e["status"] == status)
            for status in ("ok", "late", "failing", "never-run")
        },
        "healthy": not unhealthy,
        "late_multiplier": LATE_MULTIPLIER,
        "late_multiplier_basis": "hypothesis; revisit against observed run intervals",
    }
