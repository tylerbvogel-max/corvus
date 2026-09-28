"""Durable derived-model catalog and independent-evidence boundary.

The graph remains the source of facts. This catalog records hypotheses and
procedures inferred from those facts, including failed admissions. It is a
build/review artifact, never a replacement for the cited lessons.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

CATALOG_PATH = Path(os.path.expanduser("~/.corvus-mind/reflection-models.json"))
LEGACY_DECLINED_PATH = Path(os.path.expanduser("~/.corvus-mind/declined-skill-candidates.json"))
SCHEMA_VERSION = 1
_SESSION_REF = re.compile(r"\[session:([^\]]+)\]")
MAX_EPISODE_BYTES = 2_000_000
STATUSES = frozenset({"admitted", "challenged", "declined", "needs_review", "retired"})


class ReflectionStoreError(RuntimeError):
    """A malformed state file must stop publication, never reset it."""


def _empty() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "records": {}}


def _validate_catalog(catalog: Any) -> None:
    if (not isinstance(catalog, dict)
            or catalog.get("schema_version") != SCHEMA_VERSION
            or not isinstance(catalog.get("records"), dict)):
        raise ReflectionStoreError("reflection catalog schema is invalid")
    for key, record in catalog["records"].items():
        if (not isinstance(key, str) or not isinstance(record, dict)
                or record.get("status") not in STATUSES
                or not isinstance(record.get("source_ids", []), list)
                or any(type(nid) is not int or nid <= 0
                       for nid in record.get("source_ids", []))
                or not isinstance(record.get("history", []), list)):
            raise ReflectionStoreError(f"reflection catalog record is invalid: {key}")


def load_catalog(
    path: Path | None = None,
    legacy_declined: Path | None = None,
) -> dict[str, Any]:
    path = Path(path or CATALOG_PATH)
    if path.exists():
        try:
            catalog = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ReflectionStoreError(f"cannot read reflection catalog: {path}") from exc
        _validate_catalog(catalog)
        return catalog
    catalog = _empty()
    legacy_declined = Path(legacy_declined or LEGACY_DECLINED_PATH)
    if not legacy_declined.exists():
        return catalog
    try:
        legacy = json.loads(legacy_declined.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReflectionStoreError("cannot migrate legacy declined candidates") from exc
    if not isinstance(legacy, dict):
        raise ReflectionStoreError("legacy declined candidates are malformed")
    for fingerprint, old in legacy.items():
        if not isinstance(fingerprint, str) or not isinstance(old, dict):
            raise ReflectionStoreError("legacy declined candidate is malformed")
        kind = old.get("kind")
        if not isinstance(kind, str):
            raise ReflectionStoreError("legacy decline kind is missing")
        at = old.get("reviewed_at") or datetime.now(timezone.utc).isoformat()
        catalog["records"][fingerprint] = {
            "id": fingerprint, "status": "needs_review" if kind == "needs_review" else "declined",
            "kind": kind, "source_ids": old.get("source_ids", []),
            "source_labels": old.get("source_labels", []),
            "history": [{"at": at, "from": None,
                         "to": "needs_review" if kind == "needs_review" else "declined",
                         "reason": "legacy-declined-migration"}],
        }
    return catalog


def save_catalog(catalog: dict[str, Any], path: Path | None = None) -> None:
    _validate_catalog(catalog)
    path = Path(path or CATALOG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as fh:
            staged = fh.name
            json.dump(catalog, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(staged, path)
    finally:
        if staged and os.path.exists(staged):
            os.unlink(staged)


def retire_projection(catalog: dict, name: str, source_ids: list[int], reason: str) -> bool:
    """Retire only the model backing this exact generated projection."""
    for record in catalog["records"].values():
        if (record.get("status") == "admitted" and record.get("name") == name
                and set(record.get("source_ids", [])) == set(source_ids)):
            record["status"] = "retired"
            record.setdefault("history", []).append({
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "from": "admitted", "to": "retired", "reason": reason,
            })
            return True
    return False


def _source_session(neuron: Any) -> str | None:
    for value in (getattr(neuron, "citation", None), getattr(neuron, "content", None)):
        match = _SESSION_REF.search(value or "")
        if match:
            return match.group(1)
    return None


def _unexposed_session(record: dict, session_id: str, episode_dir: Path) -> bool:
    path = episode_dir / f"{session_id}.jsonl"
    try:
        if not path.is_file() or path.stat().st_size > MAX_EPISODE_BYTES:
            return False
    except OSError:
        return False
    labels = [str(label).casefold() for label in record.get("source_labels", [])]
    name = record.get("name")
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("event") == "PostToolUse" and event.get("tool") == "Skill":
                    if (event.get("input") or {}).get("skill") == name:
                        return False
                if event.get("event") == "SkillPointer":
                    if any(item.get("name") == name for item in event.get("skills", [])
                           if isinstance(item, dict)):
                        return False
                if event.get("event") == "Injection":
                    exposed_ids = event.get("neuron_ids", [])
                    if not isinstance(exposed_ids, list):
                        return False
                    if any(nid in record.get("source_ids", []) for nid in exposed_ids):
                        return False
                    exposed_labels = event.get("labels", [])
                    if not isinstance(exposed_labels, list):
                        return False
                    exposed = [str(label).casefold() for label in exposed_labels]
                    if any(a and b and (a in b or b in a)
                           for a in labels for b in exposed):
                        return False
    except OSError:
        return False
    return True


def independent_lessons(record: dict, lessons: list, episode_dir: Path) -> list:
    """Return later lesson evidence that could not echo this model's delivery.

    Lack of a source-session receipt is uncertainty, not independence.
    This only nominates observations; semantic corroboration is judged later.
    """
    try:
        cutoff = datetime.fromisoformat(record["last_evidence_at"])
    except (KeyError, TypeError, ValueError):
        return []
    if cutoff.tzinfo is not None:
        cutoff = cutoff.astimezone(timezone.utc).replace(tzinfo=None)
    source_ids = set(record.get("source_ids", []))
    last_id = record.get("last_evidence_id", 0)
    eligible = []
    for lesson in lessons:
        created = getattr(lesson, "created_at", None)
        if created is None:
            continue
        if created.tzinfo is not None:
            created = created.astimezone(timezone.utc).replace(tzinfo=None)
        if ((created, lesson.id) <= (cutoff, last_id) or lesson.id in source_ids
                or lesson.department != record.get("scope")
                or not getattr(lesson, "is_active", True)
                or getattr(lesson, "superseded_by", None) is not None):
            continue
        session = _source_session(lesson)
        if session and _unexposed_session(record, session, Path(episode_dir)):
            eligible.append(lesson)
    return sorted(eligible, key=lambda lesson: (lesson.created_at, lesson.id))
