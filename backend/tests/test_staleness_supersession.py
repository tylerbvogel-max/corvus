"""Staleness durability gate (mind-identity-fact-supersession).

The LoCoMo certificate forensics showed the staleness resolver superseding
by recency alone: of 29 label-recoverable supersessions, only 9 were genuine
perishable-state updates; 13 fired on compatible pairs with durable
casualties, and 13 of 55 actions were repeat-fires re-halving the same
neuron's utility every janitor pass. These tests plant a durable pair and a
perishable pair and require BOTH directions: the durable fact survives, the
contradicted perishable fact is still superseded. Hermetic — fake session,
patched edge/action plumbing.
"""
import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

import app.services.mind_janitors as mj
from app.models import IntegrityFinding, Neuron
from app.services.integrity.conflict_monitor import (
    _classify_and_build_findings, _drop_already_flagged,
)
from app.services.integrity.similarity import SimilarPair


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeSession:
    def __init__(self, rows_by_id=None, execute_rows=None):
        self._rows_by_id = rows_by_id or {}
        self._execute_rows = execute_rows or []
        self.added = []

    async def get(self, cls, id_):
        return self._rows_by_id.get((cls, int(id_)))

    async def execute(self, _stmt):
        return _FakeResult(self._execute_rows)

    def add(self, obj):
        self.added.append(obj)


def _lesson(nid, label, created_at, utility=0.5, superseded_by=None):
    return Neuron(
        id=nid, label=label, content=label, summary=label,
        department="User", layer=3, node_type="lesson",
        source_origin="distiller", is_active=True, invocations=1,
        avg_utility=utility, superseded_by=superseded_by,
        created_at=created_at,
    )


def _finding(fid, a_id, b_id, classification, hint=None, resolution=None):
    detail = {"classification": classification, "resolution_hint": hint}
    return IntegrityFinding(
        id=fid, finding_type="contradiction", status="open",
        severity="warning" if classification == "contradictory" else "info",
        neuron_ids_json=json.dumps([a_id, b_id]),
        detail_json=json.dumps(detail),
        description="test pair", priority_score=0.8, resolution=resolution,
    )


def _pair_session(older, newer):
    return _FakeSession(rows_by_id={
        (Neuron, older.id): older, (Neuron, newer.id): newer,
    })


# ── planted-pair regression: both directions ─────────────────────────────

@pytest.mark.asyncio
async def test_perishable_state_update_is_still_superseded():
    """The contradicted perishable fact must still lose — a gate that only
    protects durable facts has disabled the feature."""
    older = _lesson(10, "Quinn heading to Tokyo next month",
                    datetime(2026, 7, 1))
    newer = _lesson(11, "Quinn cancelled the Tokyo trip",
                    datetime(2026, 7, 10))
    finding = _finding(1, 10, 11, "contradictory", hint="state_update")
    db = _pair_session(older, newer)
    actions = []
    with patch.object(mj, "_add_memory_edge", new=AsyncMock()) as edge, \
         patch.object(mj, "_log_action", side_effect=lambda a, d: actions.append(a)):
        out = await mj._resolve_contradiction(db, finding)
    assert out["verdict"] == "superseded"
    assert older.superseded_by == 11
    assert older.avg_utility == pytest.approx(0.25)
    assert finding.status == "resolved"
    assert finding.resolution == "superseded"
    edge.assert_awaited_once()
    assert "staleness.superseded" in actions


@pytest.mark.asyncio
async def test_durable_standing_conflict_survives_janitor_pass():
    """Two standing claims in genuine conflict: recency cannot arbitrate.
    No mutation; the pair goes to the integrity inbox."""
    older = _lesson(20, "Riley is a musician", datetime(2026, 7, 1))
    newer = _lesson(21, "Riley is a photographer", datetime(2026, 7, 10))
    finding = _finding(2, 20, 21, "contradictory", hint="standing_conflict")
    db = _pair_session(older, newer)
    actions = []
    with patch.object(mj, "_add_memory_edge", new=AsyncMock()) as edge, \
         patch.object(mj, "_log_action", side_effect=lambda a, d: actions.append(a)):
        out = await mj._resolve_contradiction(db, finding)
    assert out["verdict"] == "review_flagged"
    assert older.superseded_by is None
    assert older.avg_utility == pytest.approx(0.5)
    assert finding.status == "open"          # human's call, not the janitor's
    assert finding.resolution == "needs_review"
    edge.assert_not_awaited()
    assert "staleness.review_flagged" in actions


