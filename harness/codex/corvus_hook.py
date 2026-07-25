#!/usr/bin/env python3
"""Thin Codex lifecycle adapter for the shared Corvus-Mind Python hooks.

Codex command hooks already use the Claude Code event names.  This adapter
only normalizes the few tool payload aliases Codex may emit, then delegates
stdin/stdout unchanged.  Recall, planning admission, redaction, exclusion, and
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


def _run(target: Path, payload: dict) -> str:
    """Run one stdlib-only shared hook with the active interpreter."""
    result = subprocess.run(
        [sys.executable, str(target)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=TIMEOUT_SECONDS,
        env=os.environ.copy(),
        check=False,
    )
    return result.stdout


def _merge(outputs: list[str], event: str) -> str:
    """Combine memory context and admission decisions into one Codex envelope."""
    parsed = []
    for output in outputs:
        if not output.strip():
            continue
        try:
            parsed.append(json.loads(output))
        except ValueError:
            continue
    if not parsed:
        return ""

    contexts = []
    merged: dict = {}
    hook_output: dict = {"hookEventName": event}
    for item in parsed:
        if item.get("systemMessage"):
            merged["systemMessage"] = item["systemMessage"]
        if item.get("roadmapGate"):
            merged["roadmapGate"] = item["roadmapGate"]
        specific = item.get("hookSpecificOutput")
        if not isinstance(specific, dict):
            continue
        if specific.get("additionalContext"):
            contexts.append(specific["additionalContext"])
        if specific.get("permissionDecision"):
            hook_output["permissionDecision"] = specific["permissionDecision"]
            hook_output["permissionDecisionReason"] = specific.get(
                "permissionDecisionReason"
            )
        if specific.get("updatedInput") is not None:
            hook_output["updatedInput"] = specific["updatedInput"]
    if contexts:
        hook_output["additionalContext"] = "\n\n".join(contexts)
    if len(hook_output) > 1:
        merged["hookSpecificOutput"] = hook_output
    return json.dumps(merged)


def main() -> int:
    payload = _normalize(json.load(sys.stdin))
    event = payload.get("hook_event_name", "")
    if event in INJECTION_EVENTS:
        targets = [
            SHARED_DIR / "memory_inject_hook.py",
            SHARED_DIR / "roadmap_gate_hook.py",
        ]
    elif event in CAPTURE_EVENTS:
        targets = [SHARED_DIR / "episode_hook.py"]
    else:
        return 0

    merged = _merge([_run(target, payload) for target in targets], event)
    if merged:
        sys.stdout.write(merged)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:  # Hooks must never break a Codex session.
        raise SystemExit(0)
