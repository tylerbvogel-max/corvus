"""Unit tests for the integrity_reconciler agent tools (Phase 4 #204).

Hermetic — uses a minimal fake async session stand-in (supports .get,
.execute, .flush) so the tool functions can exercise their validation
and control-flow paths without a real DB or LLM. The full-loop agent
run against Postgres + Claude CLI is covered by the manual verification
fixture at `backend/scripts/integrity_reconciler_fixture.py`.

Matches the "test the pure bits; defer integration to manual script"
convention established by test_agents_runtime.py (line 3).
"""
from __future__ import annotations

import datetime
import json
import os
from typing import Any

os.environ.setdefault("TENANT_ID", "corvus-mind")

import pytest

from app.agents.tools.integrity_reconciler_tools import (
    _VALID_RESOLUTIONS,
    dismiss_contradiction,
    get_contradiction_detail,
    list_pending_contradictions,
    propose_contradiction_resolution,
)
from app.models import IntegrityFinding, Neuron


# ── Fake async session ──────────────────────────────────────────────────

class _FakeScalars:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def all(self) -> list:
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeExecuteResult:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._rows)


class _FakeSession:
    """Minimal async-session stand-in for tool-function tests.

    - .get(cls, id_): returns whatever was pre-registered in `rows_by_id`.
    - .execute(stmt): pops the next queued row-list (tests pre-seed the
      expected SELECT result in order).
    - .flush(): no-op; records that it was called for assertions.
    """

    def __init__(self, rows_by_id: dict | None = None, execute_queue: list | None = None) -> None:
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

def _make_finding(
    finding_id: int = 1,
    finding_type: str = "contradiction",
    status: str = "open",
    neuron_ids: list[int] | None = None,
    priority: float = 0.8,
    severity: str = "warning",
    description: str = "neurons disagree about X",
) -> IntegrityFinding:
    f = IntegrityFinding(
        id=finding_id,
        scan_id=1,
        finding_type=finding_type,
        severity=severity,
        priority_score=priority,
        description=description,
        detail_json=None,
        neuron_ids_json=json.dumps(neuron_ids or [10, 20]),
        status=status,
    )
    return f


def _make_neuron(id_: int = 10, label: str = "Neuron") -> Neuron:
    return Neuron(
        id=id_,
        label=label,
        layer=3,
        department="ops",
        role_key="manager",
        summary=f"summary-{id_}",
        content=f"content for neuron {id_}",
        source_origin="manual",
        authority_level=2,
        invocations=5,
        avg_utility=0.7,
        is_active=True,
        created_at=datetime.datetime(2026, 1, 1),
        last_verified=datetime.datetime(2026, 4, 1),
    )


# ── list_pending_contradictions ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_pending_contradictions_returns_queued_rows():
    rows = [
        _make_finding(1, priority=0.9),
        _make_finding(2, priority=0.5),
    ]
    sess = _FakeSession(execute_queue=[rows])
    out = await list_pending_contradictions(
        sess, {"limit": 5, "rationale": "checking what work is pending this tick"},
    )
    assert out["count"] == 2
    assert [f["id"] for f in out["findings"]] == [1, 2]
    assert out["findings"][0]["priority_score"] == 0.9


@pytest.mark.asyncio
async def test_list_pending_contradictions_rejects_missing_rationale():
    sess = _FakeSession(execute_queue=[[]])
    with pytest.raises(AssertionError, match="rationale is required"):
        await list_pending_contradictions(sess, {"limit": 5})


@pytest.mark.asyncio
async def test_list_pending_contradictions_rejects_invalid_limit():
    sess = _FakeSession(execute_queue=[[]])
    with pytest.raises(AssertionError, match="limit must be 1..20"):
        await list_pending_contradictions(
            sess, {"limit": 999, "rationale": "a rationale long enough to pass schema"},
        )


