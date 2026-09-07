"""Tiered write gate (plat-write-gate) — hermetic tests.

Covers: policy evaluation matrix (mode / authority ceiling / guardrails /
confidence), authority ranking, proposal write-class derivation, and the
auto route's approve+apply behavior with a faked apply service.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.write_gate import (
    GATE_ACTOR, WriteGatePolicy, authority_rank, evaluate_write,
    proposal_max_authority, route_proposal,
)


MANUAL = WriteGatePolicy(mode="manual")
TIERED = WriteGatePolicy(
    mode="tiered", auto_commit_max_authority="guidance",
    require_guardrails_pass=True, min_confidence=0.6,
)


# ── Authority ranking ────────────────────────────────────────────────

def test_authority_rank_ordering():
    assert authority_rank("binding_standard") > authority_rank("regulatory")
    assert authority_rank("regulatory") > authority_rank("organizational")
    assert authority_rank("organizational") > authority_rank("guidance")
    assert authority_rank("guidance") > authority_rank("informational")


def test_only_omitted_authority_is_observational():
    assert authority_rank(None) == authority_rank("informational")
    for invalid in ("", "made_up_level"):
        with pytest.raises(ValueError):
            authority_rank(invalid)


# ── Policy evaluation matrix ─────────────────────────────────────────

def test_manual_mode_queues_everything():
    for auth in (None, "informational", "binding_standard"):
        decision = evaluate_write(MANUAL, auth, True, 1.0)
        assert decision.route == "queue"
        assert "manual" in decision.reason


def test_tiered_informational_auto_commits():
    decision = evaluate_write(TIERED, "informational", True, 1.0)
    assert decision.route == "auto"


def test_tiered_unattributed_auto_commits():
    decision = evaluate_write(TIERED, None, None, 1.0)
    assert decision.route == "auto"


def test_tiered_regulatory_queues():
    decision = evaluate_write(TIERED, "regulatory", True, 1.0)
    assert decision.route == "queue"
    assert "authoritative" in decision.reason


def test_tiered_organizational_above_guidance_ceiling_queues():
    decision = evaluate_write(TIERED, "organizational", True, 1.0)
    assert decision.route == "queue"


def test_guardrail_trip_always_queues():
    decision = evaluate_write(TIERED, "informational", False, 1.0)
    assert decision.route == "queue"
    assert "guardrail" in decision.reason


def test_guardrails_not_applicable_is_not_a_trip():
    decision = evaluate_write(TIERED, "informational", None, 1.0)
    assert decision.route == "auto"


def test_low_confidence_queues():
    decision = evaluate_write(TIERED, "informational", True, 0.4)
    assert decision.route == "queue"
    assert "confidence" in decision.reason


def test_confidence_not_applicable_skips_check():
    decision = evaluate_write(TIERED, "informational", True, None)
    assert decision.route == "auto"


def test_raised_ceiling_preserves_organizational_countersign():
    policy = WriteGatePolicy(mode="tiered", auto_commit_max_authority="organizational")
    assert evaluate_write(policy, "organizational", True, 1.0).route == "queue"
    assert evaluate_write(policy, "industry_practice", True, 1.0).route == "queue"


# ── Proposal write-class derivation ──────────────────────────────────

def _fake_db(items, target_authorities):
    """Fake session: first execute returns items, second returns authorities."""
    db = AsyncMock()
    items_result = MagicMock()
    items_result.scalars.return_value.all.return_value = items
    auth_result = MagicMock()
    ids = list(dict.fromkeys(i.target_neuron_id for i in items if i.target_neuron_id))
    auth_result.all.return_value = [(nid, a, None) for nid, a in zip(ids, target_authorities)]
    db.execute = AsyncMock(side_effect=[items_result, auth_result])
    return db


def _item(action="update", target_neuron_id=None, spec=None):
    item = MagicMock()
    item.action = action
    item.target_neuron_id = target_neuron_id
    item.neuron_spec_json = json.dumps(spec) if spec else None
    return item


@pytest.mark.asyncio
async def test_proposal_authority_from_update_target():
    db = _fake_db([_item(target_neuron_id=5)], ["regulatory"])
    proposal = MagicMock(id=1)
    assert await proposal_max_authority(db, proposal) == "regulatory"


@pytest.mark.asyncio
async def test_proposal_authority_from_create_spec():
    # No target ids -> only the items query executes
    db = AsyncMock()
    items_result = MagicMock()
    items_result.scalars.return_value.all.return_value = [
        _item(action="create", spec={"authority_level": "binding_standard"}),
    ]
    db.execute = AsyncMock(return_value=items_result)
    proposal = MagicMock(id=2)
    assert await proposal_max_authority(db, proposal) == "binding_standard"


@pytest.mark.asyncio
async def test_proposal_authority_takes_max():
    db = _fake_db(
        [
            _item(target_neuron_id=5),
            _item(action="create", spec={"authority_level": "informational"}),
        ],
        ["organizational"],
    )
    proposal = MagicMock(id=3)
    assert await proposal_max_authority(db, proposal) == "organizational"


# ── route_proposal auto path ─────────────────────────────────────────

def _proposal(state="proposed"):
    p = MagicMock()
    p.id = 42
    p.state = state
    return p


@pytest.mark.asyncio
async def test_route_proposal_queue_leaves_state():
    p = _proposal()
    with patch("app.services.write_gate.load_policy", return_value=MANUAL), \
         patch("app.services.write_gate.proposal_max_authority",
               new=AsyncMock(return_value=None)):
        decision = await route_proposal(
            AsyncMock(), p, guardrails_passed=None, confidence=None,
        )
    assert decision.route == "queue"
    assert p.state == "proposed"


@pytest.mark.asyncio
async def test_route_proposal_auto_approves_and_applies():
    p = _proposal()
    apply_mock = AsyncMock(return_value=False)
    with patch("app.services.write_gate.load_policy", return_value=TIERED), \
         patch("app.services.write_gate.proposal_max_authority",
               new=AsyncMock(return_value="informational")), \
         patch("app.services.proposal_apply_service.apply_approved_proposal",
               new=apply_mock):
        decision = await route_proposal(
            AsyncMock(), p, guardrails_passed=True, confidence=None,
        )
    assert decision.route == "auto"
    assert p.state == "approved"
    assert p.reviewed_by == GATE_ACTOR
    assert "auto-approved" in p.review_notes
    apply_mock.assert_awaited_once()
    identity = apply_mock.await_args.args[2]
    assert identity.user_id == GATE_ACTOR
    assert apply_mock.await_args.kwargs.get("actor_type") == "system"


@pytest.mark.asyncio
async def test_route_proposal_requires_proposed_state():
    p = _proposal(state="approved")
    with pytest.raises(ValueError):
        await route_proposal(AsyncMock(), p, guardrails_passed=None, confidence=None)
