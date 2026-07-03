"""Tests for regulatory citation coverage detection (CFR gap -> EmergentQueue)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.config import settings
from app.models import EmergentQueue
import app.services.regulatory_coverage as rc


# ---- CFR extraction ----

def test_extract_cfr_refs_forms_and_aliases():
    text = "Per 48 CFR 31.205-6, 14 CFR 25.1309, FAR 31.205-6, and DFARS 252.204-7012."
    refs = rc.extract_cfr_refs(text)
    # FAR 31.205-6 normalises to 48 CFR 31.205-6 (same as the explicit one).
    assert refs == {"48 CFR 31.205-6", "14 CFR 25.1309", "48 CFR 252.204-7012"}


def test_extract_ignores_noise():
    assert rc.extract_cfr_refs("The F-16 flies; CFR is just an acronym here.") == set()


# ---- coverage-gap diffing ----

def test_record_queues_only_uncovered(monkeypatch):
    monkeypatch.setattr(settings, "regulatory_coverage_detection_enabled", True)
    seen = []

    async def fake_upsert(db, ref, qid):
        seen.append((ref, qid))

    monkeypatch.setattr(rc, "_upsert_coverage_gap", fake_upsert)
    answer = "See 48 CFR 31.205-6 and 14 CFR 25.1309."
    out = asyncio.run(rc.record_regulatory_coverage_gaps(
        AsyncMock(), answer, {"48 CFR 31.205-6"}, 42,
    ))
    assert out == ["14 CFR 25.1309"]  # the resolved one is not queued
    assert seen == [("14 CFR 25.1309", 42)]


def test_record_disabled_is_noop(monkeypatch):
    monkeypatch.setattr(settings, "regulatory_coverage_detection_enabled", False)
    out = asyncio.run(rc.record_regulatory_coverage_gaps(
        AsyncMock(), "48 CFR 31.205-6", set(), 1,
    ))
    assert out == []


# ---- EmergentQueue upsert ----

def test_upsert_creates_regulatory_entry():
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()
    asyncio.run(rc._upsert_coverage_gap(db, "48 CFR 31.205-6", 7))
    assert db.add.call_count == 1
    entry = db.add.call_args[0][0]
    assert isinstance(entry, EmergentQueue)
    assert entry.citation_pattern == "48 CFR 31.205-6"
    assert entry.domain == "regulatory"
    assert entry.family == "48 CFR 31"


def test_upsert_bumps_existing_pending():
    db = AsyncMock()
    existing = MagicMock()
    existing.status = "pending"
    existing.detection_count = 1
    existing.detected_in_query_ids = "[]"
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()
    asyncio.run(rc._upsert_coverage_gap(db, "48 CFR 31.205-6", 9))
    assert existing.detection_count == 2
    assert db.add.call_count == 0