# ── get_contradiction_detail ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_contradiction_detail_returns_both_neurons_with_signals():
    finding = _make_finding(1, neuron_ids=[10, 20])
    n_a = _make_neuron(10, label="Policy A")
    n_b = _make_neuron(20, label="Policy B")
    sess = _FakeSession(rows_by_id={
        (IntegrityFinding, 1): finding,
        (Neuron, 10): n_a,
        (Neuron, 20): n_b,
    })
    out = await get_contradiction_detail(
        sess, {"finding_id": 1, "rationale": "inspecting the neurons behind finding 1"},
    )
    assert out["finding_id"] == 1
    assert len(out["neurons"]) == 2
    first = out["neurons"][0]
    # Regulatory + recency + credibility signals surface in the return.
    assert "authority_level" in first
    assert "source_origin" in first
    assert "last_verified" in first
    assert "invocations" in first
    assert "avg_utility" in first


@pytest.mark.asyncio
async def test_get_contradiction_detail_rejects_wrong_finding_type():
    finding = _make_finding(1, finding_type="near_duplicate", neuron_ids=[10, 20])
    sess = _FakeSession(rows_by_id={(IntegrityFinding, 1): finding})
    with pytest.raises(ValueError, match="not contradiction"):
        await get_contradiction_detail(
            sess, {"finding_id": 1, "rationale": "defensive-type check for finding 1"},
        )


@pytest.mark.asyncio
async def test_get_contradiction_detail_404s_when_missing():
    sess = _FakeSession()
    with pytest.raises(KeyError, match="not found"):
        await get_contradiction_detail(
            sess, {"finding_id": 999, "rationale": "should surface the missing-finding case"},
        )


# ── propose_contradiction_resolution ────────────────────────────────────

@pytest.mark.asyncio
async def test_propose_contradiction_resolution_rejects_invalid_resolution():
    sess = _FakeSession()
    with pytest.raises(AssertionError, match="resolution must be one of"):
        await propose_contradiction_resolution(sess, {
            "finding_id": 1, "resolution": "merged",  # merged is dedup-only
            "notes": "x", "rationale": "testing that merged is not allowed here",
        })


@pytest.mark.asyncio
async def test_propose_contradiction_resolution_rejects_wrong_finding_type():
    finding = _make_finding(1, finding_type="near_duplicate", neuron_ids=[10, 20])
    sess = _FakeSession(rows_by_id={(IntegrityFinding, 1): finding})
    with pytest.raises(ValueError, match="not contradiction"):
        await propose_contradiction_resolution(sess, {
            "finding_id": 1, "resolution": "a_correct",
            "notes": "x", "rationale": "defensive type check blocking non-contradiction proposals",
        })


@pytest.mark.asyncio
async def test_all_valid_resolution_values_pass_enum_check():
    # Just checks the enum membership logic — doesn't DB-call create_integrity_proposal.
    assert "a_correct" in _VALID_RESOLUTIONS
    assert "b_correct" in _VALID_RESOLUTIONS
    assert "context_added" in _VALID_RESOLUTIONS
    assert "dismissed" not in _VALID_RESOLUTIONS  # dismissed uses dismiss_contradiction
    assert "merged" not in _VALID_RESOLUTIONS     # merged is dedup-only


# ── dismiss_contradiction ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dismiss_contradiction_closes_open_finding():
    finding = _make_finding(5, status="open")
    sess = _FakeSession(rows_by_id={(IntegrityFinding, 5): finding})
    out = await dismiss_contradiction(sess, {
        "finding_id": 5, "notes": "neurons cover separate materials",
        "rationale": "scan misfired — neurons are about different alloys",
    })
    assert out["status"] == "resolved"
    assert out["resolution"] == "dismissed"
    assert finding.status == "resolved"
    assert finding.resolution == "dismissed"
    assert finding.resolved_by == "agent:integrity_reconciler"
    assert sess.flush_calls == 1


