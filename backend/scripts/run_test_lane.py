#!/usr/bin/env python3
"""Run one bounded Corvus test lane and preserve diagnostic artifacts."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import datetime as dt
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from sqlalchemy.engine import make_url


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent


@dataclass(frozen=True)
class Lane:
    marker: str
    targets: tuple[str, ...]
    job_timeout_seconds: int
    requirement: str
    extra_args: tuple[str, ...] = ()


LANES = {
    "required-backend": Lane(
        marker="hermetic or integration",
        targets=("tests",),
        job_timeout_seconds=300,
        requirement=(
            "composite PR selection: no external database, network, provider, "
            "or service"
        ),
    ),
    "hermetic": Lane(
        marker="hermetic",
        targets=("tests",),
        job_timeout_seconds=240,
        requirement="no database, network, provider, or external service",
    ),
    "integration": Lane(
        marker="integration",
        targets=("tests",),
        job_timeout_seconds=120,
        requirement="in-process application dependencies only; external I/O is replaced",
    ),
    "database": Lane(
        marker="database and not snapshot",
        targets=("tests/test_migration_smoke.py",),
        job_timeout_seconds=300,
        requirement="CORVUS_TEST_DATABASE_URL naming a disposable corvus_test_* database",
    ),
    # The reconsolidation kernel's only end-to-end proof against real SQL,
    # the real one-step lifecycle and the real action bus. It is NOT in the
    # merge gate, and that is a deliberate decision recorded in
    # kernel-replay-ungated (2026-08-01):
    #
    #   - It destroys and rebuilds a schema, and the database must be
    #     created out of band (the yggdrasil role lacks CREATEDB), so it
    #     cannot be made a self-provisioning PR job the way the `database`
    #     lane's migration smoke test is.
    #   - Folding it into the `database` lane would have coupled the merge
    #     gate's cheapest DB job to a multi-minute full-kernel apply.
    #   - What actually rotted was its INPUTS, not the kernel. That failure
    #     mode is now covered on every PR, in milliseconds and with no
    #     database, by tests/test_kernel_replay_fixture_guard.py — which
    #     runs in the hermetic and required-backend lanes and fails with the
    #     exact packet violation.
    #
    # So: the expensive proof stays opt-in and NAMED, while a cheap
    # always-run guard defends the thing that was observed to break.
    "kernel-replay": Lane(
        marker="database",
        targets=("tests/test_kernel_replay_endtoend.py",),
        job_timeout_seconds=900,
        requirement=(
            "REPLAY_DB naming an existing disposable corvus_test_* database; "
            "the schema is dropped and rebuilt"
        ),
    ),
    "evaluation": Lane(
        marker="evaluation",
        targets=(
            str(REPO_ROOT / "eval/locomo/test_lifecycle.py"),
            str(REPO_ROOT / "eval/locomo/test_oracle_funnel.py"),
            str(REPO_ROOT / "eval/locomo/test_verifier_split.py"),
        ),
        job_timeout_seconds=300,
        requirement="frozen local fixtures only; this lane does not execute a benchmark",
    ),
    "live-provider": Lane(
        marker="live_provider",
        targets=("tests/test_live_provider.py",),
        job_timeout_seconds=180,
        requirement=(
            "CORVUS_RUN_LIVE_PROVIDER=1, CORVUS_LIVE_PROVIDER_MODEL, and "
            "working credentials or an authenticated provider CLI"
        ),
        extra_args=("--run-live-provider",),
    ),
}


def _preflight(name: str) -> None:
    if name == "database":
        value = os.environ.get("CORVUS_TEST_DATABASE_URL", "")
        if not value:
            raise SystemExit("database lane requires CORVUS_TEST_DATABASE_URL")
        database = make_url(value).database or ""
        if not database.startswith(("corvus_test_", "corvus_migration_")):
            raise SystemExit(
                "database lane refuses non-disposable database name "
                f"{database!r}; use corvus_test_* or corvus_migration_*"
            )
    if name == "kernel-replay":
        replay_db = os.environ.get("REPLAY_DB", "")
        if not replay_db:
            raise SystemExit("kernel-replay lane requires REPLAY_DB")
        if not replay_db.startswith(("corvus_test_", "corvus_migration_")):
            raise SystemExit(
                "kernel-replay lane refuses non-disposable database name "
                f"{replay_db!r}; this lane DROPS the schema it runs against"
            )
    if name == "live-provider":
        if os.environ.get("CORVUS_RUN_LIVE_PROVIDER") != "1":
            raise SystemExit("live-provider lane requires CORVUS_RUN_LIVE_PROVIDER=1")
        if not os.environ.get("CORVUS_LIVE_PROVIDER_MODEL", "").strip():
            raise SystemExit("live-provider lane requires CORVUS_LIVE_PROVIDER_MODEL")


def _terminate_with_stacks(process: subprocess.Popen[str]) -> str:
    notes = []
    if hasattr(signal, "SIGUSR1"):
        try:
            os.killpg(process.pid, signal.SIGUSR1)
            notes.append("sent SIGUSR1 for all-thread faulthandler dump")
            time.sleep(1)
        except ProcessLookupError:
            pass
    try:
        os.killpg(process.pid, signal.SIGTERM)
        notes.append("sent SIGTERM")
        process.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
            notes.append("sent SIGKILL after 10-second grace period")
        except ProcessLookupError:
            pass
    return "; ".join(notes)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lane", choices=tuple(LANES))
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=REPO_ROOT / ".artifacts/test-lanes",
    )
    args = parser.parse_args()
    lane = LANES[args.lane]
    _preflight(args.lane)

    artifact_dir = args.artifact_root.resolve() / args.lane
    artifact_dir.mkdir(parents=True, exist_ok=True)
    junit_path = artifact_dir / "junit.xml"
    pytest_log = artifact_dir / "pytest.log"
    output_path = artifact_dir / "output.log"
    metadata_path = artifact_dir / "metadata.json"

    command = [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        str(BACKEND_ROOT / "pytest.ini"),
        *lane.targets,
        "-m",
        lane.marker,
        *lane.extra_args,
        "-vv",
        "--tb=long",
        "--durations=25",
        f"--junitxml={junit_path}",
        f"--log-file={pytest_log}",
        "--log-file-level=DEBUG",
    ]
    started_wall = dt.datetime.now(dt.timezone.utc)
    started = time.monotonic()
    timed_out = False
    termination = ""
    process = subprocess.Popen(
        command,
        cwd=BACKEND_ROOT,
        env={**os.environ, "PYTHONPATH": ".", "PYTHONFAULTHANDLER": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        output, _ = process.communicate(timeout=lane.job_timeout_seconds)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        partial = exc.output or ""
        if isinstance(partial, bytes):
            partial = partial.decode(errors="replace")
        termination = _terminate_with_stacks(process)
        remainder, _ = process.communicate()
        output = partial + (remainder or "")

    duration = time.monotonic() - started
    output_path.write_text(output)
    print(output, end="")
    return_code = 124 if timed_out else int(process.returncode or 0)
    metadata = {
        "schema_version": 1,
        "lane": args.lane,
        "marker_expression": lane.marker,
        "requirement": lane.requirement,
        "command": command,
        "started_at": started_wall.isoformat(),
        "duration_seconds": round(duration, 3),
        "job_timeout_seconds": lane.job_timeout_seconds,
        "timed_out": timed_out,
        "termination": termination,
        "return_code": return_code,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    if return_code:
        (artifact_dir / "failure-tail.log").write_text(
            "\n".join(output.splitlines()[-250:]) + "\n"
        )
    print(json.dumps(metadata, sort_keys=True))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
