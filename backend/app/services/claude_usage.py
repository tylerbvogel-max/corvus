"""Claude subscription usage — live gauges for the Pallium dashboard.

Reads the Claude Code OAuth access token from ~/.claude/.credentials.json
(kept fresh by the CLI itself; we never refresh, never persist, never
return it) and asks Anthropic's oauth usage endpoint the same question
the /usage command does. Responses are cached for CACHE_TTL_S so the
frontend can poll freely without hammering the endpoint.
"""

import json
import time
from pathlib import Path

import httpx

CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA = "oauth-2025-04-20"
CACHE_TTL_S = 60

_cache: dict = {"at": 0.0, "report": None}


def _access_token() -> str | None:
    try:
        creds = json.loads(CREDENTIALS_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return (creds.get("claudeAiOauth") or {}).get("accessToken")


def _gauge(limit: dict) -> dict:
    scope = limit.get("scope") or {}
    model = (scope.get("model") or {}).get("display_name")
    return {
        "kind": limit.get("kind"),
        "label": {"session": "5-hour block",
                  "weekly_all": "weekly — all models"}.get(
                      limit.get("kind"),
                      f"weekly — {model}" if model else limit.get("kind")),
        "percent": limit.get("percent"),
        "severity": limit.get("severity", "normal"),
        "resets_at": limit.get("resets_at"),
        "is_active": limit.get("is_active", False),
    }


async def subscription_report() -> dict:
    """Live utilization gauges, cached for CACHE_TTL_S seconds."""
    now = time.time()
    if _cache["report"] is not None and now - _cache["at"] < CACHE_TTL_S:
        return _cache["report"]

    token = _access_token()
    if not token:
        return {"available": False, "reason": "no OAuth token in ~/.claude/.credentials.json"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(USAGE_URL, headers={
                "Authorization": f"Bearer {token}",
                "anthropic-beta": OAUTH_BETA,
            })
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        # Serve the stale gauge rather than a blank panel on a transient error.
        if _cache["report"] is not None:
            return {**_cache["report"], "stale": True}
        return {"available": False, "reason": f"usage endpoint error: {exc}"}

    report = {
        "available": True,
        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "gauges": [_gauge(l) for l in data.get("limits", []) if l.get("percent") is not None],
        "extra_usage_enabled": (data.get("extra_usage") or {}).get("is_enabled", False),
    }
    _cache.update(at=now, report=report)
    return report