@pytest.mark.asyncio
async def test_dismiss_contradiction_refuses_already_resolved():
    finding = _make_finding(5, status="resolved")
    sess = _FakeSession(rows_by_id={(IntegrityFinding, 5): finding})
    with pytest.raises(ValueError, match="cannot close"):
        await dismiss_contradiction(sess, {
            "finding_id": 5, "notes": "x",
            "rationale": "trying to dismiss something already-resolved should fail",
        })


@pytest.mark.asyncio
async def test_dismiss_contradiction_rejects_wrong_finding_type():
    finding = _make_finding(5, finding_type="near_duplicate", status="open")
    sess = _FakeSession(rows_by_id={(IntegrityFinding, 5): finding})
    with pytest.raises(ValueError, match="not contradiction"):
        await dismiss_contradiction(sess, {
            "finding_id": 5, "notes": "x",
            "rationale": "dismiss must refuse to touch a near_duplicate finding",
        })


# ── Agent allow-list sanity ─────────────────────────────────────────────

def test_integrity_reconciler_agent_has_no_neuron_write_tool():
    """Acceptance criterion 3: no autonomous write. The agent's YAML must
    never expose a neuron/edge mutation tool — the allow-list is the
    structural enforcement of this invariant."""
    # Direct YAML read to avoid depending on the global AgentRegistry
    # being loaded at import time.
    import yaml
    from pathlib import Path
    yaml_path = (
        Path(__file__).parent.parent
        / "app" / "agents" / "definitions" / "integrity_reconciler.yaml"
    )
    with yaml_path.open() as fh:
        spec = yaml.safe_load(fh)
    allow = spec["tool_allow_list"]
    assert allow == [
        "list_pending_contradictions",
        "get_contradiction_detail",
        "propose_contradiction_resolution",
        "dismiss_contradiction",
    ]
    # Paranoia check — none of these substrings should ever appear here.
    forbidden_patterns = ("write_neuron", "update_neuron", "create_neuron", "merge_neuron")
    for name in allow:
        for pattern in forbidden_patterns:
            assert pattern not in name, f"{name!r} matches forbidden pattern {pattern!r}"


# ── Evidence schema completeness (polish: gov-polish-integrity-evidence-schema)

def test_proposal_evidence_satisfies_gap_evidence_schema():
    """Evidence written for integrity findings must validate as GapEvidenceOut
    at write time — no more falling back to a plain dict in proposal detail."""
    import json as _json

    from app.models import IntegrityFinding
    from app.schemas import GapEvidenceOut
    from app.services.integrity.proposals import _create_proposal_record

    finding = IntegrityFinding(
        id=7, finding_type="near_duplicate", status="open", severity="medium",
        neuron_ids_json=_json.dumps([10, 11]), description="possible dup",
        priority_score=0.5, scan_id=3,
        detail_json=_json.dumps({"cosine_similarity": 0.9612}),
    )
    proposal = _create_proposal_record(finding, "merged", "agent:dedup", "same scope")
    evidence = _json.loads(proposal.gap_evidence_json)[0]
    out = GapEvidenceOut(**{k: v for k, v in evidence.items()
                            if k in GapEvidenceOut.model_fields})
    assert out.description == "possible dup"
    assert out.metric_value == 0.9612
    assert out.threshold == 0.92
    assert out.neuron_ids == [10, 11]


def test_proposal_evidence_fallback_metric_for_other_types():
    import json as _json

    from app.models import IntegrityFinding
    from app.services.integrity.proposals import _create_proposal_record

    finding = IntegrityFinding(
        id=8, finding_type="contradiction", status="open", severity="high",
        neuron_ids_json=_json.dumps([1, 2]), description="conflicting guidance",
        priority_score=0.8, scan_id=3, detail_json=None,
    )
    evidence = _json.loads(
        _create_proposal_record(finding, "context_added", "agent:integrity_reconciler", "").gap_evidence_json
    )[0]
    assert evidence["metric_value"] == 0.8
    assert evidence["threshold"] == 0.0
