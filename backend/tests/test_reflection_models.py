"""Reflection records are inspectable derived artifacts, never source facts."""

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import reflection_models as models


def test_legacy_declines_migrate_without_losing_source_receipts(tmp_path):
    legacy = tmp_path / "declined.json"
    legacy.write_text(json.dumps({"fingerprint": {
        "kind": "needs_review", "source_ids": [1, 2, 3],
        "source_labels": ["a", "b", "c"], "reviewed_at": "2026-09-27T00:00:00+00:00",
    }}))
    catalog = models.load_catalog(tmp_path / "models.json", legacy)
    assert catalog["schema_version"] == 1
    record = catalog["records"]["fingerprint"]
    assert record["status"] == "needs_review"
    assert record["source_ids"] == [1, 2, 3]
    assert record["source_labels"] == ["a", "b", "c"]
    assert record["history"][0]["reason"] == "legacy-declined-migration"
    models.save_catalog(catalog, tmp_path / "models.json")
    assert models.load_catalog(tmp_path / "models.json", legacy) == catalog


def test_corrupt_catalog_fails_closed(tmp_path):
    path = tmp_path / "models.json"
    path.write_text("{broken")
    with pytest.raises(models.ReflectionStoreError):
        models.load_catalog(path)
    path.write_text(json.dumps({"schema_version": 1, "records": {
        "x": {"status": "admitted", "source_ids": [1, "bad"]}}}))
    with pytest.raises(models.ReflectionStoreError):
        models.load_catalog(path)


def test_catalog_save_is_atomic_on_replace_failure(tmp_path, monkeypatch):
    path = tmp_path / "models.json"
    original = '{"schema_version":1,"records":{}}\n'
    path.write_text(original)
    monkeypatch.setattr(models.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError, match="disk"):
        models.save_catalog({"schema_version": 1, "records": {"x": {"status": "admitted"}}}, path)
    assert path.read_text() == original


def test_independent_evidence_rejects_source_skill_load_and_injection(tmp_path):
    created = datetime(2026, 9, 27, 0, 0)
    record = {"source_ids": [1, 2], "source_labels": ["source A", "source B"],
              "name": "mind-demo", "scope": "Projects", "last_evidence_at": created.isoformat()}
    episode = tmp_path / "episodes"
    episode.mkdir()
    (episode / "clean.jsonl").write_text('{"event":"PostToolUse","tool":"Bash"}\n')
    (episode / "loaded.jsonl").write_text('{"event":"PostToolUse","tool":"Skill","input":{"skill":"mind-demo"}}\n')
    (episode / "injected.jsonl").write_text('{"event":"Injection","labels":["source A"]}\n')
    def lesson(nid, session):
        return SimpleNamespace(id=nid, department="Projects", created_at=created+timedelta(days=1),
                               citation=f"[session:{session}]", content="Observed result", label=f"new {nid}")
    candidate = [lesson(3,"clean"), lesson(4,"loaded"), lesson(5,"injected"), lesson(1,"clean")]
    assert [n.id for n in models.independent_lessons(record, candidate, episode)] == [3]


@pytest.mark.asyncio
async def test_model_catalog_is_inspectable_without_mutation(tmp_path, monkeypatch):
    from app.routers import compile as compiler_router
    path = tmp_path / "models.json"
    models.save_catalog({"schema_version": 1, "records": {
        "a": {"id": "a", "status": "admitted", "reflection": {"claim": "test"}},
        "b": {"id": "b", "status": "challenged", "observations": [{"source_id": 44}]},
    }}, path)
    monkeypatch.setattr(models, "CATALOG_PATH", path)
    before = path.read_bytes()
    response = await compiler_router.compile_models()
    assert response["counts"] == {"admitted": 1, "challenged": 1}
    assert response["records"]["a"]["reflection"]["claim"] == "test"
    assert path.read_bytes() == before


def test_retire_projection_marks_matching_model_with_history():
    catalog = {"schema_version": 1, "records": {
        "one": {"status": "admitted", "name": "mind-one", "source_ids": [7, 8],
                "history": []},
        "other": {"status": "admitted", "name": "mind-other", "source_ids": [8, 9],
                  "history": []},
    }}
    assert models.retire_projection(catalog, "mind-one", [7, 8], "source-reconsolidated")
    assert catalog["records"]["one"]["status"] == "retired"
    assert catalog["records"]["one"]["history"][-1]["reason"] == "source-reconsolidated"
    assert catalog["records"]["other"]["status"] == "admitted"


def test_evidence_clock_does_not_skip_same_timestamp_later_id(tmp_path):
    stamp = datetime(2026, 9, 27, 0, 0)
    episodes = tmp_path / "episodes"
    episodes.mkdir()
    (episodes / "clean.jsonl").write_text('{"event":"Stop"}\n')
    record = {"source_ids": [1], "scope": "Projects", "last_evidence_at": stamp.isoformat(),
              "last_evidence_id": 4}
    lessons = [SimpleNamespace(id=i, created_at=stamp, department="Projects",
                               citation="[session:clean]", content="Observation")
               for i in (3, 4, 5)]
    assert [n.id for n in models.independent_lessons(record, lessons, episodes)] == [5]
