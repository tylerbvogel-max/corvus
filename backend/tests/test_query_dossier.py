"""Unit tests for the AIP Phase 3 Query Dossier pure composers.

The `build_dossier()` DB path is exercised live via the `/queries/{id}/dossier`
endpoint; tests here cover the pure composition functions that turn already-
loaded model rows into Pydantic response sections. Avoids DB fixture setup
while still exercising all aggregation logic — the tricky bits (certified
flag, integrity neuron-id overlap, empty handling) are all pure.

See `docs/design/aip-phase-3-query-dossier.md` for the full design.
"""

from __future__ import annotations

import datetime
import json
from types import SimpleNamespace

import pytest

from app.services.query_dossier import (
    _parse_id_list,
    compose_actions_section,
    compose_eval_section,
    compose_integrity_section,
    compose_output_section,
)


def _fake_ts(seconds: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 4, 22, 12, 0, seconds, tzinfo=datetime.timezone.utc)


# ── _parse_id_list ──────────────────────────────────────────────────────

def test_parse_id_list_none_returns_empty_set():
    assert _parse_id_list(None) == set()


def test_parse_id_list_empty_string_returns_empty_set():
    assert _parse_id_list("") == set()


def test_parse_id_list_valid_json_returns_ids():
    assert _parse_id_list("[1, 2, 3]") == {1, 2, 3}


def test_parse_id_list_malformed_json_is_empty():
    assert _parse_id_list("not json") == set()


def test_parse_id_list_non_list_is_empty():
    assert _parse_id_list('{"a": 1}') == set()


def test_parse_id_list_ignores_non_numeric_entries():
    # Mixed types: numbers + strings that aren't ints should be filtered.
    assert _parse_id_list('[1, "2", "abc", null]') == {1, 2}


# ── compose_eval_section ────────────────────────────────────────────────

def test_compose_eval_section_empty():
    section = compose_eval_section([], [], certified_eval_run_id=None)
    assert section.ad_hoc_scores == []
    assert section.eval_run_participations == []


def test_compose_eval_section_ad_hoc_scores_roundtrip():
    score = SimpleNamespace(
        answer_label="A", answer_mode="haiku_neuron",
        accuracy=4, completeness=5, clarity=4, faithfulness=5, overall=5,
    )
    section = compose_eval_section([score], [], certified_eval_run_id=None)
    assert len(section.ad_hoc_scores) == 1
    s = section.ad_hoc_scores[0]
    assert s.answer_label == "A"
    assert s.accuracy == 4 and s.overall == 5


def test_compose_eval_section_marks_certified_run():
    run_a = SimpleNamespace(
        id=3, suite_name="smoke", suite_hash="abc" * 10, status="completed",
        started_at=_fake_ts(0), completed_at=_fake_ts(10),
    )
    run_b = SimpleNamespace(
        id=4, suite_name="smoke", suite_hash="def" * 10, status="completed",
        started_at=_fake_ts(20), completed_at=_fake_ts(30),
    )
    case_a = SimpleNamespace(
        id=100, case_label="case-a", blocked=False,
        scores_json={"overall": 5}, violations_json=None,
    )
    case_b = SimpleNamespace(
        id=101, case_label="case-b", blocked=True,
        scores_json=None, violations_json={"critical": 1},
    )
    section = compose_eval_section(
        [], [(case_a, run_a), (case_b, run_b)], certified_eval_run_id=3,
    )
    parts = section.eval_run_participations
    assert len(parts) == 2
    # run_a is certified; run_b isn't.
    assert parts[0].eval_run_id == 3 and parts[0].certified is True
    assert parts[1].eval_run_id == 4 and parts[1].certified is False
    assert parts[1].blocked is True
    assert parts[0].scores_json == {"overall": 5}


# ── compose_output_section ──────────────────────────────────────────────

def test_compose_output_section_empty():
    section = compose_output_section([])
    assert section.violations == []


