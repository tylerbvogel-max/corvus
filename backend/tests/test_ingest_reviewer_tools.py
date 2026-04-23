"""Unit tests for the document_ingest_reviewer agent tools (Phase 4 #205).

Hermetic — same minimal fake async session shape used by the dedup (#202)
and integrity reconciler (#204) tool tests. No Postgres, no LLM. The
full-loop agent run against a real DB + Claude CLI lives in a separate
manual fixture (backend/scripts/ingest_reviewer_fixture.py, logged in
the design doc) because it's the 20-doc acceptance check, not pytest.

See docs/design/aip-phase-4-node-205-ingest-agent.md for the design.
"""
from __future__ import annotations

import datetime
import json
import os
from typing import Any

os.environ.setdefault("TENANT_ID", "corvus-aero")

import pytest

from app.agents.tools.ingest_reviewer_tools import (
    flag_ingest_uncertain,
    get_ingest_proposal_detail,
    list_pending_ingest_proposals,
    refine_ingest_classification,
)
from app.models import AutopilotProposal, ProposalItem


# ── Fake async session (same shape as test_integrity_reconciler_tools.py) ──


class _FakeScalars:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return list(self._rows)


class _FakeExecuteResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)


class _FakeSession:
    def __init__(
        self,
        rows_by_id: dict | None = None,
        execute_queue: list | None = None,
    ) -> None:
        self._rows_by_id = rows_by_id or {}
        self._execute_queue = list(execute_queue or [])
        self.flush_calls = 0

    async def get(self, cls: Any, id_: int) -> Any:
        return self._rows_by_id.get((cls, int(id_)))

    async def execute(self, _stmt: Any) -> _FakeExecuteResult:
        assert self._execute_queue, "test forgot to seed an execute result"
        return _FakeExecuteResult(self._execute_queue.pop(0))

    async def flush(self) -> None:
        self.flush_calls += 1


# ── Factories ──────────────────────────────────────────────────────────

def _make_proposal(
    proposal_id: int = 1,
    gap_source: str = "document_ingest",
    state: str = "proposed",
    reviewed_at: datetime.datetime | None = None,
    items: list[ProposalItem] | None = None,
    llm_reasoning: str | None = None,
    gap_evidence_json: str | None = None,
) -> AutopilotProposal:
    p = AutopilotProposal(
        id=proposal_id,
        state=state,
        gap_source=gap_source,
        gap_description="Section extracted from upload",
        priority_score=0.5,
        llm_reasoning=llm_reasoning,
        gap_evidence_json=gap_evidence_json,
    )
    # Set reviewed_at separately (not in constructor since it may be None).
    p.reviewed_at = reviewed_at
    # Attach items list directly (SQLAlchemy relationship mock).
    p.items = items or []  # type: ignore[assignment]
    p.created_at = datetime.datetime(2026, 4, 23, 10, 0, 0)
    return p


def _make_item(
    item_id: int,
    spec: dict[str, Any],
    action: str = "create",
) -> ProposalItem:
    return ProposalItem(
        id=item_id,
        proposal_id=1,
        action=action,
        neuron_spec_json=json.dumps(spec),
    )


# ── list_pending_ingest_proposals ──────────────────────────────────────

@pytest.mark.asyncio
async def test_list_pending_returns_queued_rows():
    rows = [
        _make_proposal(1),
        _make_proposal(2),
    ]
    sess = _FakeSession(execute_queue=[rows])
    out = await list_pending_ingest_proposals(
        sess, {"limit": 5, "rationale": "scanning for ingest proposals to review"},
    )
    assert out["count"] == 2
    assert [p["id"] for p in out["proposals"]] == [1, 2]


@pytest.mark.asyncio
async def test_list_pending_rejects_missing_rationale():
    sess = _FakeSession(execute_queue=[[]])
    with pytest.raises(AssertionError, match="rationale is required"):
        await list_pending_ingest_proposals(sess, {"limit": 5})


@pytest.mark.asyncio
async def test_list_pending_rejects_invalid_limit():
    sess = _FakeSession(execute_queue=[[]])
    with pytest.raises(AssertionError, match="limit must be 1..20"):
        await list_pending_ingest_proposals(
            sess, {"limit": 999, "rationale": "a rationale long enough to pass schema"},
        )


