"""Horizontal reconciler (plat-reconciler) — hermetic tests.

Covers: cross-region pair filtering, homonym/synonym finding construction,
judge response parsing, and region routing alignment. LLM + DB mocked.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.integrity.similarity import SimilarPair
from app.services.integrity.reconciler import (
    _build_homonym_finding, _contradiction_findings_from_verdicts,
    _cross_region_pairs, _filter_unjudged, _judge_homonym_batch,
    _pair_regions,
)


def _pair(a_id=1, b_id=2, sim=0.85, a_dept="Design", b_dept="Manufacturing & Operations"):
    return SimilarPair(
        neuron_a_id=a_id, neuron_b_id=b_id, similarity=sim,
        a_label=f"n{a_id}", b_label=f"n{b_id}",
        a_department=a_dept, b_department=b_dept,
        a_layer=3, b_layer=3,
    )


# ── Cross-region pair filtering ──────────────────────────────────────

def test_cross_region_keeps_different_regions():
    pairs = [_pair()]
    assert _cross_region_pairs(pairs) == pairs


def test_cross_region_drops_same_region():
    assert _cross_region_pairs([_pair(a_dept="Design", b_dept="Design")]) == []


def test_cross_region_drops_unknown_regions():
    assert _cross_region_pairs([_pair(a_dept=None, b_dept="Design")]) == []
    assert _cross_region_pairs([_pair(a_dept="", b_dept="Design")]) == []


def test_pair_regions_tuple():
    assert _pair_regions(_pair()) == ("Design", "Manufacturing & Operations")


# ── Incremental sweeps: judged-pair exclusion ────────────────────────

def test_filter_unjudged_excludes_seen_pairs():
    pairs = [_pair(1, 2), _pair(3, 4)]
    judged = {frozenset((2, 1))}  # order-insensitive
    remaining = _filter_unjudged(pairs, judged)
    assert [(p.neuron_a_id, p.neuron_b_id) for p in remaining] == [(3, 4)]


def test_filter_unjudged_empty_judged_passthrough():
    pairs = [_pair(1, 2)]
    assert _filter_unjudged(pairs, set()) == pairs


# ── Contradiction verdict conversion ─────────────────────────────────

def test_contradiction_verdicts_route_and_flag_cross_region():
    candidates = [_pair(1, 2), _pair(3, 4, a_dept="Legal", b_dept="HR")]
    verdicts = [
        {"pair_index": 0, "classification": "consistent", "reasoning": "fine"},
        {"pair_index": 1, "classification": "contradictory", "reasoning": "conflict"},
    ]
    findings, regions = _contradiction_findings_from_verdicts(candidates, verdicts)
    assert len(findings) == 1
    assert regions == ["Legal"]
    detail = json.loads(findings[0].detail_json)
    assert detail["cross_region"] is True
    assert detail["owning_regions"] == ["Legal", "HR"]
    assert findings[0].severity == "warning"


def test_contradiction_verdicts_ignore_bad_indices():
    findings, regions = _contradiction_findings_from_verdicts(
        [_pair()], [{"pair_index": 9, "classification": "contradictory"}],
    )
    assert findings == [] and regions == []


# ── Homonym/synonym findings ─────────────────────────────────────────

def test_synonym_finding_proposes_link():
    finding = _build_homonym_finding(_pair(), "synonym", "same concept")
    assert finding.finding_type == "homonym_synonym"
    detail = json.loads(finding.detail_json)
    assert detail["suggested_resolution"] == "linked"
    assert detail["relation"] == "synonym"
    assert detail["owning_regions"] == ["Design", "Manufacturing & Operations"]
    assert finding.neuron_ids == [1, 2]


def test_homonym_finding_proposes_separation():
    finding = _build_homonym_finding(_pair(), "homonym", "false friend")
    detail = json.loads(finding.detail_json)
    assert detail["suggested_resolution"] == "differentiated"
    assert finding.severity == "warning"  # pollution risk outranks link nicety
    assert "false friend" in finding.description or "keep separate" in finding.description


# ── Judge response parsing ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_judge_parses_clean_response():
    verdicts_json = json.dumps([
        {"pair_index": 0, "relation": "synonym", "reasoning": "same thing"},
        {"pair_index": 1, "relation": "homonym", "reasoning": "different"},
    ])
    with patch(
        "app.services.llm_provider.llm_chat",
        new=AsyncMock(return_value={"text": verdicts_json}),
    ):
        verdicts = await _judge_homonym_batch(
            [_pair(), _pair(3, 4)], {1: ("a", ""), 2: ("b", ""), 3: ("c", ""), 4: ("d", "")},
            model="haiku",
        )
    assert len(verdicts) == 2
    assert verdicts[0]["relation"] == "synonym"


@pytest.mark.asyncio
async def test_judge_strips_code_fences():
    fenced = "```json\n[{\"pair_index\": 0, \"relation\": \"homonym\", \"reasoning\": \"r\"}]\n```"
    with patch(
        "app.services.llm_provider.llm_chat",
        new=AsyncMock(return_value={"text": fenced}),
    ):
        verdicts = await _judge_homonym_batch([_pair()], {1: ("a", ""), 2: ("b", "")}, "haiku")
    assert verdicts[0]["relation"] == "homonym"


@pytest.mark.asyncio
async def test_judge_garbage_returns_empty():
    with patch(
        "app.services.llm_provider.llm_chat",
        new=AsyncMock(return_value={"text": "I cannot help with that."}),
    ):
        verdicts = await _judge_homonym_batch([_pair()], {1: ("a", ""), 2: ("b", "")}, "haiku")
    assert verdicts == []
