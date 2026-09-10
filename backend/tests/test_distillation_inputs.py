"""Synthetic append-only evidence, not personal episode fixtures."""

import json
from pathlib import Path

import pytest

from app.services.distillation_inputs import capture, materialize, read_prefix, records


def write(path, events):
    path.write_text("".join(json.dumps(e) + "\n" for e in events))


def test_appended_events_and_transcript_exclude_committed_evidence(tmp_path):
    log, transcript = tmp_path / "synthetic.jsonl", tmp_path / "source.jsonl"
    write(transcript, [{"type": "user", "message": {"content": "synthetic old"}}])
    old = [{"event": "Injection", "labels": ["known"], "neuron_ids": [7]},
           {"event": "Stop", "distill_ready": True, "transcript_path": str(transcript)}]
    write(log, old)
    first = capture(str(log))
    with transcript.open("a") as stream:
        stream.write(json.dumps({"type": "user", "message": {"content": "synthetic new"}}) + "\n")
    write(log, old + [{"event": "Stop", "distill_ready": True}])
    second = capture(str(log), first)
    assert second["events"] == [{"event": "Stop", "distill_ready": True}]
    assert second["known_labels"] == ["known"]
    assert second["delivered_ids"] == {7}
    assert b"synthetic old" not in second["transcript_delta"]
    assert b"synthetic new" in second["transcript_delta"]


@pytest.mark.parametrize("replacement", [b"", b'{"event":"Changed"}\n'])
def test_committed_prefix_cannot_be_truncated_or_rewritten(tmp_path, replacement):
    log = tmp_path / "synthetic.jsonl"
    write(log, [{"event": "Stop", "distill_ready": True}])
    first = capture(str(log))
    log.write_bytes(replacement)
    with pytest.raises(ValueError, match="distillation-input"):
        capture(str(log), first)


def test_partial_trailing_record_is_not_consumed(tmp_path):
    log = tmp_path / "synthetic.jsonl"
    log.write_bytes(b'{"event":"Stop"}\n{"event":')
    delta, cursor = read_prefix(str(log))
    assert records(delta) == [{"event": "Stop"}]
    assert cursor["bytes"] == len(b'{"event":"Stop"}\n')


@pytest.mark.parametrize("data", [b"[]\n", b"null\n", b"broken\n"])
def test_malformed_complete_record_is_not_silently_consumed(data):
    with pytest.raises(ValueError):
        records(data)


def test_frozen_parsers_cannot_read_later_live_transcript_content(tmp_path):
    log, transcript = tmp_path / "synthetic.jsonl", tmp_path / "source.jsonl"
    write(transcript, [{"type": "user", "message": {"content": "captured"}}])
    write(log, [{"event": "Stop", "distill_ready": True, "transcript_path": str(transcript)}])
    snapshot = capture(str(log))
    write(transcript, [{"type": "user", "message": {"content": "later"}}])
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    result = materialize(snapshot, str(log), str(frozen))
    stop = json.loads(Path(result).read_text(encoding="utf-8"))
    assert "captured" in Path(stop["transcript_path"]).read_text(encoding="utf-8")


def test_changed_transcript_identity_requires_explicit_repair(tmp_path):
    log, transcript = tmp_path / "synthetic.jsonl", tmp_path / "source.jsonl"
    write(transcript, [])
    first_events = [{"event": "Stop", "distill_ready": True, "transcript_path": str(transcript)}]
    write(log, first_events)
    first = capture(str(log))
    write(log, first_events + [{"event": "Stop", "transcript_path": "different"}])
    with pytest.raises(ValueError, match="identity-changed"):
        capture(str(log), first)


def test_episode_named_transcript_does_not_overwrite_frozen_transcript(tmp_path):
    log, transcript = tmp_path / "transcript.jsonl", tmp_path / "source.jsonl"
    write(transcript, [{"type": "user", "message": {"content": "synthetic evidence"}}])
    write(log, [{"event": "Stop", "distill_ready": True, "transcript_path": str(transcript)}])
    snapshot = capture(str(log))
    frozen = tmp_path / "frozen"
    frozen.mkdir()
    result = materialize(snapshot, str(log), str(frozen))
    stop = json.loads(Path(result).read_text(encoding="utf-8"))
    assert stop["transcript_path"] != result
    assert "synthetic evidence" in Path(stop["transcript_path"]).read_text(encoding="utf-8")