@pytest.mark.asyncio
async def test_ambiguous_classification_never_supersedes():
    """Live receipt from the standing corpus: an 'ambiguous' pair (wedding
    photo vs greenhouse wedding) was auto-superseded. Ambiguous means the
    classifier could not establish a contradiction — no mutation."""
    older = _lesson(30, "Morgan's wedding in a greenhouse",
                    datetime(2026, 7, 1))
    newer = _lesson(31, "Morgan shared wedding dress photo",
                    datetime(2026, 7, 10))
    finding = _finding(3, 30, 31, "ambiguous")
    db = _pair_session(older, newer)
    with patch.object(mj, "_add_memory_edge", new=AsyncMock()) as edge, \
         patch.object(mj, "_log_action"):
        out = await mj._resolve_contradiction(db, finding)
    assert out["verdict"] == "review_flagged"
    assert older.superseded_by is None
    assert finding.status == "open"
    edge.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_finding_without_hint_fails_closed():
    """Findings filed before the hint existed must not auto-retire."""
    older = _lesson(40, "Jordan joined a rock band", datetime(2026, 7, 1))
    newer = _lesson(41, "Jordan left the rock band", datetime(2026, 7, 10))
    finding = _finding(4, 40, 41, "contradictory", hint=None)
    db = _pair_session(older, newer)
    with patch.object(mj, "_add_memory_edge", new=AsyncMock()), \
         patch.object(mj, "_log_action"):
        out = await mj._resolve_contradiction(db, finding)
    assert out["verdict"] == "review_flagged"
    assert older.superseded_by is None


@pytest.mark.asyncio
async def test_already_superseded_older_is_not_redemoted():
    """Repeat-fire guard: the certificate runs re-halved the same loser's
    utility every pass (0.5 → 0.25 → 0.125). Resolve without mutating."""
    older = _lesson(50, "Casey's dogs photo", datetime(2026, 7, 1),
                    utility=0.25, superseded_by=51)
    newer = _lesson(51, "Casey's dogs outdoors", datetime(2026, 7, 10))
    finding = _finding(5, 50, 51, "contradictory", hint="state_update")
    db = _pair_session(older, newer)
    with patch.object(mj, "_add_memory_edge", new=AsyncMock()) as edge, \
         patch.object(mj, "_log_action", side_effect=lambda a, d: None):
        out = await mj._resolve_contradiction(db, finding)
    assert out["verdict"] == "already_superseded"
    assert older.avg_utility == pytest.approx(0.25)   # NOT re-halved
    assert finding.status == "resolved"
    assert finding.resolution == "already_superseded"
    edge.assert_not_awaited()


# ── scan-side repeat-fire prevention ─────────────────────────────────────

@pytest.mark.asyncio
async def test_scan_drops_pairs_already_flagged_either_order():
    flagged = _FakeSession(execute_rows=[
        json.dumps([11, 10]),          # stored in reverse order on purpose
        json.dumps([98, 99]),
    ])
    keep = SimilarPair(neuron_a_id=12, neuron_b_id=13, similarity=0.8)
    drop = SimilarPair(neuron_a_id=10, neuron_b_id=11, similarity=0.8)
    out = await _drop_already_flagged(flagged, [drop, keep])
    assert out == [keep]


# ── classifier hint plumbing ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_classifier_stores_hint_and_rejects_garbage():
    pairs = [
        SimilarPair(neuron_a_id=1, neuron_b_id=2, similarity=0.8,
                    a_label="plan A", b_label="plan B"),
        SimilarPair(neuron_a_id=3, neuron_b_id=4, similarity=0.8,
                    a_label="fact A", b_label="fact B"),
    ]
    canned = json.dumps([
        {"pair_index": 0, "classification": "contradictory",
         "resolution_hint": "state_update", "reasoning": "same plan"},
        {"pair_index": 1, "classification": "contradictory",
         "resolution_hint": "totally-bogus", "reasoning": "conflict"},
    ])
    db = _FakeSession(execute_rows=[])

    async def fake_llm_chat(**_kw):
        return {"text": canned}

    with patch("app.services.llm_provider.llm_chat", new=fake_llm_chat):
        findings = await _classify_and_build_findings(db, pairs, batch_size=5)
    assert len(findings) == 2
    d0 = json.loads(findings[0].detail_json)
    d1 = json.loads(findings[1].detail_json)
    assert d0["resolution_hint"] == "state_update"
    assert d1["resolution_hint"] is None       # garbage hint rejected
