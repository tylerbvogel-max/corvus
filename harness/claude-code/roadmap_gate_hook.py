#!/usr/bin/env python3
"""Lifecycle hook for deterministic roadmap context and mutation admission."""

from __future__ import annotations

import json
import sys

import roadmap_admission


def _block_output(payload: dict, decision: dict) -> dict:
    harness = payload.get("harness") or "claude_code"
    reason = decision["reason"]
    # Claude Code and Codex intentionally use different decision vocabularies.
    permission_decision = "block" if harness == "codex" else "deny"
    return {
        "roadmapGate": {
            "decision": "block",
            "reason": reason,
            "ledger_slug": decision["ledger"].get("slug"),
            "ledger_revision": decision["ledger"].get("revision"),
        },
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": permission_decision,
            "permissionDecisionReason": reason,
        },
        "systemMessage": reason,
    }


def main() -> int:
    payload = json.load(sys.stdin)
    event = payload.get("hook_event_name", "")
    session_id = roadmap_admission.valid_session_id(payload.get("session_id"))
    cwd = str(payload.get("cwd") or "")
    harness = str(payload.get("harness") or "claude_code")

    if event == "SessionStart":
        cache = roadmap_admission.load_cache() or roadmap_admission.repair_cache(cwd)
        ledger = roadmap_admission.ledger_for_path(cache, cwd) if cache else None
        roadmap_admission.log_pending(
            session_id, cwd, ledger, harness=harness,
        )
        context = roadmap_admission.format_session_context(
            cache, session_id=session_id, cwd=cwd,
        )
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            }
        }))
        return 0

    if event == "PreToolUse":
        decision = roadmap_admission.gate(payload)
        if decision:
            print(json.dumps(_block_output(payload, decision)))
        return 0

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        # Admission failures must be visible.  A malformed local cache should
        # not crash the harness, but silently pretending the gate ran would be
        # worse than a warning.
        print(json.dumps({
            "systemMessage": f"Corvus-Mind roadmap gate unavailable: {exc}",
        }))
        raise SystemExit(0)
