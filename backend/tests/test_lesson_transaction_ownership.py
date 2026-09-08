"""Caller-owned lesson writes preserve gates and defer cache-producing work."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services import lesson_store


@pytest.fixture
def store(monkeypatch):
    rows = []
    db = SimpleNamespace(add=Mock(side_effect=rows.append), flush=AsyncMock(),
                         commit=AsyncMock(), refresh=AsyncMock())

    async def flush():
        for index, row in enumerate(rows, 1):
            row.id = index
            if hasattr(row, "created_neuron_id"):
                row.created_neuron_id = 42

    db.flush.side_effect = flush
    monkeypatch.setattr(lesson_store, "resolve_scope_anchor", AsyncMock(return_value=(None, None, None)))
    monkeypatch.setattr(lesson_store, "_nearest_active_lesson", AsyncMock(return_value=None))
    route = AsyncMock(return_value=SimpleNamespace(route="auto", reason="synthetic"))
    enrich = AsyncMock()
    monkeypatch.setattr(lesson_store, "route_proposal", route)
    monkeypatch.setattr(lesson_store, "_embed_and_wire", enrich)
    return db, route, enrich


def arguments():
    return dict(lesson="Synthetic transaction boundary.", evidence="Synthetic fixture.",
                label="synthetic", future_use="Test rollback.", likely_queries="Who commits?")


@pytest.mark.asyncio
@pytest.mark.parametrize("commit", [True, False])
@pytest.mark.parametrize("route_name", ["auto", "queue"])
async def test_routed_save_respects_transaction_owner(store, commit, route_name):
    db, route, enrich = store
    route.return_value = SimpleNamespace(route=route_name, reason="synthetic")
    result = await lesson_store.save_lesson(db, **arguments(), commit=commit)
    route.assert_awaited_once()
    assert db.commit.await_count == int(commit)
    assert enrich.await_count == int(commit and route_name == "auto")
    assert result["route"] == route_name
    if commit:
        assert "enrichment_pending" not in result
    else:
        assert result["enrichment_pending"] is (route_name == "auto")


@pytest.mark.asyncio
@pytest.mark.parametrize("commit", [True, False])
@pytest.mark.parametrize("disposition", ["queue", "skip"])
async def test_near_duplicate_branches_respect_owner(store, monkeypatch, commit, disposition):
    db, route, enrich = store
    monkeypatch.setattr(lesson_store, "_nearest_active_lesson", AsyncMock(return_value={
        "id": 7, "label": "synthetic neighbor", "sim": .99, "lane": "cosine"}))
    monkeypatch.setattr(lesson_store, "_near_dup_disposition", lambda *args: disposition)
    result = await lesson_store.save_lesson(db, **arguments(), commit=commit)
    assert result["route"] == disposition
    assert result["neuron_id"] is None
    assert db.commit.await_count == int(commit)
    route.assert_not_awaited()
    enrich.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [None, 0, 1, "false", [], {}])
async def test_transaction_mode_rejected_before_any_write(store, invalid):
    db, route, enrich = store
    with pytest.raises(ValueError, match="commit must be a boolean"):
        await lesson_store.save_lesson(db, **arguments(), commit=invalid)
    db.add.assert_not_called()
    db.flush.assert_not_awaited()
    db.commit.assert_not_awaited()
    route.assert_not_awaited()
    enrich.assert_not_awaited()


@pytest.mark.asyncio
async def test_staged_assistant_scope_still_escalates_authority(store):
    db, route, enrich = store
    await lesson_store.save_lesson(db, **arguments(), scope="Assistant", commit=False)
    specs = [call.args[0].neuron_spec_json for call in db.add.call_args_list
             if hasattr(call.args[0], "neuron_spec_json")]
    import json
    assert json.loads(specs[0])["authority_level"] == "organizational"
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_default_mode_preserves_commit_and_enrichment(store):
    db, route, enrich = store
    await lesson_store.save_lesson(db, **arguments())
    db.commit.assert_awaited_once()
    enrich.assert_awaited_once_with(db, 42)
