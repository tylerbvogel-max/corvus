#!/usr/bin/env python3
"""One-time bootstrap: convert historical Claude Code transcripts into
synthetic episode logs the EXISTING distiller pipeline can drain.

Design (CORVUS-MIND-DESIGN.md §5 + phase-6 discussion 2026-07-10):
- Stage 1 (deterministic): replay each transcript's tool_use/tool_result
  pairs into the same episode-log schema the live PostToolUse hook
  writes — same redaction, same exclusion, same field allowlist (reused
  by import). Failures come from structural signals (tool_result
  is_error), never text matching. Long sessions split into parts so the
  distiller's prompt cap doesn't starve them.
- Stage 2 (deterministic): rank output logs by signal density
  (errors + user corrections) with a recency bonus, and name them
  bf-{rank}-... so find_ready_logs' sorted order drains highest-signal,
  newest-first. Cross-session recurrence is NOT computed here — the
  consolidation janitor turns per-session near-dups into weight.
- A tool-usage distribution report lands in ~/.corvus-mind/backfill-report.json.

Idempotent: sessions with an existing live episode log or an existing
bf- log are skipped. Output mtimes inherit the transcript's, so the
distiller's quiescence guard passes immediately.
"""

import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import episode_hook  # reuse redaction/allowlist/exclusion — one source of truth

PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
EPISODE_DIR = episode_hook.EPISODE_DIR
REPORT_PATH = os.path.expanduser("~/.corvus-mind/backfill-report.json")
MIN_TOOL_EVENTS = 5
MAX_EVENTS_PER_LOG = 150
MAX_TRANSCRIPT_LINE = 2_000_000

_CORRECTION = re.compile(
    r"(?i)\b(no[,.]|don'?t|do not|never|stop|wrong|actually|instead|"
    r"not what I|that'?s not|revert|undo)\b"
)


def _iter_transcripts():
    for proj in sorted(os.listdir(PROJECTS_DIR)):
        pdir = os.path.join(PROJECTS_DIR, proj)
        if not os.path.isdir(pdir):
            continue
        for name in sorted(os.listdir(pdir)):
            if name.endswith(".jsonl"):
                yield os.path.join(pdir, name)


def _already_done(session_id: str) -> bool:
    if os.path.exists(os.path.join(EPISODE_DIR, f"{session_id}.jsonl")):
        return True  # live-captured session: the real hook owns it
    for name in os.listdir(EPISODE_DIR):
        if name.startswith("bf-") and f"-{session_id}" in name:
            return True
    return False


def _tool_error_text(content) -> str:
    if isinstance(content, str):
        return content[:300]
    if isinstance(content, list):
        parts = [c.get("text", "") for c in content if isinstance(c, dict)]
        return " ".join(parts)[:300]
    return ""


def _parse_transcript(path: str, excludes: list) -> tuple[list, dict]:
    """Transcript → (episode events, signal stats). Structural signals only."""
    pending: dict[str, dict] = {}
    events: list[dict] = []
    stats = {"tool_calls": 0, "errors": 0, "corrections": 0, "user_msgs": 0,
             "tools": {}, "excluded_events": 0}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if len(line) > MAX_TRANSCRIPT_LINE:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            rtype = rec.get("type")
            cwd = rec.get("cwd") or ""
            content = (rec.get("message") or {}).get("content")
            if rtype == "assistant" and isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        pending[block.get("id", "")] = {
                            "ts": rec.get("timestamp", ""), "cwd": cwd,
                            "tool": block.get("name", "unknown"),
                            "input": block.get("input") or {},
                        }
            elif rtype == "user":
                if isinstance(content, str):
                    stats["user_msgs"] += 1
                    if _CORRECTION.search(content):
                        stats["corrections"] += 1
                    continue
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not (isinstance(block, dict) and block.get("type") == "tool_result"):
                        continue
                    call = pending.pop(block.get("tool_use_id", ""), None)
                    if call is None:
                        continue
                    if any(call["cwd"].startswith(p) for p in excludes):
                        stats["excluded_events"] += 1
                        continue
                    is_err = bool(block.get("is_error"))
                    ev = {
                        "ts": call["ts"], "event": "PostToolUse",
                        "cwd": episode_hook._redact(call["cwd"]),
                        "project": episode_hook._project_from_cwd(call["cwd"]),
                        "tool": call["tool"],
                        "input": episode_hook._summarize_input(call["input"]),
                        "ok": not is_err,
                    }
                    if is_err:
                        ev["error"] = episode_hook._redact(
                            _tool_error_text(block.get("content")))
                        stats["errors"] += 1
                    events.append(ev)
                    stats["tool_calls"] += 1
                    tool_stats = stats["tools"].setdefault(
                        call["tool"], {"calls": 0, "errors": 0})
                    tool_stats["calls"] += 1
                    tool_stats["errors"] += int(is_err)
    return events, stats


