"""Memory-organ observability: aggregate performance + growth metrics.

Feeds GET /metrics/mind — the tailored Evaluate surface for a memory
tenant. Reads only what the running system already produces: persisted
recall Query rows (model_version 'recall:{source}'), neuron rows,
distiller .distilled markers, the janitor actions log, and the compiled
skills manifest. No LLM, no writes.
"""

import json
import os
from datetime import datetime, timezone

from sqlalchemy import func as sa_func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Neuron, Query, SynapticLearningEvent
from app.services.mind_janitors import ACTIONS_LOG, EPISODE_DIR, LESSON_TYPES
from app.services.skill_compiler import MANIFEST_PATH

RECALL_MARKER = "recall:%"
GROWTH_DAYS = 30
TOP_N = 10


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    if not sorted_values:
        return None
    idx = min(len(sorted_values) - 1, int(round(pct * (len(sorted_values) - 1))))
    return round(sorted_values[idx], 1)


async def recall_metrics(db: AsyncSession) -> dict:
    """Latency percentiles, per-source counts, per-stage mean timings."""
    rows = (await db.execute(
        select(Query.model_version, Query.results_json, Query.stage_telemetry_json,
               Query.created_at)
        .where(Query.model_version.like(RECALL_MARKER))
        .order_by(Query.id.desc()).limit(2000)
    )).all()
    latencies: list[float] = []
    by_source: dict[str, int] = {}
    stage_sums: dict[str, list[float]] = {}
    for model_version, results_json, telemetry, _created in rows:
        by_source[model_version.removeprefix("recall:")] = \
            by_source.get(model_version.removeprefix("recall:"), 0) + 1
        try:
            latencies.append(float(json.loads(results_json or "[]")[0]["latency_ms"]))
        except (ValueError, KeyError, IndexError, TypeError):
            pass
        for stage in telemetry or []:
            name = stage.get("stage")
            dur = stage.get("duration_ms")
            if name and isinstance(dur, (int, float)):
                stage_sums.setdefault(name, []).append(float(dur))
    latencies.sort()
    return {
        "total": len(rows),
        "by_source": by_source,
        "latency_ms": {"p50": _percentile(latencies, 0.5),
                       "p95": _percentile(latencies, 0.95),
                       "max": latencies[-1] if latencies else None},
        "stage_mean_ms": {name: round(sum(vals) / len(vals), 1)
                          for name, vals in sorted(stage_sums.items())},
    }


async def lesson_metrics(db: AsyncSession) -> dict:
    """Corpus shape: counts, scopes, top-recalled, zombies, utility spread."""
    lessons = (await db.execute(
        select(Neuron).where(Neuron.node_type.in_(LESSON_TYPES))
    )).scalars().all()
    reinforced_ids = set((await db.execute(
        select(SynapticLearningEvent.neuron_id).distinct()
    )).scalars().all())
    active = [n for n in lessons if n.is_active]
    by_scope: dict[str, int] = {}
    for n in active:
        by_scope[n.department or "unscoped"] = by_scope.get(n.department or "unscoped", 0) + 1
    top = sorted(active, key=lambda n: -(n.invocations or 0))[:TOP_N]
    zombies = [n for n in active
               if (n.invocations or 0) >= 5 and n.id not in reinforced_ids]
    return {
        "active": len(active),
        "absorbed_or_inactive": len(lessons) - len(active),
        "superseded": sum(1 for n in lessons if n.superseded_by is not None),
        "by_scope": by_scope,
        "avg_utility": round(sum((n.avg_utility or 0.5) for n in active)
                             / len(active), 3) if active else None,
        "reinforced": sum(1 for n in active if n.id in reinforced_ids),
        "top_recalled": [{"id": n.id, "label": n.label,
                          "invocations": n.invocations or 0,
                          "utility": round(n.avg_utility or 0.5, 3)} for n in top],
        "zombie_candidates": [{"id": n.id, "label": n.label,
                               "invocations": n.invocations or 0} for n in zombies[:TOP_N]],
    }


async def growth_metrics(db: AsyncSession) -> dict:
    """Daily timeseries: lessons created and recalls served."""
    lessons_daily = (await db.execute(text(
        "SELECT date(created_at) AS d, count(*) FROM neurons "
        "WHERE node_type = ANY(:types) "
        "GROUP BY d ORDER BY d DESC LIMIT :days"),
        {"types": list(LESSON_TYPES), "days": GROWTH_DAYS},
    )).all()
    recalls_daily = (await db.execute(text(
        "SELECT date(created_at) AS d, count(*) FROM queries "
        "WHERE model_version LIKE 'recall:%' "
        "GROUP BY d ORDER BY d DESC LIMIT :days"), {"days": GROWTH_DAYS},
    )).all()
    return {
        "lessons_created_daily": [{"date": str(d), "count": c} for d, c in lessons_daily],
        "recalls_daily": [{"date": str(d), "count": c} for d, c in recalls_daily],
    }


