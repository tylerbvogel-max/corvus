#!/usr/bin/env python3
"""Single-command verification that a deployed Corvus artifact is actually serving.

INTERIM. Roadmap record durability-operational-envelope (06) owns the real
operational verification contract — health semantics, SLOs, and readiness vs
liveness. That record is still `planned`, and record 07's release dry-run cannot
be performed without *some* single command to run after deploying. This is that
command, deliberately minimal, and it should be absorbed by 06 rather than grown
here.

What it asserts today:
  1. The service answers /health with status "ok".
  2. The database schema is at the expected Alembic revision — a container can
     report healthy while its migrations silently failed to reach head.
  3. The running container's recorded provenance matches the artifact that was
     supposed to be deployed, so "we deployed the tested build" is checkable
     rather than assumed.

Exit codes: 0 verified, 1 a check failed, 2 could not run a check.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request


def _check_health(url: str, timeout: float) -> tuple[bool, str]:
    endpoint = url.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(endpoint, timeout=timeout) as response:
            if response.status != 200:
                return False, f"{endpoint} returned HTTP {response.status}"
            payload = json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"{endpoint} unreachable: {exc}"
    status = payload.get("status")
    if status != "ok":
        return False, f"{endpoint} reported status {status!r}"
    return True, f"health ok ({payload})"


def _check_revision(database_url: str, expected: str) -> tuple[bool, str]:
    # psql keeps this dependency-free; the point is to check the deployment from
    # OUTSIDE the application, not to ask the application about itself.
    proc = subprocess.run(
        ["psql", database_url, "-At", "-c", "SELECT version_num FROM alembic_version"],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        return False, f"could not read alembic_version: {proc.stderr.strip()}"
    actual = proc.stdout.strip()
    if not actual:
        return False, "alembic_version is empty; migrations never ran"
    if expected and actual != expected:
        return False, f"schema at {actual!r}, expected {expected!r}"
    return True, f"schema at {actual}"


def _check_provenance(container: str, expected_revision: str) -> tuple[bool, str]:
    proc = subprocess.run(
        ["docker", "inspect", "--format",
         "{{index .Config.Labels \"org.opencontainers.image.revision\"}}", container],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        return False, f"could not inspect {container}: {proc.stderr.strip()}"
    actual = proc.stdout.strip()
    if expected_revision and actual != expected_revision:
        return False, (
            f"deployed artifact reports revision {actual!r}, expected "
            f"{expected_revision!r} — this is not the build that was tested"
        )
    return True, f"artifact revision {actual}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8005")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--database-url", default="")
    parser.add_argument("--expect-revision", default="",
                        help="expected Alembic revision, e.g. 025_standard_date_seed")
    parser.add_argument("--container", default="",
                        help="container name to check provenance labels on")
    parser.add_argument("--expect-source-revision", default="",
                        help="expected git revision baked into the image")
    args = parser.parse_args()

    checks: list[tuple[str, tuple[bool, str]]] = [
        ("health", _check_health(args.url, args.timeout)),
    ]
    if args.database_url:
        checks.append(
            ("schema", _check_revision(args.database_url, args.expect_revision))
        )
    if args.container:
        checks.append(
            ("provenance",
             _check_provenance(args.container, args.expect_source_revision))
        )

    failed = 0
    for name, (ok, detail) in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
        failed += 0 if ok else 1

    if failed:
        print(f"{failed} check(s) failed", file=sys.stderr)
        return 1
    print(f"deployment verified ({len(checks)} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
