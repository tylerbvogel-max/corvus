"""Append-only, provider-neutral model usage ledger for Pallium.

Records only observed provider responses. Dollar values are explicitly split
between list-price equivalents and actual metered cash; subscription calls do
not pretend their equivalent price was charged.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

LEDGER_PATH = Path(os.path.expanduser(
    os.environ.get("CORVUS_MIND_MODEL_LEDGER", "~/.corvus-mind/model-usage.jsonl")
))
EPISODE_DIR = Path(os.path.expanduser(
    os.environ.get("CORVUS_MIND_EPISODE_DIR", "~/.corvus-mind/episodes")
))


def record_model_usage(*, provider: str, model: str, harness: str,
                       workload: str, result: dict) -> None:
    """Append one completed model call. Never disrupt the model response."""
    equivalent = float(result.get("cost_usd") or 0.0)
    if provider == "anthropic":
        billing_basis, actual = "subscription_included", None
    elif equivalent == 0:
        billing_basis, actual = "free_tier", 0.0
    else:
        billing_basis, actual = "api_estimate", None
    row = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "provider": provider,
        "model": result.get("model_version") or model,
        "model_alias": model,
        "harness": harness,
        "workload": workload,
        "input_tokens": int(result.get("input_tokens") or 0),
        "cache_creation_tokens": int(result.get("cache_creation_tokens") or 0),
        "cache_read_tokens": int(result.get("cache_read_tokens") or 0),
        "output_tokens": int(result.get("output_tokens") or 0),
        "equivalent_cost_usd": equivalent,
        "actual_cash_usd": actual,
        "billing_basis": billing_basis,
    }
    try:
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(LEDGER_PATH, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(fd, (json.dumps(row, separators=(",", ":")) + "\n").encode())
        finally:
            os.close(fd)
    except OSError:
        return


def maintenance_cost_breakdown() -> dict:
    """Graph-upkeep LLM cost at API list price, grouped by workload.

    Reads only the ledger file — every row there is a backend job
    (distiller, janitors, lint judges, skill compiler, reconsolidation
    review, action buses via llm_provider's `corvus_internal` default).
    Interactive harness sessions live in EPISODE_DIR and are deliberately
    excluded: they are not part of what keeps the graph runnable. So are
    `eval:*` workloads — benchmark runs are the operator's comparison
    burden, not what an end user pays to run the system. Costs are
    list-price equivalents — the calls run on subscription, so this is
    what the same tokens would bill at API prices, not cash spent.
    """
    buckets: dict[str, dict] = {}
    total = 0.0
    started: str | None = None
    try:
        with LEDGER_PATH.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                w = str(row.get("workload") or "unknown")
                if w.startswith("eval"):
                    continue
                b = buckets.setdefault(w, {
                    "workload": w, "calls": 0, "input_tokens": 0,
                    "output_tokens": 0, "cache_creation_tokens": 0,
                    "cache_read_tokens": 0, "equivalent_cost_usd": 0.0,
                    "models": set(),
                })
                b["calls"] += 1
                for k in ("input_tokens", "output_tokens",
                          "cache_creation_tokens", "cache_read_tokens"):
                    b[k] += int(row.get(k) or 0)
                cost = float(row.get("equivalent_cost_usd") or 0.0)
                b["equivalent_cost_usd"] += cost
                total += cost
                b["models"].add(str(row.get("model_alias") or row.get("model") or "unknown"))
                ts = row.get("ts")
                if ts and (started is None or ts < started):
                    started = ts
    except OSError:
        pass
    by_workload = sorted(
        ({**b, "models": sorted(b["models"]),
          "equivalent_cost_usd": round(b["equivalent_cost_usd"], 4)}
         for b in buckets.values()),
        key=lambda b: -b["equivalent_cost_usd"])
    return {
        "total_equivalent_usd": round(total, 4),
        "by_workload": by_workload,
        "ledger_started_at": started,
    }


def usage_report(historical_distillation_equivalent: float = 0.0) -> dict:
    rows = []
    try:
        with LEDGER_PATH.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except (ValueError, TypeError):
                    continue
    except OSError:
        pass

    # Some harnesses expose final model usage in their Stop payload. Capture
    # those rows without requiring provider SDKs or credentials in the hooks.
    try:
        for path in EPISODE_DIR.glob("*.jsonl"):
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    try:
                        event = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    usage = event.get("usage")
                    if event.get("event") != "Stop" or not isinstance(usage, dict):
                        continue
                    rows.append({
                        "ts": event.get("ts"),
                        "provider": usage.get("provider") or "unknown",
                        "model": event.get("model") or usage.get("model") or "unknown",
                        "harness": event.get("harness") or "unknown",
                        "workload": "interactive_session",
                        "input_tokens": usage.get("input_tokens"),
                        "cache_creation_tokens": usage.get("cache_creation_input_tokens"),
                        "cache_read_tokens": usage.get("cache_read_input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                        "equivalent_cost_usd": usage.get("equivalent_cost_usd") or usage.get("cost_usd"),
                        "actual_cash_usd": usage.get("actual_cash_usd"),
                        "billing_basis": usage.get("billing_basis") or "unknown",
                    })
    except OSError:
        pass

    def grouped(field: str) -> list[dict]:
        buckets: dict[str, dict] = {}
        for row in rows:
            key = str(row.get(field) or "unknown")
            b = buckets.setdefault(key, {"label": key, "calls": 0, "tokens": 0,
                                         "equivalent_cost_usd": 0.0})
            b["calls"] += 1
            b["tokens"] += sum(int(row.get(k) or 0) for k in
                               ("input_tokens", "cache_creation_tokens", "cache_read_tokens", "output_tokens"))
            b["equivalent_cost_usd"] += float(row.get("equivalent_cost_usd") or 0)
        return sorted(({**b, "equivalent_cost_usd": round(b["equivalent_cost_usd"], 4)}
                       for b in buckets.values()), key=lambda b: -b["equivalent_cost_usd"])

    ledger_equivalent = sum(float(r.get("equivalent_cost_usd") or 0) for r in rows)
    # Distillation markers are the complete historical record and continue to
    # include new calls. Avoid double-counting their matching ledger rows.
    nondistill_equivalent = sum(float(r.get("equivalent_cost_usd") or 0) for r in rows
                                if r.get("workload") != "distillation")
    known_actual = [float(r["actual_cash_usd"]) for r in rows if r.get("actual_cash_usd") is not None]
    return {
        "tracked_calls": len(rows),
        "equivalent_cost_usd": round(historical_distillation_equivalent + nondistill_equivalent, 4),
        "historical_distillation_equivalent_usd": round(historical_distillation_equivalent, 4),
        "ledger_equivalent_usd": round(ledger_equivalent, 4),
        "known_actual_cash_usd": round(sum(known_actual), 4),
        "actual_cash_complete": all(r.get("actual_cash_usd") is not None for r in rows) and bool(rows),
        "by_provider": grouped("provider"),
        "by_model": grouped("model"),
        "by_harness": grouped("harness"),
        "by_workload": grouped("workload"),
        "ledger_started_at": min((r.get("ts") for r in rows if r.get("ts")), default=None),
        "coverage_note": "Interactive harness tokens are included only when the harness exposes usage metadata.",
    }