def _write_parts(session_id: str, transcript: str, events: list) -> list:
    """Write events as one or more part logs, each with a Stop marker."""
    paths = []
    chunks = [events[i:i + MAX_EVENTS_PER_LOG]
              for i in range(0, len(events), MAX_EVENTS_PER_LOG)]
    mtime = os.path.getmtime(transcript)
    for idx, chunk in enumerate(chunks, start=1):
        stem = f"{session_id}-p{idx:02d}" if len(chunks) > 1 else session_id
        path = os.path.join(EPISODE_DIR, f"tmp-{stem}.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for ev in chunk:
                fh.write(json.dumps({**ev, "session_id": stem}, ensure_ascii=False) + "\n")
            fh.write(json.dumps({
                "ts": chunk[-1]["ts"], "event": "Stop", "session_id": stem,
                "cwd": chunk[-1]["cwd"], "transcript_path": transcript,
                "distill_ready": True, "backfill": True,
            }, ensure_ascii=False) + "\n")
        os.utime(path, (mtime, mtime))
        paths.append(path)
    return paths


def main() -> int:
    excludes = episode_hook._load_excludes()
    os.makedirs(EPISODE_DIR, exist_ok=True)
    staged = []          # (score, tmp_path, session_id, stats)
    tool_totals: dict = {}
    skipped = {"done": 0, "thin": 0}
    now = datetime.now(timezone.utc).timestamp()

    for transcript in _iter_transcripts():
        session_id = os.path.basename(transcript).removesuffix(".jsonl")
        if _already_done(session_id):
            skipped["done"] += 1
            continue
        events, stats = _parse_transcript(transcript, excludes)
        if stats["tool_calls"] < MIN_TOOL_EVENTS:
            skipped["thin"] += 1
            continue
        for tool, ts in stats["tools"].items():
            agg = tool_totals.setdefault(tool, {"calls": 0, "errors": 0})
            agg["calls"] += ts["calls"]
            agg["errors"] += ts["errors"]
        age_days = max(0.0, (now - os.path.getmtime(transcript)) / 86400)
        score = (2 * stats["errors"] + 3 * stats["corrections"]
                 + max(0.0, 30 - age_days))
        for path in _write_parts(session_id, transcript, events):
            staged.append((score, path, session_id, stats))

    staged.sort(key=lambda item: -item[0])
    ranking = []
    for rank, (score, tmp_path, session_id, stats) in enumerate(staged, start=1):
        final = tmp_path.replace("tmp-", f"bf-{rank:03d}-", 1)
        os.rename(tmp_path, final)
        ranking.append({
            "rank": rank, "log": os.path.basename(final), "score": round(score, 1),
            "errors": stats["errors"], "corrections": stats["corrections"],
            "tool_calls": stats["tool_calls"],
        })

    report = {
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "staged_logs": len(staged), "skipped": skipped,
        "tool_distribution": dict(sorted(
            tool_totals.items(), key=lambda kv: -kv[1]["calls"])),
        "ranking": ranking,
    }
    with open(REPORT_PATH, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    print(json.dumps({k: report[k] for k in ("staged_logs", "skipped")}, indent=2))
    print(f"report: {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
