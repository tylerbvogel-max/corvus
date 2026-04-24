"""Unit tests for the neuron_placer agent tools (renamed from document_ingest_reviewer).

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
    get_placement_status,
    list_pending_ingest_proposals,
    refine_ingest_classification,
    search_graph_parents,
)
from app.models import AutopilotProposal, Neuron, ProposalItem


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

    def all(self) -> list:
        """Real SQLAlchemy Result.all() returns a list of Row tuples — the
        caller iterates them. We just return the pre-seeded list as-is."""
        return list(self._rows)

    def scalar(self) -> Any:
        """Real SQLAlchemy Result.scalar() returns the first column of the
        first row, or None."""
        if not self._rows:
            return None
        first = self._rows[0]
        return first[0] if isinstance(first, tuple) else first


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
        _make_proposal(1, state="artifact"),
        _make_proposal(2, state="artifact"),
    ]
    sess = _FakeSession(execute_queue=[rows])
    out = await list_pending_ingest_proposals(
        sess, {"limit": 5, "rationale": "scanning for artifacts awaiting graph placement"},
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
async def test_refine_places_artifact_and_promotes_state():
    # Artifact-shape spec (Phase 1 output): content fields present, placement fields absent.
    initial_spec = {
        "node_type": "standard",
        "label": "Travel reimbursement policy",
        "content": "Travel is reimbursed per GSA rates.",
        "summary": "Travel reimbursement standard.",
    }
    item = _make_item(10, initial_spec)
    proposal = _make_proposal(1, state="artifact", items=[item])

    # Layer 1 validation queries: (1) parent neurons by id, (2) dept/role pairs.
    parent_neuron = Neuron(id=42, label="Expenses", layer=2, department="Finance",
                           role_key="policy_author", node_type="knowledge", is_active=True)
    sess = _FakeSession(
        rows_by_id={(AutopilotProposal, 1): proposal},
        execute_queue=[
            [parent_neuron],                         # _fetch_valid_parents
            [("Finance", "policy_author")],          # _fetch_known_dept_roles (rows iter)
        ],
    )

    out = await refine_ingest_classification(sess, {
        "proposal_id": 1,
        "item_updates": [
            {"item_id": 10, "parent_id": 42, "layer": 3,
             "department": "Finance", "role_key": "policy_author",
             "rationale": "Finance owns travel policy under parent #42 (Expenses)"},
        ],
        "confidence": 0.92,
        "rationale": "Content describes travel reimbursement — Finance/policy_author fits under Expenses parent",
    })

    # Item spec gains the committed placement fields.
    new_spec = json.loads(item.neuron_spec_json)
    assert new_spec["parent_id"] == 42
    assert new_spec["department"] == "Finance"
    assert new_spec["role_key"] == "policy_author"
    assert new_spec["layer"] == 3
    # Original content fields preserved.
    assert new_spec["label"] == "Travel reimbursement policy"
    assert new_spec["node_type"] == "standard"
    # Item's target_neuron_id mirrors the chosen parent.
    assert item.target_neuron_id == 42
    # Placement rationale recorded on the item's reason.
    assert "neuron_placer" in (item.reason or "")
    assert "Finance" in (item.reason or "") or "Expenses" in (item.reason or "")

    # Proposal promoted artifact → proposed, reviewed by neuron_placer.
    assert proposal.state == "proposed"
    assert proposal.reviewed_by == "agent:neuron_placer"
    assert proposal.reviewed_at is not None
    assert "neuron_placer" in (proposal.llm_reasoning or "")
    assert out["updated_items"] == 1
    assert out["confidence"] == 0.92
    assert out["promoted"] is True
    assert out["state"] == "proposed"
    assert sess.flush_calls == 1


@pytest.mark.asyncio
async def test_refine_rejects_wrong_gap_source():
    proposal = _make_proposal(1, gap_source="emergent_queue")
    sess = _FakeSession(rows_by_id={(AutopilotProposal, 1): proposal})
    with pytest.raises(ValueError, match="not document_ingest"):
        await refine_ingest_classification(sess, {
            "proposal_id": 1,
            "item_updates": [{"item_id": 1, "parent_id": 5, "layer": 2,
                              "department": "Ops", "role_key": "manager",
                              "rationale": "attempting placement on a wrong-source proposal"}],
            "confidence": 0.9,
            "rationale": "attempting to refine a non-ingest proposal must fail",
        })


@pytest.mark.asyncio
async def test_refine_rejects_out_of_range_confidence():
    sess = _FakeSession()
    with pytest.raises(AssertionError, match="confidence must be 0..1"):
        await refine_ingest_classification(sess, {
            "proposal_id": 1,
            "item_updates": [{"item_id": 1, "parent_id": 1, "layer": 1,
                              "department": "x", "role_key": "y",
                              "rationale": "confidence validation test"}],
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
    assert proposal.reviewed_by == "agent:neuron_placer"
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


# ── Layer 1: refine validates placement fields against reality ─────────


@pytest.mark.asyncio
async def test_refine_rejects_nonexistent_parent_id():
    item = _make_item(10, {"node_type": "standard", "label": "X"})
    proposal = _make_proposal(1, state="artifact", items=[item])
    # _fetch_valid_parents returns nothing → validation raises ValueError.
    sess = _FakeSession(
        rows_by_id={(AutopilotProposal, 1): proposal},
        execute_queue=[
            [],                               # _fetch_valid_parents → no row for 99999
            [("Engineering", "materials")],   # _fetch_known_dept_roles
        ],
    )
    with pytest.raises(ValueError, match="Unknown or inactive parent_id"):
        await refine_ingest_classification(sess, {
            "proposal_id": 1,
            "item_updates": [{
                "item_id": 10, "parent_id": 99999, "layer": 3,
                "department": "Engineering", "role_key": "materials",
                "rationale": "attempting placement under a made-up parent id",
            }],
            "confidence": 0.8,
            "rationale": "unknown parent validation — must raise ValueError",
        })


@pytest.mark.asyncio
async def test_refine_rejects_unknown_department():
    item = _make_item(10, {"node_type": "standard", "label": "X"})
    proposal = _make_proposal(1, state="artifact", items=[item])
    parent = Neuron(id=42, label="P", layer=2, department="Engineering",
                    role_key="materials", node_type="knowledge", is_active=True)
    sess = _FakeSession(
        rows_by_id={(AutopilotProposal, 1): proposal},
        execute_queue=[
            [parent],                         # valid parent
            [("Engineering", "materials")],   # dept_roles — Marketing not here
        ],
    )
    with pytest.raises(ValueError, match="Unknown department"):
        await refine_ingest_classification(sess, {
            "proposal_id": 1,
            "item_updates": [{
                "item_id": 10, "parent_id": 42, "layer": 3,
                "department": "Marketing", "role_key": "materials",
                "rationale": "invented department should be rejected",
            }],
            "confidence": 0.8,
            "rationale": "unknown department validation — must raise ValueError",
        })


@pytest.mark.asyncio
async def test_refine_rejects_layer_node_type_mismatch():
    """A 'metric' node_type only fits layers 4-5; layer 1 should raise."""
    item = _make_item(10, {"node_type": "standard", "label": "X"})
    proposal = _make_proposal(1, state="artifact", items=[item])
    # parent.node_type='metric' is what validator checks — test that layer=1
    # trips the compat check.
    parent = Neuron(id=42, label="Metric parent", layer=4, department="Engineering",
                    role_key="materials", node_type="metric", is_active=True)
    sess = _FakeSession(
        rows_by_id={(AutopilotProposal, 1): proposal},
        execute_queue=[
            [parent],
            [("Engineering", "materials")],
        ],
    )
    with pytest.raises(ValueError, match="not compatible with node_type"):
        await refine_ingest_classification(sess, {
            "proposal_id": 1,
            "item_updates": [{
                "item_id": 10, "parent_id": 42, "layer": 1,
                "department": "Engineering", "role_key": "materials",
                "rationale": "metric at layer 1 is a compatibility violation",
            }],
            "confidence": 0.8,
            "rationale": "layer-vs-node_type compat check — must raise ValueError",
        })


# ── Layer 2: get_placement_status read-back ────────────────────────────


@pytest.mark.asyncio
async def test_get_placement_status_returns_committed_fields():
    placed_spec = {
        "parent_id": 42, "layer": 3, "department": "Engineering",
        "role_key": "materials", "label": "Minimum thickness",
    }
    item = _make_item(10, placed_spec)
    proposal = _make_proposal(1, state="proposed", items=[item])
    proposal.reviewed_by = "agent:neuron_placer"
    proposal.reviewed_at = datetime.datetime(2026, 4, 23, 16, 0, 0)
    parent = Neuron(id=42, label="Materials & Structures", layer=2,
                    department="Engineering", role_key="materials",
                    node_type="knowledge", is_active=True)
    sess = _FakeSession(rows_by_id={
        (AutopilotProposal, 1): proposal,
        (Neuron, 42): parent,
    })
    out = await get_placement_status(sess, {
        "proposal_id": 1,
        "rationale": "read-back after refine to confirm placement stuck",
    })
    assert out["proposal_id"] == 1
    assert out["state"] == "proposed"
    assert out["placement"]["parent_id"] == 42
    assert out["placement"]["parent_label"] == "Materials & Structures"
    assert out["placement"]["layer"] == 3
    assert out["placement"]["department"] == "Engineering"
    assert out["reviewed_by"] == "agent:neuron_placer"


@pytest.mark.asyncio
async def test_get_placement_status_returns_null_placement_for_unplaced():
    """An artifact-state row (no placement fields) reports placement=None."""
    item = _make_item(10, {"node_type": "standard", "label": "X",
                           "content": "c", "summary": "s"})
    proposal = _make_proposal(1, state="artifact", items=[item])
    sess = _FakeSession(rows_by_id={(AutopilotProposal, 1): proposal})
    out = await get_placement_status(sess, {
        "proposal_id": 1,
        "rationale": "reading back an artifact that hasn't been placed yet",
    })
    assert out["state"] == "artifact"
    assert out["placement"] is None


# ── search_graph_parents ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_graph_parents_empty_returns_zero_candidates():
    """With no matching rows, the tool returns an empty candidate list
    without attempting sibling-count sub-queries."""
    sess = _FakeSession(execute_queue=[[]])
    out = await search_graph_parents(sess, {
        "label_pattern": "nonexistent_topic_xyz",
        "max_results": 5,
        "rationale": "search returning zero candidates — hermetic test",
    })
    assert out["count"] == 0
    assert out["candidates"] == []


@pytest.mark.asyncio
async def test_search_graph_parents_rejects_missing_rationale():
    sess = _FakeSession(execute_queue=[[]])
    with pytest.raises(AssertionError, match="rationale is required"):
        await search_graph_parents(sess, {"label_pattern": "heat%"})


@pytest.mark.asyncio
async def test_search_graph_parents_caps_max_results():
    sess = _FakeSession(execute_queue=[[]])
    with pytest.raises(AssertionError, match="max_results must be 1..20"):
        await search_graph_parents(sess, {
            "max_results": 50,
            "rationale": "out-of-range max_results should be rejected",
        })


# ── Agent allow-list sanity ────────────────────────────────────────────


def test_neuron_placer_agent_has_no_neuron_write_tool():
    """Acceptance parallel to #204: no autonomous write. The agent's YAML
    must never expose a neuron/edge mutation tool — the allow-list is the
    structural enforcement of this invariant."""
    import yaml
    from pathlib import Path
    yaml_path = (
        Path(__file__).parent.parent
        / "app" / "agents" / "definitions" / "neuron_placer.yaml"
    )
    with yaml_path.open() as fh:
        spec = yaml.safe_load(fh)
    assert spec["name"] == "neuron_placer"
    allow = spec["tool_allow_list"]
    # list_pending_ingest_proposals intentionally dropped — neuron_placer
    # works on one artifact_id per run (passed via input_context) and never
    # enumerates. get_placement_status added as mandatory post-mutation
    # verification read-back.
    assert set(allow) == {
        "get_ingest_proposal_detail",
        "search_graph_parents",
        "refine_ingest_classification",
        "flag_ingest_uncertain",
        "get_placement_status",
    }
    forbidden_patterns = ("write_neuron", "update_neuron", "create_neuron", "merge_neuron", "delete_neuron")
    for name in allow:
        for pattern in forbidden_patterns:
            assert pattern not in name, f"{name!r} matches forbidden pattern {pattern!r}"
