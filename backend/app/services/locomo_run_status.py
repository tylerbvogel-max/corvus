"""LoCoMo certificate run status — derived from run logs on disk.

The benchmark phases run as detached systemd user units writing marker lines
(PHASE-A-DONE / PHASE-A-FAILED, PHASE-B-DONE / PHASE-B-FAILED) into logs under
~/.corvus-mind/evals/locomo/. This service turns those markers into a small
status document for the Pallium beacon, so "is the run done?" is answerable
from any browser tab with no session attached. Read-only: no DB, no LLM.
"""

import glob
import os
from datetime import datetime, timezone

EVALS_DIR = os.path.expanduser("~/.corvus-mind/evals/locomo")
RESULTS_DIR = os.path.expanduser("~/Projects/corvus/eval/locomo/results")
CERT_TOTAL_CONVERSATIONS = 10


def _phase_status(log_glob: str, done_marker: str, failed_marker: str,
                  progress_prefix: str) -> dict:
    logs = sorted(glob.glob(os.path.join(EVALS_DIR, log_glob)),
                  key=os.path.getmtime)
    if not logs:
        return {"state": "pending", "detail": None, "log": None,
                "changed_at": None}
    log = logs[-1]
    try:
        text = open(log, encoding="utf-8", errors="replace").read()
    except OSError:
        return {"state": "pending", "detail": None, "log": log,
                "changed_at": None}
    if failed_marker in text:
        state = "failed"
    elif done_marker in text:
        state = "done"
    else:
        state = "running"
    detail = None
    for line in reversed(text.splitlines()):
        if line.startswith(progress_prefix):
            detail = line.strip()
            break
    changed_at = datetime.fromtimestamp(
        os.path.getmtime(log), tz=timezone.utc).isoformat(timespec="seconds")
    return {"state": state, "detail": detail, "log": log,
            "changed_at": changed_at}


def run_status() -> dict:
    """Phase A/B states plus scored-conversation progress for the beacon."""
    scored = len(glob.glob(os.path.join(
        RESULTS_DIR, "conv*-summary-strict-full-lifecycle.json")))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "phase_a": _phase_status("phase-a-*.log",
                                 "PHASE-A-DONE", "PHASE-A-FAILED",
                                 "=== PHASE A"),
        "phase_b": _phase_status("phase-b-*.log",
                                 "PHASE-B-DONE", "PHASE-B-FAILED",
                                 "=== CONV"),
        "scored_conversations": scored,
        "total_conversations": CERT_TOTAL_CONVERSATIONS,
    }
