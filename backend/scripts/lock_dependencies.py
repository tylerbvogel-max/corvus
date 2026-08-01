#!/usr/bin/env python3
"""Compile backend/pyproject.toml into the hash-verified requirements.txt lock.

The lock is the only thing CI, Docker, and local installs read. This script is
the only supported way to regenerate it, so that two people running it from the
same commit produce the same file.

The resolver is `uv`. pip-tools was evaluated first and rejected: pip-tools 7.x
imports `pip._internal.utils.compat.stdlib_pkgs`, which modern pip no longer
exports, so it cannot run against a current pip without pinning pip itself
backwards. A lock tool that needs its own tool pinned is not a lock strategy.

Determinism notes:
  --generate-hashes      every artifact is pinned by content, not just version
  --no-header            uv stamps its own argv into a header, which makes the
                         file differ by invocation path; we write our own stable
                         provenance header instead
  --emit-index-url       the index the hashes were resolved against is part of
                         the lock's meaning
  --python-version       resolve for the one supported interpreter, not for
                         whatever happens to be running this script
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = BACKEND_ROOT / "pyproject.toml"
LOCK = BACKEND_ROOT / "requirements.txt"

# Must stay inside the requires-python range declared in pyproject.toml.
SUPPORTED_PYTHON = "3.11"

HEADER = """\
# GENERATED FILE — DO NOT EDIT BY HAND.
#
# Compiled from pyproject.toml by scripts/lock_dependencies.py.
# To change a dependency, edit pyproject.toml and re-run that script.
#
# This lock is fully pinned and hash-verified: `pip install -r requirements.txt`
# will refuse any artifact whose content does not match, which is what makes a
# clean-checkout build reproducible rather than merely repeatable.
"""


def _run(cmd: list[str]) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, cwd=BACKEND_ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if the committed lock differs from a fresh compile",
    )
    parser.add_argument(
        "--extra",
        action="append",
        default=["test"],
        help="optional-dependency group to include (repeatable)",
    )
    parser.add_argument(
        "--upgrade",
        action="store_true",
        help=(
            "deliberately re-resolve to the newest versions satisfying "
            "pyproject.toml instead of preserving the committed pins"
        ),
    )
    args = parser.parse_args()

    uv = shutil.which("uv") or str(Path(sys.executable).with_name("uv"))
    if not Path(uv).exists():
        print(
            "uv is not available.\n  pip install uv",
            file=sys.stderr,
        )
        return 2

    target = LOCK if not args.check else LOCK.with_suffix(".check")

    # uv reads an existing --output-file as resolution PREFERENCES, so a package
    # already pinned in the committed lock stays put instead of drifting to the
    # newest release on every regeneration. That is the behaviour we want, but it
    # means the check has to compile against the same preference set the real
    # regeneration would see — otherwise it reports spurious staleness the moment
    # any upstream publishes a new version. Seed the scratch target from the
    # committed lock so --check measures "does this lock still satisfy
    # pyproject.toml", not "has PyPI moved since we locked".
    if args.check:
        target.write_text(LOCK.read_text())

    cmd = [
        uv,
        "pip",
        "compile",
        "--generate-hashes",
        "--no-header",
        "--emit-index-url",
        "--python-version",
        SUPPORTED_PYTHON,
        "--output-file",
        str(target),
    ]
    if args.upgrade:
        cmd.append("--upgrade")
    for extra in args.extra:
        cmd += ["--extra", extra]
    cmd.append(str(PYPROJECT))

    # SOURCE_DATE_EPOCH keeps any timestamp-sensitive resolver output stable.
    os.environ.setdefault("SOURCE_DATE_EPOCH", "0")
    _run(cmd)

    body = target.read_text()
    if not body.startswith("#"):
        body = "\n" + body
    target.write_text(HEADER + body)

    if args.check:
        committed = LOCK.read_text()
        fresh = target.read_text()
        target.unlink()
        if committed != fresh:
            print(
                "requirements.txt is stale: it does not match a fresh compile of "
                "pyproject.toml. Run scripts/lock_dependencies.py and commit.",
                file=sys.stderr,
            )
            return 1
        print("lock is current")
    else:
        print(f"wrote {LOCK}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
