"""Lane entry point for the end-to-end reconsolidation kernel replay.

The proof itself lives in tests/replay_nvm_throwaway.py. This module only
makes it ADDRESSABLE to scripts/run_test_lane.py (lane `kernel-replay`), so
the strongest guarantee the memory system makes about its most dangerous
write path is something a person can run by name instead of remembering a
bespoke command line.

WHY A SUBPROCESS, NOT AN IMPORT. The replay executes `DROP SCHEMA public
CASCADE` against whatever `DATABASE_URL` names, and it sets that variable at
import time. Importing it into a live pytest process would let any module
that already built an engine decide which database gets dropped. A clean
interpreter makes the target unambiguous. It also keeps the replay runnable
exactly as its own docstring documents.

WHY THIS IS NOT IN THE MERGE GATE: see scripts/run_test_lane.py.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.database

REPLAY = Path(__file__).parent / "replay_nvm_throwaway.py"
_DISPOSABLE_PREFIXES = ("corvus_test_", "corvus_migration_")


@pytest.mark.timeout(900)
def test_kernel_replay_end_to_end():
    """Run the golden NVM replay against a throwaway database.

    Skips unless REPLAY_DB names a disposable database, because this test
    DESTROYS the schema it runs against. The database must already exist —
    the yggdrasil role lacks CREATEDB, so create it once with:

        sudo -u postgres psql -c \\
          'CREATE DATABASE corvus_test_kernel_replay OWNER yggdrasil'
    """
    replay_db = os.environ.get("REPLAY_DB", "").strip()
    if not replay_db:
        pytest.skip("kernel-replay lane requires REPLAY_DB")
    # Belt and braces: the script refuses corvus_mind by name, and the lane
    # preflight checks the prefix. Neither is allowed to be the only check.
    assert replay_db != "corvus_mind", "refusing to run against the live database"
    assert replay_db.startswith(_DISPOSABLE_PREFIXES), (
        f"REPLAY_DB={replay_db!r} is not a disposable database name; "
        f"this test drops the schema it runs against"
    )

    proc = subprocess.run(
        [sys.executable, str(REPLAY)],
        cwd=str(REPLAY.parent.parent),
        env={**os.environ, "PYTHONPATH": ".", "REPLAY_DB": replay_db},
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        pytest.fail(
            "kernel replay failed. If the failure is a packet violation, the "
            "FIXTURE is stale and tests/test_kernel_replay_fixture_guard.py "
            "should have caught it first — check that guard before suspecting "
            f"the kernel.\n\n{proc.stdout[-6000:]}\n{proc.stderr[-6000:]}"
        )
    assert "ALL REPLAY ASSERTIONS PASSED" in proc.stdout, proc.stdout[-4000:]
