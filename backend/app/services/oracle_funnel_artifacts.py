"""Read-only projection of Oracle Funnel JSONL artifacts for the lab UI."""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from pathlib import Path

STAGES = (
    "ingest", "candidate", "rank", "assembly",
    "synthesis", "judge", "success",
)


def artifact_root() -> Path:
    configured = os.environ.get("CORVUS_EVAL_ARTIFACT_ROOT")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".corvus-mind" / "evals" / "locomo"


def _read_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid funnel artifact {path.name}:{line_number}"
                ) from exc
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _ledger(rows: list[dict]) -> dict:
    eligible = [row for row in rows if row.get("stage") != "adversarial"]
    total = len(eligible)
    counts = Counter(str(row.get("stage")) for row in eligible)
    categories: dict[str, Counter] = defaultdict(Counter)
    for row in eligible:
        categories[str(row.get("category"))][str(row.get("stage"))] += 1
    stages = {
        stage: {
            "count": counts.get(stage, 0),
            "pct": round(100 * counts.get(stage, 0) / max(1, total), 1),
        }
        for stage in STAGES
    }
    losses = [
        f"{stage}: {payload['pct']}pp"
        for stage, payload in stages.items()
        if stage != "success" and payload["count"]
    ]
    return {
        "n_funneled": total,
        "stages": stages,
        "per_category": {
            category: dict(counts)
            for category, counts in sorted(categories.items())
        },
        "headline": (
            f"{stages['success']['pct']}% success; losses — "
            + (", ".join(losses) if losses else "none observed")
        ),
    }


def latest_artifact(root: Path | None = None) -> dict:
    """Return the newest funnel artifact without mutating or relocating it."""
    base = (root or artifact_root()).expanduser()
    if not base.exists():
        return {
            "available": False,
            "artifact_root": str(base),
            "note": "No LoCoMo artifact directory exists yet.",
        }
    candidates = [
        path for path in base.rglob("funnel-*.jsonl")
        if path.is_file()
    ]
    if not candidates:
        return {
            "available": False,
            "artifact_root": str(base),
            "note": (
                "No Oracle Funnel artifact yet. Run the LoCoMo harness with "
                "--oracle-funnel to create one."
            ),
        }
    latest = max(candidates, key=lambda path: path.stat().st_mtime_ns)
    rows = _read_rows(latest)
    return {
        "available": True,
        "artifact_root": str(base),
        "artifact_path": str(latest),
        "run": latest.parent.name,
        "condition": latest.stem.removeprefix("funnel-"),
        "modified_at": latest.stat().st_mtime,
        "ledger": _ledger(rows),
        "sample_rows": rows[:20],
        "row_count": len(rows),
    }
