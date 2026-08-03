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
JOB_INVENTORY: tuple[ScheduledJob, ...] = (
    ScheduledJob(
        name="distill",
        unit="corvus-mind-distill.timer",
        owner="memory-ingestion",
        cadence="every 30 minutes (OnUnitActiveSec=30min, OnBootSec=15min)",
        cadence_seconds=30 * 60,
        endpoint="POST /distill/run",
        overlap_policy=(
            "systemd serializes: Type=oneshot on a single unit cannot start "
            "again while the previous run is active, so a long distill delays "
            "the next tick rather than doubling up."
        ),
        retry_contract="No retry. The next tick is the retry, 30 minutes later.",
        idempotence=(
            "Idempotent by episode watermark: already-distilled sessions are "
            "skipped, so a repeated run re-reads nothing it has consumed."
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
        overlap_policy="systemd serializes (Type=oneshot, single unit).",
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
        overlap_policy="systemd serializes (Type=oneshot, single unit).",
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
        overlap_policy="systemd serializes (Type=oneshot, single unit).",
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
    """Record what a scheduled run did, including when it failed.

    The failure path is the whole point. On success the receipt records
    last_success; on failure it preserves the exception and the PREVIOUS
    success time, because "when did this last work" is the first question
    asked about a job that is now broken.
    """
    started = _now()
    previous = read_receipt(job) or {}
    detail: dict[str, Any] = {}
    try:
        yield detail
    except Exception as exc:
        write_receipt(job, {
            "job": job,
            "tenant": tenant,
            "outcome": "error",
            "started_at": started.isoformat(),
            "finished_at": _now().isoformat(),
            "duration_ms": round((_now() - started).total_seconds() * 1000),
            "exception": f"{type(exc).__name__}: {exc}",
            "last_success": previous.get("last_success"),
            "remediation": JOBS_BY_NAME[job].remediation if job in JOBS_BY_NAME else "",
            "detail": detail,
        })
        raise
    finished = _now()
    write_receipt(job, {
        "job": job,
        "tenant": tenant,
        "outcome": "ok",
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_ms": round((finished - started).total_seconds() * 1000),
        "exception": None,
        "last_success": finished.isoformat(),
        "remediation": "",
        "detail": detail,
    })


@contextmanager
def scheduled_run(job: str, tenant: str) -> Iterator[dict[str, Any]]:
    """The one wrapper every scheduled endpoint uses.

    Binds job correlation so each line the run emits is attributable, logs a
    start/complete pair, and writes the receipt that is the job's real health
    signal. Combined into one helper because four handlers repeating three
    concerns is how they drift apart — and the failure path is the one that
    must not be forgotten in the fourth copy.
    """
    logger = logging.getLogger("app.observability.jobs")
    with bound(job=job):
        logger.info("scheduled job starting", extra={
            "event": "job.start", "job": job,
        })
        try:
            with job_receipt(job, tenant) as detail:
                yield detail
        except Exception as exc:
            logger.error("scheduled job FAILED", extra={
                "event": "job.failed", "job": job, "outcome": "error",
                "reason": f"{type(exc).__name__}: {exc}",
            }, exc_info=True)
            raise
        receipt = read_receipt(job) or {}
        logger.info("scheduled job complete", extra={
            "event": "job.complete", "job": job, "outcome": "ok",
            "duration_ms": receipt.get("duration_ms"),
        })


# ---- health ----------------------------------------------------------------

def job_health(job: ScheduledJob, receipt: dict[str, Any] | None,
               now: datetime | None = None) -> dict[str, Any]:
    """Judge one job from its receipt: ok, late, failing, or never-run."""
    now = now or _now()
    entry: dict[str, Any] = {**job.as_dict(), "receipt": receipt}

    if receipt is None:
        entry["status"] = "never-run"
        entry["detail"] = "no receipt has ever been written for this job"
        return entry

    if receipt.get("outcome") == "error":
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
