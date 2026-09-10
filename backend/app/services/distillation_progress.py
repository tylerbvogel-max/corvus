"""Database-authoritative distillation progress, with recoverable projections.

Transaction-scoped PostgreSQL advisory locks cover cooperating callers using
the same database and canonical source path. No clock-based lease, process
registry, or lock held on a connection returned to the pool is involved.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, text

from app.models import DistillationCheckpoint
from app.services.distillation_inputs import capture, materialize, source_id
from app.services.lesson_store import stage_lesson_enrichment


async def _locked_checkpoint(db, identity):
    key = int(identity[:16], 16) & ((1 << 63) - 1)
    if not await db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}):
        raise RuntimeError("distillation-source-busy")
    return await db.get(DistillationCheckpoint, identity, populate_existing=True)


def _write_marker(path, state):
    """Atomic replace; failure does not undo or invalidate the database receipt."""
    target = Path(path + ".distilled")
    fd, temporary = tempfile.mkstemp(prefix=".distilled-", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({**state["receipt"], "checkpoint_version": 1,
                       "episode": state["episode"]}, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _deliver_action(identity, boundary, index, action):
    """Idempotent local JSONL projection, fsynced before acknowledging delivery.

    A dedicated persistent lock coordinates checkpoint writers on this host.
    The unrelated legacy action writers do not take this lock; their delivery
    semantics are unchanged. This is not a distributed filesystem protocol.
    """
    from app.services.mind_corpus import ACTIONS_LOG

    event_id = hashlib.sha256(f"{identity}:{boundary}:{index}".encode()).hexdigest()
    path = Path(ACTIONS_LOG)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + ".distillation.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with open(path, "a+", encoding="utf-8") as stream:
            stream.seek(0)
            for line in stream:
                try:
                    existing = json.loads(line)
                except ValueError:
                    continue
                if isinstance(existing, dict) and existing.get("distillation_event_id") == event_id:
                    # A prior cancelled process may have written but not fsynced.
                    stream.flush()
                    os.fsync(stream.fileno())
                    return
            record = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                      "event": "JanitorAction", **action["detail"],
                      "action": action["action"], "distillation_event_id": event_id}
            # A newline also separates any interrupted trailing record.
            stream.write("\n" + json.dumps(record) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


async def _finish(db, identity):
    row = await _locked_checkpoint(db, identity)
    if row is None:
        raise RuntimeError("distillation-checkpoint-missing")
    state = dict(row.state)
    if state["pending"]:
        for neuron_id in state["receipt"].get("neuron_ids", []):
            await stage_lesson_enrichment(db, neuron_id)
        for index, action in enumerate(state.get("actions", [])):
            _deliver_action(identity, state["episode"]["sha256"], index, action)
        state["pending"] = False
        state["actions"] = []
        row.state = state
        await db.commit()
    else:
        await db.rollback()  # release the read-side transaction and source lock
    # Invalidate only after committed enrichment. A failed projection retry
    # never inserts staged vectors into the shared semantic cache.
    from app.services.semantic_prefilter import invalidate_cache
    from app.services import adjacency_cache

    invalidate_cache()
    adjacency_cache.invalidate_adjacency_cache()
    _write_marker(state["path"], state)
    return dict(state["receipt"])


async def run_checkpointed(db, path, stage):
    path = os.path.realpath(path)
    identity = source_id(path)
    try:
        row = await _locked_checkpoint(db, identity)
        if row is not None and row.state["pending"]:
            await db.rollback()
            return {**await _finish(db, identity), "recovered": True}
        if row is None and os.path.exists(path + ".distilled"):
            raise ValueError("legacy-distillation-boundary-unverified")
        snapshot = capture(path, row.state if row else None)
        if not snapshot["events"]:
            if row is None:
                raise ValueError("distillation-no-complete-events")
            await db.rollback()
            return {**await _finish(db, identity), "recovered": True}
        if not any(e.get("event") == "Stop" and e.get("distill_ready") is True
                   for e in snapshot["events"]):
            raise ValueError("distillation-delta-not-ready")
        with tempfile.TemporaryDirectory(prefix="corvus-distillation-") as directory:
            frozen_path = materialize(snapshot, path, directory)
            receipt = await stage(db, frozen_path, known_labels=snapshot["known_labels"],
                                  delivered_ids=snapshot["delivered_ids"])
        actions = receipt.pop("_actions", [])
        state = {"path": path, "episode": snapshot["episode"],
                 "transcript": snapshot["transcript"], "receipt": receipt,
                 "actions": actions, "pending": True}
        if row is None:
            db.add(DistillationCheckpoint(source_id=identity, state=state))
        else:
            row.state = state
        await db.commit()
        return await _finish(db, identity)
    except BaseException:
        # Includes cancellation; release source ownership and any failed SQL
        # transaction before request cleanup. A prior commit remains durable.
        await db.rollback()
        raise


async def progress_status(db, episode_dir, min_quiet_minutes=30):
    """Ready work is checked against DB state, never inferred from a marker.

    Legacy/changed/missing inputs are reported separately, not silently treated
    as processed. Pending enrichment is eligible even without its source file.
    """
    if type(min_quiet_minutes) is not int or min_quiet_minutes < 0:
        raise ValueError("min_quiet_minutes must be a nonnegative integer")
    root = Path(episode_dir).resolve()
    rows = (await db.execute(select(DistillationCheckpoint))).scalars().all()
    states = {r.source_id: r.state for r in rows if Path(r.state["path"]).parent == root}
    paths = {str(p.resolve()) for p in root.glob("*.jsonl")}
    paths.update(s["path"] for s in states.values())
    ready, blocked = [], []
    cutoff = time.time() - min_quiet_minutes * 60
    for path in sorted(paths):
        state = states.get(source_id(path))
        if state and state["pending"]:
            ready.append(path)
            continue
        if state is None and os.path.exists(path + ".distilled"):
            blocked.append({"path": path, "reason": "legacy-boundary-unverified"})
            continue
        try:
            snapshot = capture(path, state)
            if os.path.getmtime(path) > cutoff:
                continue
            if any(e.get("event") == "Stop" and e.get("distill_ready") is True
                   for e in snapshot["events"]):
                ready.append(path)
            elif state and not os.path.exists(path + ".distilled"):
                ready.append(path)  # repair a missing post-commit projection
        except (OSError, ValueError, TypeError, KeyError):
            blocked.append({"path": path, "reason": "input-boundary-unreadable-or-changed"})
    return {"ready": len(ready), "paths": ready, "blocked": len(blocked),
            "blocked_inputs": blocked}