def test_compose_output_section_serializes_all_fields():
    v = SimpleNamespace(
        id=7, rule_id="pii.ssn", severity="critical", action="block",
        matched_span="SSN: 123-45-6789", redaction="[REDACTED]",
        detail={"count": 1}, action_id=42, created_at=_fake_ts(5),
    )
    section = compose_output_section([v])
    assert len(section.violations) == 1
    out = section.violations[0]
    assert out.id == 7 and out.rule_id == "pii.ssn"
    assert out.severity == "critical" and out.action == "block"
    assert out.redaction == "[REDACTED]" and out.action_id == 42
    assert out.created_at is not None and "2026-04-22" in out.created_at


# ── compose_actions_section ─────────────────────────────────────────────

def test_compose_actions_section_empty():
    section = compose_actions_section([])
    assert section.actions == []


def test_compose_actions_section_preserves_parent_tree_link_and_state():
    root = SimpleNamespace(
        id=1, kind="neuron.refine", actor_type="user", actor_id="tester",
        state="applied", requires_approval=False, reason="manual refine",
        parent_action_id=None, applied_at=_fake_ts(1), error_message=None,
        created_at=_fake_ts(0),
    )
    child = SimpleNamespace(
        id=2, kind="neuron.create", actor_type="user", actor_id="tester",
        state="failed", requires_approval=True, reason="child op",
        parent_action_id=1, applied_at=None, error_message="FK violation",
        created_at=_fake_ts(2),
    )
    section = compose_actions_section([root, child])
    assert len(section.actions) == 2
    assert section.actions[0].id == 1 and section.actions[0].state == "applied"
    assert section.actions[1].parent_action_id == 1
    assert section.actions[1].error_message == "FK violation"
    assert section.actions[1].applied_at is None


# ── compose_integrity_section ───────────────────────────────────────────

def test_compose_integrity_section_empty_selected_returns_empty():
    finding = SimpleNamespace(
        id=1, scan_id=1, finding_type="duplicate", severity="warning",
        priority_score=0.5, description="...", status="open", resolution=None,
        neuron_ids_json="[1, 2, 3]", created_at=_fake_ts(0),
    )
    # No selected_neuron_ids means no attribution possible.
    section = compose_integrity_section([finding], None)
    assert section.findings == []


def test_compose_integrity_section_filters_non_overlapping():
    overlapping = SimpleNamespace(
        id=10, scan_id=1, finding_type="duplicate", severity="warning",
        priority_score=0.9, description="dup cluster", status="open",
        resolution=None, neuron_ids_json="[5, 6, 7]", created_at=_fake_ts(0),
    )
    non_overlapping = SimpleNamespace(
        id=11, scan_id=1, finding_type="orphan", severity="info",
        priority_score=0.1, description="orphan node", status="open",
        resolution=None, neuron_ids_json="[999, 1000]", created_at=_fake_ts(1),
    )
    # Query selected neurons include 6 (overlaps with finding 10).
    section = compose_integrity_section(
        [overlapping, non_overlapping],
        json.dumps([5, 6, 42]),
    )
    assert len(section.findings) == 1
    out = section.findings[0]
    assert out.id == 10
    assert out.attributed_via == "selected_neurons"
    assert out.overlapping_neuron_ids == [5, 6]


def test_compose_integrity_section_malformed_neuron_ids_skipped():
    bad = SimpleNamespace(
        id=20, scan_id=1, finding_type="x", severity="info",
        priority_score=0.0, description=None, status="open", resolution=None,
        neuron_ids_json="not json", created_at=_fake_ts(0),
    )
    section = compose_integrity_section([bad], json.dumps([1, 2, 3]))
    # Malformed neuron_ids_json → empty set → no overlap → skipped.
    assert section.findings == []


def test_compose_integrity_section_multiple_overlaps_are_sorted():
    f = SimpleNamespace(
        id=30, scan_id=1, finding_type="duplicate", severity="warning",
        priority_score=0.5, description="", status="open", resolution=None,
        neuron_ids_json="[99, 3, 17, 2]", created_at=_fake_ts(0),
    )
    section = compose_integrity_section([f], json.dumps([99, 17, 2, 5000]))
    assert len(section.findings) == 1
    assert section.findings[0].overlapping_neuron_ids == [2, 17, 99]