# ── get_ingest_proposal_detail ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_detail_returns_items_and_empty_ontology_sample():
    spec = {
        "parent_id": 42, "layer": 3, "node_type": "knowledge",
        "label": "Travel reimbursement policy",
        "department": "Finance", "role_key": "policy_author",
    }
    proposal = _make_proposal(1, items=[_make_item(10, spec)])
    sess = _FakeSession(
        rows_by_id={(AutopilotProposal, 1): proposal},
        execute_queue=[[]],  # ontology sibling query returns empty
    )
    out = await get_ingest_proposal_detail(
        sess, {"proposal_id": 1, "rationale": "inspecting the proposal's specs and ontology"},
    )
    assert out["proposal_id"] == 1
    assert len(out["items"]) == 1
    assert out["items"][0]["spec"]["layer"] == 3
    assert out["ontology_sample"] == []


@pytest.mark.asyncio
async def test_get_detail_rejects_wrong_gap_source():
    proposal = _make_proposal(1, gap_source="emergent_queue")
    sess = _FakeSession(rows_by_id={(AutopilotProposal, 1): proposal})
    with pytest.raises(ValueError, match="not document_ingest"):
        await get_ingest_proposal_detail(
            sess, {"proposal_id": 1, "rationale": "defensive gap_source check on the loaded proposal"},
        )


@pytest.mark.asyncio
async def test_get_detail_404s_when_missing():
    sess = _FakeSession()
    with pytest.raises(KeyError, match="not found"):
        await get_ingest_proposal_detail(
            sess, {"proposal_id": 999, "rationale": "should surface the missing-proposal case"},
        )


@pytest.mark.asyncio
async def test_get_detail_accepts_uncertain_gap_source():
    """flag_ingest_uncertain converts gap_source to 'document_ingest/uncertain'.
    Re-reading the detail afterwards must still succeed."""
    proposal = _make_proposal(1, gap_source="document_ingest/uncertain")
    sess = _FakeSession(
        rows_by_id={(AutopilotProposal, 1): proposal},
        execute_queue=[[]],
    )
    out = await get_ingest_proposal_detail(
        sess, {"proposal_id": 1, "rationale": "re-reading a previously-flagged uncertain proposal"},
    )
    assert out["gap_source"] == "document_ingest/uncertain"


# ── refine_ingest_classification ───────────────────────────────────────

@pytest.mark.asyncio
async def test_refine_updates_item_spec_and_marks_reviewed():
    initial_spec = {
        "parent_id": 42, "layer": 3, "node_type": "knowledge",
        "label": "Travel reimbursement policy",
        "department": "Engineering", "role_key": "engineer",  # wrong!
    }
    item = _make_item(10, initial_spec)
    proposal = _make_proposal(1, items=[item])
    sess = _FakeSession(rows_by_id={(AutopilotProposal, 1): proposal})

    out = await refine_ingest_classification(sess, {
        "proposal_id": 1,
        "item_updates": [
            {"item_id": 10, "layer": 3, "department": "Finance", "role_key": "policy_author"},
        ],
        "confidence": 0.92,
        "rationale": "Content clearly describes travel reimbursement — Finance/policy_author fits better than Engineering/engineer",
    })

    # Item spec was rewritten with the correct department + role_key.
    new_spec = json.loads(item.neuron_spec_json)
    assert new_spec["department"] == "Finance"
    assert new_spec["role_key"] == "policy_author"
    # Label + parent preserved.
    assert new_spec["label"] == "Travel reimbursement policy"
    assert new_spec["parent_id"] == 42

    # Proposal marked reviewed by the agent; state unchanged.
    assert proposal.reviewed_by == "agent:document_ingest_reviewer"
    assert proposal.reviewed_at is not None
    assert proposal.state == "proposed"
    assert "document_ingest_reviewer" in (proposal.llm_reasoning or "")
    assert out["updated_items"] == 1
    assert out["confidence"] == 0.92
    assert sess.flush_calls == 1


@pytest.mark.asyncio
async def test_refine_rejects_wrong_gap_source():
    proposal = _make_proposal(1, gap_source="emergent_queue")
    sess = _FakeSession(rows_by_id={(AutopilotProposal, 1): proposal})
    with pytest.raises(ValueError, match="not document_ingest"):
        await refine_ingest_classification(sess, {
            "proposal_id": 1,
            "item_updates": [{"item_id": 1, "layer": 2, "department": "Ops", "role_key": "manager"}],
            "confidence": 0.9,
            "rationale": "attempting to refine a non-ingest proposal must fail",
        })


@pytest.mark.asyncio
async def test_refine_rejects_out_of_range_confidence():
    sess = _FakeSession()
    with pytest.raises(AssertionError, match="confidence must be 0..1"):
        await refine_ingest_classification(sess, {
            "proposal_id": 1,
            "item_updates": [{"item_id": 1, "layer": 1, "department": "x", "role_key": "y"}],
            "confidence": 1.5,
            "rationale": "out-of-range confidence should be rejected defensively",
        })


