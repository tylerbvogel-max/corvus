#!/usr/bin/env python3
"""Thin Codex lifecycle adapter for the shared Corvus-Mind Python hooks.

Codex command hooks already use the Claude Code event names.  This adapter
only normalizes the few tool payload aliases Codex may emit, then delegates
stdin/stdout unchanged.  Recall, capsule delivery, redaction, exclusion, and
episode semantics remain implemented once in harness/claude-code/.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


SHARED_DIR = Path(__file__).resolve().parents[1] / "claude-code"
INJECTION_EVENTS = {"SessionStart", "UserPromptSubmit", "PreToolUse"}
CAPTURE_EVENTS = {"PostToolUse", "Stop"}
TIMEOUT_SECONDS = 10


def _normalize(payload: dict) -> dict:
    """Map Codex aliases without changing Claude-shaped payloads."""
    normalized = dict(payload)
    normalized["harness"] = "codex"
    normalized.setdefault("tool_name", payload.get("tool"))
    normalized.setdefault("tool_input", payload.get("tool_input") or payload.get("input"))
    normalized.setdefault(
        "tool_response", payload.get("tool_response") or payload.get("tool_result") or payload.get("response")
    )
    return normalized


def main() -> int:
    payload = _normalize(json.load(sys.stdin))
    event = payload.get("hook_event_name", "")
    if event in INJECTION_EVENTS:
        target = SHARED_DIR / "memory_inject_hook.py"
    elif event in CAPTURE_EVENTS:
        target = SHARED_DIR / "episode_hook.py"
    else:
        return 0

    # These hooks are stdlib-only.  Preserve the active interpreter and avoid
    # introducing any model-provider or API-key behavior.
    result = subprocess.run(
        [sys.executable, str(target)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
        env=os.environ.copy(),
        check=False,
    )
    if result.stdout:
        sys.stdout.write(result.stdout)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:  # Hooks must never break a Codex session.
        raise SystemExit(0)
