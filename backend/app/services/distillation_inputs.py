"""Bounded immutable input snapshots and append-only cursor validation."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

# Conservative safety bounds, not measured corpus optima. Exceeding one is an
# explicit repair condition; never truncate and mark unseen evidence processed.
MAX_INPUT_BYTES = 16 * 1024 * 1024


def source_id(path: str) -> str:
    return hashlib.sha256(os.path.realpath(path).encode()).hexdigest()


def read_prefix(path: str, previous: dict | None = None) -> tuple[bytes, dict]:
    """Capture complete JSONL records, verifying every previously committed byte."""
    with open(path, "rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("distillation-input-too-large")
    end = raw.rfind(b"\n") + 1
    data = raw[:end]
    previous = previous or {"bytes": 0, "sha256": hashlib.sha256(b"").hexdigest()}
    offset = previous["bytes"]
    if type(offset) is not int or offset < 0 or offset > len(data):
        raise ValueError("distillation-input-truncated")
    if hashlib.sha256(data[:offset]).hexdigest() != previous["sha256"]:
        raise ValueError("distillation-input-prefix-changed")
    return data[offset:], {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def records(data: bytes) -> list[dict]:
    result = []
    for line in data.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError("distillation-record-not-object")
        result.append(item)
    return result


def capture(path: str, previous: dict | None = None) -> dict:
    """The delta is extraction evidence; prior injections are exclusion context."""
    previous = previous or {}
    delta, cursor = read_prefix(path, previous.get("episode"))
    events = records(delta)
    # Read the exact captured prefix, not an unbounded growing file. A second
    # read must match the captured hash or this attempt is rejected.
    with open(path, "rb") as stream:
        full = stream.read(cursor["bytes"])
    if hashlib.sha256(full).hexdigest() != cursor["sha256"]:
        raise ValueError("distillation-input-changed-during-capture")
    all_events = records(full)
    transcript_path = next((e["transcript_path"] for e in reversed(all_events)
                            if e.get("event") == "Stop" and e.get("transcript_path")), None)
    old_transcript = previous.get("transcript")
    if old_transcript and transcript_path != old_transcript["path"]:
        raise ValueError("distillation-transcript-identity-changed")
    transcript = None
    transcript_delta = b""
    if transcript_path:
        transcript_delta, transcript_cursor = read_prefix(
            transcript_path, old_transcript.get("cursor") if old_transcript else None)
        records(transcript_delta)
        transcript = {"path": transcript_path, "cursor": transcript_cursor}
    prior_count = len(all_events) - len(events)
    prior_injections = [e for e in all_events[:prior_count] if e.get("event") == "Injection"]
    return {
        "episode": cursor, "transcript": transcript, "events": events,
        "transcript_delta": transcript_delta,
        "known_labels": [str(label) for e in prior_injections for label in e.get("labels", [])],
        "delivered_ids": {n for e in prior_injections for n in e.get("neuron_ids", [])
                          if type(n) is int},
    }


def materialize(snapshot: dict, path: str, directory: str) -> str:
    """Private temporary files retain the existing parsers, never the live inputs."""
    transcript = Path(directory) / "transcript" / "source.jsonl"
    transcript.parent.mkdir()
    transcript.write_bytes(snapshot["transcript_delta"])
    target = Path(directory) / "episode" / Path(path).name
    target.parent.mkdir()
    events = [{**e, "transcript_path": str(transcript)} if e.get("event") == "Stop" else e
              for e in snapshot["events"]]
    target.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return str(target)
