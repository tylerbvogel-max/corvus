"""Codex subscription usage via the supported local app-server protocol."""

import asyncio
import json
import os
import time
from datetime import datetime, timezone

CODEX_PATH = os.path.expanduser(os.environ.get("CODEX_PATH", "~/.local/bin/codex"))
CACHE_TTL_S = 60
_cache: dict = {"at": 0.0, "report": None}


def _severity(percent: int) -> str:
    return "serious" if percent >= 90 else "warning" if percent >= 70 else "normal"


def _window_label(minutes: int | None) -> str:
    if not minutes:
        return "usage window"
    if minutes % 10080 == 0:
        return f"{minutes // 10080}-week window"
    if minutes % 1440 == 0:
        return f"{minutes // 1440}-day window"
    if minutes % 60 == 0:
        return f"{minutes // 60}-hour window"
    return f"{minutes}-minute window"


def _format_report(rate_result: dict, usage_result: dict, now: float) -> dict:
    snapshots = rate_result.get("rateLimitsByLimitId") or {}
    if not snapshots and rate_result.get("rateLimits"):
        snapshots = {"codex": rate_result["rateLimits"]}
    gauges = []
    for limit_id, snapshot in snapshots.items():
        for slot, window in (("primary", snapshot.get("primary")),
                             ("secondary", snapshot.get("secondary"))):
            if not window:
                continue
            percent = int(window.get("usedPercent") or 0)
            name = snapshot.get("limitName") or ("Codex" if limit_id == "codex" else limit_id)
            duration = _window_label(window.get("windowDurationMins"))
            gauges.append({
                "kind": f"{limit_id}:{slot}", "label": f"{name} · {duration}",
                "percent": percent, "severity": _severity(percent),
                "resets_at": datetime.fromtimestamp(window["resetsAt"], tz=timezone.utc).isoformat()
                if window.get("resetsAt") else None,
            })
    return {
        "available": True,
        "fetched_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(timespec="seconds"),
        "gauges": gauges,
        "summary": usage_result.get("summary") or {},
        "daily_usage": usage_result.get("dailyUsageBuckets") or [],
        "plan_type": (rate_result.get("rateLimits") or {}).get("planType"),
        "reset_credits": (rate_result.get("rateLimitResetCredits") or {}).get("availableCount", 0),
        "source": "codex app-server",
    }


async def _app_server_read() -> tuple[dict, dict]:
    proc = await asyncio.create_subprocess_exec(
        CODEX_PATH, "app-server", "--listen", "stdio://",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    messages = [
        {"method": "initialize", "id": 0, "params": {"clientInfo": {
            "name": "corvus_mind", "title": "Corvus Mind", "version": "0.1.0"}}},
        {"method": "initialized", "params": {}},
        {"method": "account/rateLimits/read", "id": 1},
        {"method": "account/usage/read", "id": 2},
    ]
    assert proc.stdin and proc.stdout
    for message in messages:
        proc.stdin.write((json.dumps(message) + "\n").encode())
    await proc.stdin.drain()
    found: dict[int, dict] = {}
    try:
        while 1 not in found or 2 not in found:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=15)
            if not line:
                break
            message = json.loads(line)
            if message.get("id") in (1, 2):
                if message.get("error"):
                    raise RuntimeError(str(message["error"]))
                found[message["id"]] = message.get("result") or {}
    finally:
        if proc.returncode is None:
            proc.terminate()
        await proc.wait()
    if 1 not in found or 2 not in found:
        raise RuntimeError("Codex app-server returned an incomplete usage response")
    return found[1], found[2]


async def subscription_report() -> dict:
    now = time.time()
    if _cache["report"] is not None and now - _cache["at"] < CACHE_TTL_S:
        return _cache["report"]
    try:
        rate_limits, token_usage = await _app_server_read()
        report = _format_report(rate_limits, token_usage, now)
    except (OSError, RuntimeError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
        if _cache["report"] is not None:
            return {**_cache["report"], "stale": True}
        return {"available": False, "reason": f"Codex usage unavailable: {exc}"}
    _cache.update(at=now, report=report)
    return report
