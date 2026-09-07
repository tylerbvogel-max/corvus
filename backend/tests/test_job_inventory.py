"""Scheduled jobs must be inventoried, and their health must be their own.

An inventory that drifts from reality is worse than none: it reads as coverage
while describing a system that no longer exists. These tests reconcile the
declared inventory against the units actually installed, in BOTH directions.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

from app.observability import jobs as jobs_module
from app.observability.jobs import (
    JOB_INVENTORY, JOBS_BY_NAME, LATE_MULTIPLIER, NON_JOB_UNITS,
    ScheduledJob, inventory_health, job_health, job_receipt, read_receipt,
    scheduled_run, write_receipt,
)


@pytest.fixture(autouse=True)
def _isolated_receipts(tmp_path, monkeypatch):
    """Never read or write the real ~/.corvus-mind receipts from a test."""
    monkeypatch.setattr(jobs_module, "RECEIPTS_DIR", tmp_path / "receipts")


# ---- inventory completeness -------------------------------------------------

def test_every_job_declares_what_an_operator_needs_at_3am():
    for job in JOB_INVENTORY:
        assert job.owner, f"{job.name} has no owner"
        assert job.cadence, f"{job.name} has no cadence"
        assert job.overlap_policy, f"{job.name} has no overlap policy"
        assert job.retry_contract, f"{job.name} has no retry contract"
        assert job.idempotence, f"{job.name} has no idempotence contract"
        assert job.health_signal, f"{job.name} has no health signal"
        assert job.remediation, f"{job.name} has no remediation guidance"
        assert job.unit.endswith(".timer"), f"{job.name} unit is not a timer"


def test_job_names_and_units_are_unique():
    assert len({j.name for j in JOB_INVENTORY}) == len(JOB_INVENTORY)
    assert len({j.unit for j in JOB_INVENTORY}) == len(JOB_INVENTORY)


def test_declared_units_never_collide_with_the_not_a_job_list():
    assert not {j.unit for j in JOB_INVENTORY} & NON_JOB_UNITS


# ---- receipts ---------------------------------------------------------------

def test_a_successful_run_records_its_success():
    with job_receipt("janitor", "corvus-mind") as detail:
        detail["passes"] = ["decay"]
    receipt = read_receipt("janitor")
    assert receipt["outcome"] == "ok"
    assert receipt["last_success"]
    assert receipt["detail"]["passes"] == ["decay"]
    assert receipt["exception"] is None


def test_a_failing_run_records_the_exception_and_keeps_the_prior_success():
    """'When did this last work' is the first question about a broken job."""
    with job_receipt("janitor", "corvus-mind"):
        pass
    first_success = read_receipt("janitor")["last_success"]

    with pytest.raises(RuntimeError):
        with job_receipt("janitor", "corvus-mind"):
            raise RuntimeError("decay pass exploded")

    receipt = read_receipt("janitor")
    assert receipt["outcome"] == "error"
    assert receipt["exception"] == "job-execution-failure"
    assert receipt["last_success"] == first_success, (
        "a failed run erased the record of when the job last worked"
    )
    assert receipt["remediation"], "a failure receipt carries no remediation"


def test_receipt_carries_job_identity_and_tenant():
    """Criterion 4: the alert must name what broke and where."""
    with pytest.raises(ValueError):
        with job_receipt("distill", "corvus-locomo"):
            raise ValueError("boom")
    receipt = read_receipt("distill")
    assert receipt["job"] == "distill"
    assert receipt["tenant"] == "corvus-locomo"


def test_unreadable_receipt_does_not_take_down_the_reporting_path():
    jobs_module.RECEIPTS_DIR.mkdir(parents=True, exist_ok=True)
    (jobs_module.RECEIPTS_DIR / "compile.json").write_text("{ not json")
    assert read_receipt("compile")["outcome"] == "unreadable"


def test_receipt_write_is_atomic_leaving_no_partial_file():
    write_receipt("auditor", {"job": "auditor", "outcome": "ok"})
    leftovers = list(jobs_module.RECEIPTS_DIR.glob("*.tmp"))
    assert not leftovers, f"temp files left behind: {leftovers}"
    assert json.loads((jobs_module.RECEIPTS_DIR / "auditor.json").read_text())


# ---- health judging ---------------------------------------------------------

def _job(**over) -> ScheduledJob:
    base = dict(
        name="t", unit="t.timer", owner="o", cadence="hourly",
        cadence_seconds=3600, endpoint="POST /t", overlap_policy="p",
        retry_contract="r", idempotence="i", health_signal="h", remediation="m",
    )
    base.update(over)
    return ScheduledJob(**base)


def test_a_job_that_never_ran_is_not_reported_healthy():
    assert job_health(_job(), None)["status"] == "never-run"


def test_a_recent_success_is_healthy():
    now = datetime.now(timezone.utc)
    receipt = {"outcome": "ok", "last_success": (now - timedelta(minutes=5)).isoformat()}
    assert job_health(_job(), receipt, now)["status"] == "ok"


def test_a_job_that_stopped_running_goes_late():
    """The canonical silent failure: nothing errored, it simply stopped."""
    now = datetime.now(timezone.utc)
    stale = now - timedelta(seconds=3600 * LATE_MULTIPLIER + 60)
    entry = job_health(_job(), {"outcome": "ok", "last_success": stale.isoformat()}, now)
    assert entry["status"] == "late"
    assert "cadence" in entry["detail"]


def test_an_errored_job_is_failing_and_surfaces_its_exception():
    entry = job_health(_job(), {"outcome": "error", "exception": "ValueError: nope"})
    assert entry["status"] == "failing"
    assert "ValueError: nope" in entry["detail"]


def test_unparseable_timestamp_is_a_failure_not_a_pass():
    entry = job_health(_job(), {"outcome": "ok", "last_success": "not-a-date"})
    assert entry["status"] == "failing"


def test_inventory_health_is_unhealthy_when_any_job_is():
    report = inventory_health()          # no receipts written yet
    assert report["healthy"] is False
    assert report["counts"]["never-run"] == len(JOB_INVENTORY)
    assert report["late_multiplier_basis"].startswith("hypothesis")


def test_inventory_health_is_healthy_once_every_job_has_reported():
    for job in JOB_INVENTORY:
        with job_receipt(job.name, "corvus-mind"):
            pass
    report = inventory_health()
    assert report["healthy"] is True
    assert report["counts"]["ok"] == len(JOB_INVENTORY)


# ---- the wrapper the endpoints use -----------------------------------------

def test_scheduled_run_writes_a_receipt_and_reraises_on_failure():
    with pytest.raises(RuntimeError):
        with scheduled_run("compile", "corvus-mind"):
            raise RuntimeError("compiler died")
    receipt = read_receipt("compile")
    assert receipt["outcome"] == "error"
    assert receipt["exception"] == "job-execution-failure"


def test_scheduled_run_binds_job_correlation():
    from app.observability.context import current
    with scheduled_run("janitor", "corvus-mind"):
        assert current()["job"] == "janitor"


# ---- reconciliation against the real machine --------------------------------

@pytest.mark.integration
def test_declared_jobs_match_the_timers_actually_installed():
    """Both directions: an undeclared timer fails, and so does a phantom entry.

    Runs `systemctl --user`, so this cannot be hermetic. Skips rather than
    fails where systemd user units are unavailable (CI containers), because a
    missing supervisor is not evidence that the inventory is wrong.
    """
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "list-unit-files", "--type=timer",
             "--no-legend", "--no-pager"],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"systemd user manager unavailable: {exc}")
    if proc.returncode != 0:
        pytest.skip(f"systemctl unavailable: {proc.stderr.strip()[:120]}")

    installed = {
        line.split()[0] for line in proc.stdout.strip().splitlines()
        if line.split() and line.split()[0].startswith("corvus")
    }
    if not installed:
        pytest.skip("no corvus timers installed on this host")

    declared = {job.unit for job in JOB_INVENTORY}

    undeclared = installed - declared - NON_JOB_UNITS
    assert not undeclared, (
        f"timers are installed but absent from JOB_INVENTORY: {sorted(undeclared)}. "
        "An uninventoried job is one nothing is watching."
    )

    phantom = declared - installed
    assert not phantom, (
        f"JOB_INVENTORY declares timers that are not installed: {sorted(phantom)}. "
        "An inventory describing jobs that do not exist reads as coverage."
    )


@pytest.mark.integration
def test_no_scheduled_job_invokes_curl_without_failing_on_http_errors():
    """THE CANONICAL SILENT FAILURE, pinned.

    `curl -s` exits 0 on an HTTP error, so a timer using it reports success to
    systemd no matter what the endpoint answered. Measured on this machine: a
    405 returned exit 0, while `curl -sf` returned 22.
    """
    offenders = []
    for job in JOB_INVENTORY:
        service = job.unit.replace(".timer", ".service")
        try:
            proc = subprocess.run(
                ["systemctl", "--user", "cat", service],
                capture_output=True, text=True, timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            pytest.skip(f"systemd user manager unavailable: {exc}")
        if proc.returncode != 0:
            pytest.skip(f"{service} not installed on this host")
        for line in proc.stdout.splitlines():
            if not line.startswith("ExecStart") or "curl" not in line:
                continue
            if not any(flag in line for flag in (" -f", "--fail", "-sf", "-fsS")):
                offenders.append(f"{service}: {line.strip()}")

    assert not offenders, (
        "these units invoke curl without --fail, so an HTTP error exits 0 and "
        "systemd records success:\n  " + "\n  ".join(offenders)
    )
