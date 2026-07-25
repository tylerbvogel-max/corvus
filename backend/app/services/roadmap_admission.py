"""Fast local projection and episode receipts for roadmap admission.

The roadmap database remains canonical.  This module writes a small,
atomically-replaced projection that coding-agent startup hooks can read without
waiting for PostgreSQL or the HTTP service.  Admission receipts live beside the
existing Corvus-Mind episode stream so one session has one auditable timeline.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RoadmapLedger
from app.services.roadmap_ledger import (
    OUT_OF_SCOPE_STATUSES,
    node_is_ready,
    review_status,
    state_summary,
)


PLANNING_DIR = Path(
    os.environ.get(
        "CORVUS_MIND_PLANNING_DIR",
        os.path.expanduser("~/.corvus-mind/planning"),
    )
)
CACHE_PATH = PLANNING_DIR / "ledgers.json"
EPISODE_DIR = Path(
    os.environ.get(
        "CORVUS_MIND_EPISODE_DIR",
        os.path.expanduser("~/.corvus-mind/episodes"),
    )
)
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
CACHE_SCHEMA_VERSION = 1
MAX_CANDIDATES = 8


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _candidate_priority(state: dict[str, Any], node: dict[str, Any]) -> tuple:
    status = node.get("status")
    review = review_status(node)
    horizon = node.get("horizon")
    if status == "active":
        lane = 0
    elif status == "in-progress":
        lane = 1
    elif review == "due":
        lane = 2
    elif node_is_ready(state, node) and horizon in {"active", "horizon-1"}:
        lane = 3
    elif node_is_ready(state, node):
        lane = 4
    else:
        lane = 5
    return (lane, str(node.get("label", "")).lower(), str(node.get("id", "")))


def project_ledger(row: RoadmapLedger) -> dict[str, Any]:
    """Render one ledger into the compact hook-facing cache contract."""
    state = row.state
    in_scope = [
        node for node in state.get("nodes", [])
        if node.get("status") not in {"done", *OUT_OF_SCOPE_STATUSES}
    ]
    candidates = sorted(
        in_scope,
        key=lambda node: _candidate_priority(state, node),
    )[:MAX_CANDIDATES]
    return {
        "slug": row.slug,
        "name": row.name,
        "description": row.description,
        "project_path": (
            os.path.realpath(os.path.expanduser(row.project_path))
            if row.project_path else None
        ),
        "revision": row.revision,
        "state_version": state.get("version"),
        "summary": state_summary(state),
        "candidates": [
            {
                "id": node["id"],
                "label": node["label"],
                "status": node.get("status"),
                "horizon": node.get("horizon"),
                "review_status": review_status(node),
                "ready": node_is_ready(state, node),
                "summary": node.get("summary"),
            }
            for node in candidates
        ],
        # The index lets the gate render an admitted record even when it is not
        # in the current shortlist.  At ~100 bytes/record it remains tiny.
        "record_index": {
            node["id"]: {
                "label": node["label"],
                "status": node.get("status"),
                "horizon": node.get("horizon"),
            }
            for node in state.get("nodes", [])
        },
    }


def cache_document(rows: Iterable[RoadmapLedger]) -> dict[str, Any]:
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "generated_at": _now(),
        "ledgers": [project_ledger(row) for row in rows],
    }


def write_cache(document: dict[str, Any]) -> Path:
    """Atomically replace the local projection with owner-only permissions."""
    PLANNING_DIR.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=".ledgers-", suffix=".json", dir=PLANNING_DIR,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, CACHE_PATH)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return CACHE_PATH


async def refresh_cache(db: AsyncSession) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(RoadmapLedger).order_by(
                RoadmapLedger.updated_at.desc(), RoadmapLedger.id,
            )
        )
    ).scalars().all()
    document = cache_document(rows)
    write_cache(document)
    return document


def append_episode_event(session_id: str, event: dict[str, Any]) -> dict[str, Any]:
    if not SESSION_ID_RE.fullmatch(session_id):
        raise ValueError("session_id must contain only letters, numbers, underscores, or hyphens")
    EPISODE_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": _now(),
        "session_id": session_id,
        **event,
    }
    path = EPISODE_DIR / f"{session_id}.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def admit_session(
    *,
    ledger: RoadmapLedger,
    session_id: str,
    mode: str,
    record_id: str | None,
    reason: str | None,
    cwd: str | None,
    harness: str | None,
) -> dict[str, Any]:
    if mode not in {"bound", "off-ledger"}:
        raise ValueError("mode must be bound or off-ledger")
    node = None
    if mode == "bound":
        if not record_id:
            raise ValueError("record_id is required for bound admission")
        node = next(
            (item for item in ledger.state.get("nodes", []) if item.get("id") == record_id),
            None,
        )
        if node is None:
            raise ValueError("roadmap record not found")
        if node.get("status") in {"done", *OUT_OF_SCOPE_STATUSES}:
            raise ValueError("admission requires an in-scope, unfinished roadmap record")
    elif not reason or not reason.strip():
        raise ValueError("off-ledger admission requires a reason")

    return append_episode_event(
        session_id,
        {
            "event": "PlanningAdmission",
            "ledger_slug": ledger.slug,
            "ledger_name": ledger.name,
            "ledger_revision": ledger.revision,
            "ledger_state_version": ledger.state.get("version"),
            "record_id": record_id if mode == "bound" else None,
            "record_label": node.get("label") if node else None,
            "mode": mode,
            "reason": reason.strip() if reason else None,
            "cwd": cwd,
            "harness": harness,
        },
    )


def recent_admissions(slug: str, *, limit: int = 50) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if not EPISODE_DIR.exists():
        return events
    for path in EPISODE_DIR.glob("*.jsonl"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if (
                event.get("event") in {"PlanningAdmission", "PlanningReturn"}
                and event.get("ledger_slug") == slug
            ):
                events.append(event)
    events.sort(key=lambda item: str(item.get("ts", "")), reverse=True)
    return events[:limit]
