import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import roadmap_admission as backend_admission
from app.services.roadmap_admission import cache_document, project_ledger


HOOK_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "harness" / "claude-code" / "roadmap_admission.py"
)
SPEC = importlib.util.spec_from_file_location("roadmap_hook_admission", HOOK_MODULE_PATH)
hook = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(hook)


def _state():
    return {
        "version": 11,
        "updatedAt": "2026-07-25T00:00:00+00:00",
        "sections": [{"id": "forward", "label": "Forward"}],
        "nodes": [
            {
                "id": "done",
                "section": "forward",
                "label": "Finished",
                "status": "done",
            },
            {
                "id": "active",
                "section": "forward",
                "label": "Admission controller",
                "status": "active",
                "horizon": "active",
                "prereqs": ["done"],
            },
            {
                "id": "later",
                "section": "forward",
                "label": "Later",
                "status": "planned",
                "horizon": "horizon-2",
                "prereqs": ["active"],
            },
        ],
        "edges": [],
        "milestones": [],
    }


def _row(project_path="/work/corvus", revision=7):
    return SimpleNamespace(
        slug="corvus-long-horizon",
        name="Corvus / Long Horizon",
        description="Durable roadmap",
        project_path=project_path,
        revision=revision,
        state=_state(),
    )


def _cache(revision=7):
    return cache_document([_row(revision=revision)])


def _payload(tool_name, tool_input, cwd="/home/tyler"):
    return {
        "session_id": "session-1",
        "cwd": cwd,
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
    }


def test_projection_prioritizes_active_records_and_keeps_full_index():
    projected = project_ledger(_row())
    assert projected["candidates"][0]["id"] == "active"
    assert projected["candidates"][0]["ready"] is True
    assert projected["record_index"]["done"]["status"] == "done"
    assert projected["revision"] == 7


def test_admission_requires_unfinished_record_or_reasoned_override(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(backend_admission, "EPISODE_DIR", tmp_path)
    with pytest.raises(ValueError, match="unfinished"):
        backend_admission.admit_session(
            ledger=_row(),
            session_id="session-1",
            mode="bound",
            record_id="done",
            reason=None,
            cwd="/work/corvus",
            harness="codex",
        )
    with pytest.raises(ValueError, match="requires a reason"):
        backend_admission.admit_session(
            ledger=_row(),
            session_id="session-1",
            mode="off-ledger",
            record_id=None,
            reason="",
            cwd="/work/corvus",
            harness="codex",
        )
    override = backend_admission.admit_session(
        ledger=_row(),
        session_id="session-1",
        mode="off-ledger",
        record_id=None,
        reason="Production incident outside the current plan",
        cwd="/work/corvus",
        harness="codex",
    )
    assert override["mode"] == "off-ledger"
    assert override["ledger_revision"] == 7


def test_mutation_target_resolves_project_when_session_started_above_repo():
    cache = _cache()
    payload = _payload(
        "Bash",
        {"command": "npm run build", "workdir": "/work/corvus/frontend"},
    )
    assert hook.resolve_ledger(cache, payload)["slug"] == "corvus-long-horizon"
    assert hook.is_material_tool(payload) is True


def test_read_only_exploration_remains_allowed():
    cache = _cache()
    payload = _payload(
        "Bash",
        {"command": "git status --short", "workdir": "/work/corvus"},
    )
    assert hook.is_material_tool(payload) is False
    assert hook.gate(payload, cache) is None


def test_mapped_mutation_blocks_until_revision_pinned_admission(tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "EPISODE_DIR", tmp_path)
    cache = _cache(revision=7)
    payload = _payload(
        "apply_patch",
        {"patch": "*** Update File: /work/corvus/app.py\n@@\n-old\n+new\n"},
    )
    blocked = hook.gate(payload, cache)
    assert blocked["decision"] == "block"
    assert "roadmap_admit" in blocked["reason"]

    hook.append_event("session-1", {
        "event": "PlanningAdmission",
        "ledger_slug": "corvus-long-horizon",
        "ledger_revision": 7,
        "record_id": "active",
        "record_label": "Admission controller",
        "mode": "bound",
    })
    assert hook.gate(payload, cache) is None


def test_stale_admission_and_cross_project_mutation_are_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr(hook, "EPISODE_DIR", tmp_path)
    hook.append_event("session-1", {
        "event": "PlanningAdmission",
        "ledger_slug": "corvus-long-horizon",
        "ledger_revision": 6,
        "record_id": "active",
        "mode": "bound",
    })
    payload = _payload(
        "Bash",
        {"command": "npm run build", "workdir": "/work/corvus"},
    )
    assert "stale" in hook.gate(payload, _cache(revision=7))["reason"].lower()

    second = _row(project_path="/work/other", revision=2)
    second.slug = "other-ledger"
    second.name = "Other"
    cache = cache_document([_row(), second])
    spanning = _payload(
        "Bash",
        {
            "command": "cp /work/corvus/a.txt /work/other/a.txt",
            "workdir": "/work/corvus",
        },
    )
    decision = hook.gate(spanning, cache)
    assert "multiple mapped projects" in decision["reason"]


def test_stop_receipt_records_material_work_without_closing_roadmap(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(hook, "EPISODE_DIR", tmp_path)
    hook.append_event("session-1", {
        "event": "PlanningAdmission",
        "ledger_slug": "corvus-long-horizon",
        "ledger_revision": 7,
        "record_id": "active",
        "record_label": "Admission controller",
        "mode": "bound",
    })
    hook.append_event("session-1", {
        "event": "PostToolUse",
        "tool": "apply_patch",
        "input": {"path": "/work/corvus/app.py"},
        "ok": True,
    })
    receipt = hook.planning_return("session-1")
    assert receipt["status"] == "reconcile-required"
    assert receipt["material_tool_count"] == 1
    stored = [
        json.loads(line)
        for line in (tmp_path / "session-1.jsonl").read_text().splitlines()
    ]
    assert stored[-1]["event"] == "PlanningReturn"
    assert "durable roadmap status remains unchanged" in stored[-1]["note"]