def distiller_metrics() -> dict:
    """Yield funnel + cost ledger from the .distilled marker files."""
    funnel = {"sessions_distilled": 0, "events": 0, "candidates": 0, "saved": 0,
              "usage_skipped": 0, "duplicate": 0, "flagged": 0, "invalid": 0}
    cost = 0.0
    if os.path.isdir(EPISODE_DIR):
        for name in os.listdir(EPISODE_DIR):
            if not name.endswith(".distilled"):
                continue
            try:
                with open(os.path.join(EPISODE_DIR, name), encoding="utf-8") as fh:
                    marker = json.load(fh)
            except (OSError, ValueError):
                continue
            funnel["sessions_distilled"] += 1
            for key in ("events", "candidates", "saved", "usage_skipped",
                        "duplicate", "flagged", "invalid"):
                funnel[key] += int(marker.get(key, 0) or 0)
            cost += float(marker.get("cost_usd") or 0.0)
    pending = 0
    if os.path.isdir(EPISODE_DIR):
        pending = sum(1 for n in os.listdir(EPISODE_DIR)
                      if n.endswith(".jsonl")
                      and not os.path.exists(os.path.join(EPISODE_DIR, n) + ".distilled"))
    return {**funnel, "logs_awaiting": pending, "cost_usd": round(cost, 4)}


def janitor_metrics() -> dict:
    """Action counts by type from the janitor actions episode log."""
    counts: dict[str, int] = {}
    try:
        with open(ACTIONS_LOG, encoding="utf-8") as fh:
            for line in fh:
                try:
                    action = json.loads(line).get("action", "unknown")
                except ValueError:
                    continue
                counts[action] = counts.get(action, 0) + 1
    except OSError:
        pass
    return counts


def compiler_metrics() -> dict:
    """Compiled-skill inventory from the manifest."""
    try:
        with open(MANIFEST_PATH, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError):
        manifest = []
    return {
        "skills": [{"name": m.get("name"), "scope": m.get("scope"),
                    "sources": len(m.get("sources", [])),
                    "compiled_at": m.get("compiled_at")} for m in manifest],
    }


async def injection_metrics() -> dict:
    """Injection volume + coverage from Injection events in episode logs."""
    total = 0
    per_lesson: dict[str, int] = {}
    sessions_with = set()
    if os.path.isdir(EPISODE_DIR):
        for name in os.listdir(EPISODE_DIR):
            if not name.endswith(".jsonl"):
                continue
            try:
                with open(os.path.join(EPISODE_DIR, name), encoding="utf-8") as fh:
                    for line in fh:
                        if '"Injection"' not in line:
                            continue
                        try:
                            rec = json.loads(line)
                        except ValueError:
                            continue
                        total += 1
                        sessions_with.add(name)
                        for label in rec.get("labels", []):
                            per_lesson[label] = per_lesson.get(label, 0) + 1
            except OSError:
                continue
    top = sorted(per_lesson.items(), key=lambda kv: -kv[1])[:TOP_N]
    return {"events": total, "sessions_with_injections": len(sessions_with),
            "distinct_lessons_injected": len(per_lesson),
            "top_injected": [{"label": k, "count": v} for k, v in top]}


def sessions_report() -> list[dict]:
    """Episode-log browser data: one row per captured/backfilled session."""
    rows: list[dict] = []
    if not os.path.isdir(EPISODE_DIR):
        return rows
    for name in sorted(os.listdir(EPISODE_DIR)):
        if not name.endswith(".jsonl") or name == "janitor-actions.jsonl":
            continue
        path = os.path.join(EPISODE_DIR, name)
        stats = {"events": 0, "errors": 0, "injections": 0}
        project = None
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    stats["events"] += 1
                    if '"Injection"' in line:
                        stats["injections"] += 1
                    elif '"ok": false' in line:
                        stats["errors"] += 1
                    if project is None and '"project"' in line:
                        try:
                            project = json.loads(line).get("project")
                        except ValueError:
                            pass
        except OSError:
            continue
        marker = None
        try:
            with open(path + ".distilled", encoding="utf-8") as fh:
                m = json.load(fh)
            marker = {"saved": m.get("saved"), "candidates": m.get("candidates"),
                      "cost_usd": m.get("cost_usd"),
                      "neuron_ids": m.get("neuron_ids", []),
                      "attribution": m.get("attribution")}
        except (OSError, ValueError):
            pass
        rows.append({"session": name.removesuffix(".jsonl"), "project": project,
                     **stats, "distilled": marker,
                     "mtime": datetime.fromtimestamp(
                         os.path.getmtime(path), tz=timezone.utc).isoformat(timespec="seconds")})
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    return rows[:150]