# ── flag_ingest_uncertain ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_flag_uncertain_flips_gap_source_and_stores_candidates():
    proposal = _make_proposal(1, items=[_make_item(10, {"layer": 3, "department": "X"})])
    sess = _FakeSession(rows_by_id={(AutopilotProposal, 1): proposal})

    out = await flag_ingest_uncertain(sess, {
        "proposal_id": 1,
        "candidates": [
            {"layer": 3, "department": "Procurement", "role_key": "buyer",
             "confidence": 0.55, "rationale": "content references contract award clauses"},
            {"layer": 2, "department": "Legal", "role_key": "counsel",
             "confidence": 0.40, "rationale": "content also cites FAR 52.219 compliance language"},
        ],
        "rationale": "Section is genuinely borderline between Procurement and Legal",
    })

    assert out["gap_source"] == "document_ingest/uncertain"
    assert out["candidate_count"] == 2
    assert proposal.gap_source == "document_ingest/uncertain"
    assert proposal.reviewed_by == "agent:document_ingest_reviewer"
    assert "FLAGGED UNCERTAIN" in (proposal.llm_reasoning or "")
    # Candidate block stored in gap_evidence_json.
    parsed = json.loads(proposal.gap_evidence_json)
    assert isinstance(parsed, list) and len(parsed) == 1
    block = parsed[0]
    assert block["uncertain"] is True
    assert len(block["candidates"]) == 2
    assert block["candidates"][0]["department"] == "Procurement"


@pytest.mark.asyncio
async def test_flag_uncertain_rejects_wrong_gap_source():
    proposal = _make_proposal(1, gap_source="emergent_queue")
    sess = _FakeSession(rows_by_id={(AutopilotProposal, 1): proposal})
    with pytest.raises(ValueError, match="not document_ingest"):
        await flag_ingest_uncertain(sess, {
            "proposal_id": 1,
            "candidates": [
                {"layer": 1, "department": "a", "role_key": "x", "confidence": 0.5, "rationale": "candidate one rationale here"},
                {"layer": 2, "department": "b", "role_key": "y", "confidence": 0.4, "rationale": "candidate two rationale here"},
            ],
            "rationale": "attempting to flag a non-ingest proposal must fail",
        })


@pytest.mark.asyncio
async def test_flag_uncertain_preserves_prior_evidence():
    prior_evidence = json.dumps([{"signal": "extractor_initial", "section_id": 7}])
    proposal = _make_proposal(1, gap_evidence_json=prior_evidence)
    sess = _FakeSession(rows_by_id={(AutopilotProposal, 1): proposal})

    await flag_ingest_uncertain(sess, {
        "proposal_id": 1,
        "candidates": [
            {"layer": 3, "department": "A", "role_key": "x", "confidence": 0.5, "rationale": "candidate one reason here"},
            {"layer": 3, "department": "B", "role_key": "y", "confidence": 0.4, "rationale": "candidate two reason here"},
        ],
        "rationale": "checking that prior evidence blocks remain intact after flagging",
    })

    parsed = json.loads(proposal.gap_evidence_json)
    assert isinstance(parsed, list) and len(parsed) == 2
    assert parsed[0]["signal"] == "extractor_initial"  # original preserved
    assert parsed[1]["signal"] == "agent_flagged_uncertain"


# ── Agent allow-list sanity ────────────────────────────────────────────

def test_ingest_reviewer_agent_has_no_neuron_write_tool():
    """Acceptance parallel to #204: no autonomous write. The agent's YAML
    must never expose a neuron/edge mutation tool — the allow-list is the
    structural enforcement of this invariant."""
    import yaml
    from pathlib import Path
    yaml_path = (
        Path(__file__).parent.parent
        / "app" / "agents" / "definitions" / "document_ingest_reviewer.yaml"
    )
    with yaml_path.open() as fh:
        spec = yaml.safe_load(fh)
    allow = spec["tool_allow_list"]
    assert allow == [
        "list_pending_ingest_proposals",
        "get_ingest_proposal_detail",
        "refine_ingest_classification",
        "flag_ingest_uncertain",
    ]
    forbidden_patterns = ("write_neuron", "update_neuron", "create_neuron", "merge_neuron", "delete_neuron")
    for name in allow:
        for pattern in forbidden_patterns:
            assert pattern not in name, f"{name!r} matches forbidden pattern {pattern!r}"
