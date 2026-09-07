"""Invalid authority must never become permission for an automatic write."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from app.services import write_gate as gate


INVALID = ["unrecognized_authority", "", " ", "Informational", "informational ", 0, False, [], {}]


@pytest.mark.parametrize("authority", INVALID)
def test_invalid_authority_rejected_at_rank_and_gate(authority):
    with pytest.raises(ValueError):
        gate.authority_rank(authority)
    for mode in ("manual", "tiered"):
        with pytest.raises(ValueError):
            gate.evaluate_write(gate.WriteGatePolicy(mode=mode), authority, None, None)


@pytest.mark.parametrize("raw", [
    {"mode": "typo"}, {"mode": None}, {"auto_commit_max_authority": ""},
    {"auto_commit_max_authority": "typo"}, {"auto_commit_max_authority": None},
    {"require_guardrails_pass": "false"}, {"require_guardrails_pass": 0},
    {"min_confidence": -0.1}, {"min_confidence": 1.1},
    {"min_confidence": float("nan")}, {"min_confidence": float("inf")},
    {"min_confidence": True}, {"min_confidence": "0.6"},
])
def test_invalid_policy_rejected_at_construction_and_overlay(raw):
    with pytest.raises(ValueError):
        gate.WriteGatePolicy(**raw)
    with pytest.raises(ValueError):
        gate._policy_from_dict(raw)


@pytest.mark.parametrize("raw", [None, [], "tiered", {"mdoe": "tiered"}])
def test_malformed_policy_container_rejected(raw):
    with pytest.raises(ValueError):
        gate._policy_from_dict(raw)


@pytest.mark.parametrize("confidence", [-1, 2, float("nan"), float("inf"), "1", True])
def test_invalid_confidence_rejected_even_in_manual_mode(confidence):
    with pytest.raises(ValueError):
        gate.evaluate_write(gate.WriteGatePolicy(), "informational", None, confidence)


@pytest.mark.parametrize("guardrails", [0, 1, "false", [], {}])
def test_invalid_guardrail_signal_rejected(guardrails):
    with pytest.raises(ValueError):
        gate.evaluate_write(gate.WriteGatePolicy(mode="tiered"), None, guardrails, None)


def test_none_is_deliberate_internal_default():
    assert gate.authority_rank(None) == gate.authority_rank("informational")
    assert gate.evaluate_write(gate.WriteGatePolicy(mode="tiered"), None, None, None).route == "auto"


def test_policy_is_revalidated_at_evaluation_boundary():
    policy = gate.WriteGatePolicy(mode="tiered")
    object.__setattr__(policy, "mode", "invalid")
    with pytest.raises(ValueError):
        gate.evaluate_write(policy, "informational", None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [None, False, [], "tiered", {"min_confidence": "0.8"}])
async def test_region_override_cannot_hide_invalid_falsey_config(monkeypatch, override):
    from app.services import region_policy
    monkeypatch.setattr(gate, "load_policy", lambda: gate.WriteGatePolicy())
    monkeypatch.setattr(region_policy, "get_region_policies", AsyncMock(return_value={"Projects": {"write_gate": override}}))
    with pytest.raises(ValueError):
        await gate.policy_for_region(AsyncMock(), "Projects")


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["link", "rescale", "reconsolidate", "unknown"])
async def test_unclassified_mutations_do_not_auto_route(action):
    with pytest.raises(ValueError):
        await gate.proposal_max_authority(database([item("{}", action=action)]), SimpleNamespace(id=1))


@pytest.mark.asyncio
async def test_empty_proposal_is_not_auto_approved():
    with pytest.raises(ValueError):
        await gate.proposal_max_authority(database([]), SimpleNamespace(id=1))


def test_http_authority_vocabulary_matches_gate():
    from typing import get_args
    assert set(get_args(gate.AuthorityLevel)) == set(gate.AUTHORITY_RANK)


@pytest.mark.parametrize("authority", ["organizational", "industry_practice", "regulatory", "binding_standard"])
def test_countersign_cannot_be_bypassed_by_raised_ceiling(authority):
    policy = gate.WriteGatePolicy(mode="tiered", auto_commit_max_authority="binding_standard")
    assert gate.evaluate_write(policy, authority, True, 1.0).route == "queue"


def item(spec=None, action="create", target=None, field=None, new_value=None):
    return SimpleNamespace(action=action, target_neuron_id=target, field=field,
                           new_value=new_value, neuron_spec_json=spec)


def database(items, targets=()):
    db = AsyncMock()
    first, second = MagicMock(), MagicMock()
    first.scalars.return_value.all.return_value = items
    second.all.return_value = targets
    db.execute.side_effect = [first, second]
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize("spec", [
    '{"authority_level":"bad"}', '{"authority_level":""}',
    '{"authority_level":[]}', '{"authority_level":false}',
    'invalid json', '[]', 'null', '"informational"', '', None,
])
async def test_mixed_proposal_cannot_hide_invalid_spec(spec):
    db = database([item('{"authority_level":"regulatory"}'), item(spec)])
    with pytest.raises(ValueError):
        await gate.proposal_max_authority(db, SimpleNamespace(id=1))


@pytest.mark.asyncio
async def test_invalid_target_authority_blocks_routing_without_side_effects(monkeypatch):
    db = database([item(action="update", target=10)], [(10, "bad", "Projects")])
    proposal = SimpleNamespace(id=1, state="proposed")
    monkeypatch.setattr(gate, "policy_for_region", AsyncMock(return_value=gate.WriteGatePolicy(mode="tiered")))
    with pytest.raises(ValueError):
        await gate.route_proposal(db, proposal, guardrails_passed=None, confidence=None)
    assert proposal.state == "proposed"
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_target_rejected():
    db = database([item(action="update", target=10)])
    with pytest.raises(ValueError):
        await gate.proposal_max_authority(db, SimpleNamespace(id=1))


@pytest.mark.asyncio
@pytest.mark.parametrize("new_value", ["bad", "", "organizational"])
async def test_authority_edits_consider_new_value(new_value):
    db = database([item(action="update", target=10, field="authority_level", new_value=new_value)],
                  [(10, "informational", "Projects")])
    if new_value == "organizational":
        assert await gate.proposal_max_authority(db, SimpleNamespace(id=1)) == new_value
    else:
        with pytest.raises(ValueError):
            await gate.proposal_max_authority(db, SimpleNamespace(id=1))


@pytest.mark.asyncio
@pytest.mark.parametrize("items,targets", [
    ([item('{"department":" assistant ","authority_level":"informational"}')], []),
    ([item(action="update", target=10)], [(10, None, "Assistant")]),
    ([item(action="update", target=10, field="department", new_value="Assistant")],
     [(10, "informational", "Projects")]),
])
async def test_identity_scope_requires_countersign(items, targets):
    authority = await gate.proposal_max_authority(database(items, targets), SimpleNamespace(id=1))
    assert gate.evaluate_write(gate.WriteGatePolicy(mode="tiered", auto_commit_max_authority="binding_standard"),
                               authority, None, None).route == "queue"


@pytest.mark.asyncio
@pytest.mark.parametrize("authority", INVALID)
async def test_lesson_store_rejects_before_database_or_provider_work(authority):
    from app.services.lesson_store import save_lesson
    db = AsyncMock()
    db.add = MagicMock()
    with pytest.raises(ValueError):
        await save_lesson(db, lesson="synthetic", evidence="synthetic", label="synthetic", authority_level=authority)
    db.add.assert_not_called()
    db.flush.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("authority", INVALID + [None])
async def test_remember_http_rejects_invalid_authority_before_save(monkeypatch, authority):
    from app.routers import recall
    app = FastAPI()
    app.include_router(recall.router)
    async def fake_db():
        yield AsyncMock()
    app.dependency_overrides[recall.get_db] = fake_db
    save = AsyncMock(return_value={"route": "auto"})
    monkeypatch.setattr(recall, "save_lesson", save)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/remember", json={"lesson":"synthetic", "evidence":"synthetic", "label":"synthetic", "authority_level":authority})
    assert response.status_code == 422
    save.assert_not_awaited()


@pytest.mark.asyncio
async def test_remember_http_omission_keeps_informational_default(monkeypatch):
    from app.routers import recall
    app = FastAPI()
    app.include_router(recall.router)
    async def fake_db():
        yield AsyncMock()
    app.dependency_overrides[recall.get_db] = fake_db
    save = AsyncMock(return_value={"route": "auto"})
    monkeypatch.setattr(recall, "save_lesson", save)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/remember", json={"lesson":"synthetic", "evidence":"synthetic", "label":"synthetic"})
    assert response.status_code == 200
    assert save.await_args.kwargs["authority_level"] == "informational"
