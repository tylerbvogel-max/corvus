#!/usr/bin/env python3
"""Corvus Mind episode-capture hook for Claude Code.

Wired into ~/.claude/settings.json as a PostToolUse + Stop hook. Reads the
hook payload from stdin and appends one JSON line per event to
~/.corvus-mind/episodes/{session_id}.jsonl. Deterministic — no LLM, no
network, stdlib only (this machine has no jq).

Safety requirements (CORVUS-MIND-DESIGN.md §8.2 Amendment D) are enforced
here, at the capture layer, where the data is born:
  - secret redaction before anything touches disk
  - excluded cwd prefixes (the work/personal wall) are never logged
  - the hook must never break a session: any failure exits 0 silently
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

EPISODE_DIR = os.path.expanduser("~/.corvus-mind/episodes")
CONFIG_PATH = os.path.expanduser("~/.corvus-mind/config.json")
MAX_FIELD_CHARS = 400
MAX_ERROR_CHARS = 600

# Credential-shaped literals and assignments. Applied to every captured
# string; a memory system that regurgitates a secret is a persistent leak.
SECRET_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key|apikey|auth)\s*[=:]\s*\S+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_.~+/-]{16,}"),
    re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^:\s]+:[^@\s]+@"),
)

# Per-tool argument keys worth keeping. Everything else (file bodies,
# old_string/new_string diffs, huge prompts) is noise at episode granularity.
INPUT_KEY_ALLOWLIST = (
    "command", "description", "file_path", "notebook_path", "pattern",
    "query", "url", "skill", "args", "prompt", "subagent_type", "path",
    "glob", "offset", "limit",
)


def _redact(text: str) -> str:
    for pat in SECRET_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


def _clip(text: str, limit: int) -> str:
    text = _redact(str(text))
    if len(text) > limit:
        return text[:limit] + f"…[+{len(text) - limit} chars]"
    return text


def _load_excludes() -> list:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            cfg = json.load(fh)
        raw = cfg.get("excluded_cwd_prefixes", [])
        return [os.path.expanduser(p) for p in raw if isinstance(p, str)]
    except (OSError, ValueError):
        return []


def _summarize_input(tool_input) -> dict:
    if not isinstance(tool_input, dict):
        return {"_raw": _clip(tool_input, MAX_FIELD_CHARS)}
    summary = {}
    for key in INPUT_KEY_ALLOWLIST:
        if key in tool_input:
            summary[key] = _clip(tool_input[key], MAX_FIELD_CHARS)
    dropped = sorted(set(tool_input) - set(summary))
    if dropped:
        summary["_dropped_keys"] = dropped
    return summary


def _outcome(tool_response) -> tuple:
    """Best-effort (ok, error_text) from a tool_response of unknown shape."""
    if isinstance(tool_response, dict):
        if tool_response.get("is_error") or tool_response.get("interrupted"):
            err = tool_response.get("error") or tool_response.get("stderr") or "interrupted"
            return False, _clip(err, MAX_ERROR_CHARS)
        err = tool_response.get("error")
        if err:
            return False, _clip(err, MAX_ERROR_CHARS)
        return True, None
    if isinstance(tool_response, str) and tool_response.startswith("Error"):
        return False, _clip(tool_response, MAX_ERROR_CHARS)
    return True, None


def _project_from_cwd(cwd: str) -> str:
    home = os.path.expanduser("~")
    projects_root = os.path.join(home, "Projects") + os.sep
    if cwd.startswith(projects_root):
        return cwd[len(projects_root):].split(os.sep, 1)[0]
    if cwd.rstrip(os.sep) == home:
        return "home"
    return "other"


def build_record(payload: dict) -> dict:
    event = payload.get("hook_event_name", "unknown")
    cwd = payload.get("cwd") or ""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": event,
        "session_id": payload.get("session_id", "unknown"),
        "cwd": _redact(cwd),
        "project": _project_from_cwd(cwd),
        "harness": payload.get("harness") or "claude_code",
    }
    if event == "PostToolUse":
        record["tool"] = payload.get("tool_name", "unknown")
        record["input"] = _summarize_input(payload.get("tool_input"))
        ok, error = _outcome(payload.get("tool_response"))
        record["ok"] = ok
        if error:
            record["error"] = error
    elif event == "Stop":
        record["transcript_path"] = payload.get("transcript_path")
        record["distill_ready"] = True
        # Harnesses may expose these fields now or later. Preserve them when
        # present; absence means unknown, never zero.
        if payload.get("model"):
            record["model"] = payload["model"]
        if isinstance(payload.get("usage"), dict):
            record["usage"] = payload["usage"]
    return record


def main() -> int:
    payload = json.load(sys.stdin)
    cwd = payload.get("cwd") or ""
    for prefix in _load_excludes():
        if cwd.startswith(prefix):
            return 0  # excluded scope: never logged, by design
    session_id = payload.get("session_id") or "unknown"
    if not re.fullmatch(r"[A-Za-z0-9_-]+", session_id):
        session_id = "unknown"
    os.makedirs(EPISODE_DIR, exist_ok=True)
    record = build_record(payload)
    path = os.path.join(EPISODE_DIR, f"{session_id}.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 — capture must never break a session
        sys.exit(0)
