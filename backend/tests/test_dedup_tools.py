"""Tool-level tests for the dedup agent (previously the only agent with no
dedicated tool coverage). Hermetic — fake session, mocked LLM/proposal layer.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

import app.agents.tools.dedup_tools as dt
from app.models import IntegrityFinding, Neuron


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeSession:
    def __init__(self, rows_by_id=None, execute_rows=None):
        self._rows_by_id = rows_by_id or {}
        self._execute_rows = execute_rows or []
        self.flush_calls = 0

    async def get(self, cls, id_):
        return self._rows_by_id.get((cls, int(id_)))

    async def execute(self, _stmt):
        return _FakeResult(self._execute_rows)

    async def flush(self):
        self.flush_calls += 1

    def add(self, obj):
        pass


def _neuron(nid, embedding=None, **kw):
    defaults = dict(label=f"Neuron {nid}", content="content", summary="summary",
                    department="Engineering", role_key="materials_engineer",
                    layer=3, source_origin="seed", authority_level=None,
                    invocations=1, is_active=True, node_type="knowledge")
    defaults.update(kw)
    return Neuron(id=nid, embedding=json.dumps(embedding) if embedding else None, **defaults)


def _finding(fid=1, neuron_ids=(10, 11), status="open"):
    return IntegrityFinding(
        id=fid, finding_type="near_duplicate", status=status, severity="medium",
        neuron_ids_json=json.dumps(list(neuron_ids)), description="possible dup",
        priority_score=0.5,
    )


# ── list / detail ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_pending_duplicates_shapes_rows():
    sess = _FakeSession(execute_rows=[_finding(1), _finding(2, neuron_ids=(3, 4))])
    out = await dt.list_pending_duplicates(sess, {"limit": 5})
    assert out["count"] == 2
    assert out["findings"][0]["neuron_ids"] == [10, 11]
    assert "severity" in out["findings"][0]


@pytest.mark.asyncio
async def test_list_pending_duplicates_rejects_bad_limit():
    with pytest.raises(AssertionError, match="limit must be 1..100"):
        await dt.list_pending_duplicates(_FakeSession(), {"limit": 0})


@pytest.mark.asyncio
async def test_get_finding_detail_returns_both_neurons():
    sess = _FakeSession(rows_by_id={
        (IntegrityFinding, 1): _finding(1),
        (Neuron, 10): _neuron(10),
        (Neuron, 11): _neuron(11),
    })
    out = await dt.get_finding_detail(sess, {"finding_id": 1})
    assert [n["id"] for n in out["neurons"]] == [10, 11]
    assert out["severity"] == "medium"


@pytest.mark.asyncio
async def test_get_finding_detail_rejects_wrong_type():
    f = _finding(1)
    f.finding_type = "contradiction"
    sess = _FakeSession(rows_by_id={(IntegrityFinding, 1): f})
    with pytest.raises(ValueError, match="not near_duplicate"):
        await dt.get_finding_detail(sess, {"finding_id": 1})


# ── embedding gate: thresholds must match the prompt exactly ─────────────

@pytest.mark.asyncio
async def test_embedding_zones_match_prompt_thresholds():
    async def zone(sim_vecs):
        a, b = sim_vecs
        sess = _FakeSession(rows_by_id={
            (Neuron, 10): _neuron(10, embedding=a),
            (Neuron, 11): _neuron(11, embedding=b),
        })
        return await dt.compute_embedding_similarity(
            sess, {"neuron_a_id": 10, "neuron_b_id": 11})

    identical = await zone(([1.0, 0.0], [1.0, 0.0]))          # sim = 1.0
    assert identical["decision_zone"] == "high_confidence_duplicate"
    orthogonal = await zone(([1.0, 0.0], [0.0, 1.0]))          # sim = 0.0
    assert orthogonal["decision_zone"] == "high_confidence_distinct"
    borderline = await zone(([1.0, 0.0], [0.9, 0.436]))        # sim ~= 0.90
    assert borderline["decision_zone"] == "borderline"


@pytest.mark.asyncio
async def test_embedding_unavailable_zone_when_missing():
    sess = _FakeSession(rows_by_id={
        (Neuron, 10): _neuron(10, embedding=None),
        (Neuron, 11): _neuron(11, embedding=[1.0, 0.0]),
    })
    out = await dt.compute_embedding_similarity(sess, {"neuron_a_id": 10, "neuron_b_id": 11})
    assert out["decision_zone"] == "unavailable"
    assert out["similarity"] is None


# ── semantic compare: approved LLM path + failure fallback ───────────────

@pytest.mark.asyncio
async def test_compare_neurons_semantic_parses_and_clamps():
    sess = _FakeSession(rows_by_id={
        (Neuron, 10): _neuron(10), (Neuron, 11): _neuron(11),
    })
    with patch.object(dt, "llm_chat", AsyncMock(return_value={
        "text": '{"classification": "duplicate", "rationale": "same requirement"}',
        "cost_usd": 0.01,
    })) as llm:
        out = await dt.compare_neurons_semantic(sess, {"neuron_a_id": 10, "neuron_b_id": 11})
    assert out["classification"] == "duplicate"
    assert llm.call_args.kwargs["model"] == "opus", "graph-mutation judgment runs on opus"


@pytest.mark.asyncio
async def test_compare_neurons_semantic_garbage_falls_back_to_needs_human():
    sess = _FakeSession(rows_by_id={
        (Neuron, 10): _neuron(10), (Neuron, 11): _neuron(11),
    })
    with patch.object(dt, "llm_chat", AsyncMock(return_value={"text": "not json at all", "cost_usd": 0.0})):
        out = await dt.compare_neurons_semantic(sess, {"neuron_a_id": 10, "neuron_b_id": 11})
    assert out["classification"] == "needs_human"


# ── mutating tools route through the human-approval proposal flow ────────

@pytest.mark.asyncio
async def test_mark_duplicate_routes_through_proposal():
    fake_proposal = type("P", (), {"id": 77, "state": "proposed"})()
    with patch("app.services.integrity.proposals.create_integrity_proposal",
               AsyncMock(return_value=fake_proposal)) as create:
        out = await dt.mark_duplicate(_FakeSession(), {
            "finding_id": 1, "notes": "embedding 0.97 + same scope",
        })
    assert out == {"finding_id": 1, "proposal_id": 77, "state": "proposed"}
    assert create.call_args.kwargs["resolution"] == "merged"
    assert create.call_args.kwargs["reviewer"] == "agent:dedup"


@pytest.mark.asyncio
async def test_mark_reviewed_dismissed_closes_finding_directly():
    f = _finding(1)
    sess = _FakeSession(rows_by_id={(IntegrityFinding, 1): f})
    out = await dt.mark_reviewed_as_unique(sess, {
        "finding_id": 1, "resolution": "dismissed", "notes": "scan misfire — unrelated topics",
    })
    assert out["status"] == "resolved"
    assert f.status == "resolved"
    assert f.resolution == "dismissed"