def _janitor_trust_events() -> dict[int, list[dict]]:
    """Per-neuron utility-changing janitor/attribution events from the log."""
    out: dict[int, list[dict]] = {}
    try:
        with open(ACTIONS_LOG, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                nid = rec.get("neuron_id") or rec.get("canonical_id")
                new_u = rec.get("new_utility")
                if nid is None or new_u is None:
                    continue
                out.setdefault(int(nid), []).append({
                    "ts": rec.get("ts"), "u": float(new_u),
                    "kind": rec.get("action", "janitor")})
    except OSError:
        pass
    return out


async def trust_report(db: AsyncSession) -> list[dict]:
    """Per-lesson trust trajectory: creation baseline + every utility move."""
    lessons = (await db.execute(
        select(Neuron).where(Neuron.node_type.in_(LESSON_TYPES),
                             Neuron.is_active.is_(True))
    )).scalars().all()
    events = (await db.execute(
        select(SynapticLearningEvent)
        .where(SynapticLearningEvent.neuron_id.in_([n.id for n in lessons] or [0]))
    )).scalars().all()
    by_neuron: dict[int, list[dict]] = {}
    for e in events:
        by_neuron.setdefault(e.neuron_id, []).append({
            "ts": e.created_at.isoformat() if e.created_at else None,
            "u": round(e.new_avg_utility, 3), "kind": f"synaptic.{e.event_type}"})
    janitor_events = _janitor_trust_events()
    out = []
    for n in lessons:
        points = [{"ts": n.created_at.isoformat() if n.created_at else None,
                   "u": 0.5, "kind": "created"}]
        points += by_neuron.get(n.id, []) + janitor_events.get(n.id, [])
        points.sort(key=lambda p: p["ts"] or "")
        points.append({"ts": None, "u": round(n.avg_utility or 0.5, 3), "kind": "now"})
        out.append({"id": n.id, "label": n.label, "scope": n.department,
                    "invocations": n.invocations or 0,
                    "utility": round(n.avg_utility or 0.5, 3), "points": points})
    out.sort(key=lambda r: -r["utility"])
    return out


async def inbox_report(db: AsyncSession) -> dict:
    """Everything awaiting human judgment, in one place."""
    from app.models import AutopilotProposal, IntegrityFinding
    findings = (await db.execute(
        select(IntegrityFinding).where(IntegrityFinding.status == "open")
        .order_by(IntegrityFinding.id.desc()).limit(50)
    )).scalars().all()
    proposals = (await db.execute(
        select(AutopilotProposal).where(AutopilotProposal.state == "proposed")
        .order_by(AutopilotProposal.id.desc()).limit(50)
    )).scalars().all()
    borderline = []
    try:
        with open(os.path.join(EPISODE_DIR, "janitor-report.json"), encoding="utf-8") as fh:
            borderline = (json.load(fh).get("consolidation") or {}).get("borderline", [])
    except (OSError, ValueError):
        pass
    return {
        "findings": [{"id": f.id, "type": f.finding_type,
                      "description": (f.description or "")[:300],
                      "neuron_ids": json.loads(f.neuron_ids_json or "[]")}
                     for f in findings],
        "proposals": [{"id": p.id, "source": p.gap_source,
                       "description": (p.gap_description or "")[:200]}
                      for p in proposals],
        "borderline_pairs": borderline,
    }


async def skills_report(db: AsyncSession) -> list[dict]:
    """Compiled-skill inventory with source health and rendered body."""
    try:
        with open(MANIFEST_PATH, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError):
        manifest = []
    out = []
    for entry in manifest:
        sources = []
        for nid in entry.get("sources", []):
            n = await db.get(Neuron, nid)
            sources.append({"id": nid,
                            "label": n.label if n else "(missing)",
                            "healthy": bool(n and n.is_active and n.superseded_by is None)})
        body = None
        try:
            with open(entry.get("path", ""), encoding="utf-8") as fh:
                body = fh.read()[:8000]
        except OSError:
            pass
        out.append({**entry, "source_health": sources, "body": body,
                    "stale": any(not s["healthy"] for s in sources)})
    return out


async def collect_all(db: AsyncSession) -> dict:
    """The full /metrics/mind payload."""
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "recall": await recall_metrics(db),
        "lessons": await lesson_metrics(db),
        "growth": await growth_metrics(db),
        "injections": await injection_metrics(),
        "distiller": distiller_metrics(),
        "janitors": janitor_metrics(),
        "compiler": compiler_metrics(),
    }
    report["cost_ledger_usd"] = {
        "distiller": report["distiller"]["cost_usd"],
        "recall_and_injection": 0.0,
    }
    assert "recall" in report and "growth" in report, "metrics payload incomplete"
    return report
