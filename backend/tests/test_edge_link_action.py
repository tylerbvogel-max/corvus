"""Regression tests for idempotent promoted edge assertions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.actions.edge_link import EdgeLinkInput, handle_edge_link


def _db(existing=None):
    db = AsyncMock()
    db.get = AsyncMock(return_value=existing)
    db.add = MagicMock()
    return db


@pytest.mark.asyncio
async def test_promoted_edge_reassertion_does_not_insert_duplicate():
    existing = MagicMock(
        edge_type="evidence-link", weight=1.0, co_fire_count=2,
        context="old", source="mind_janitor", last_updated_query=0,
    )
    db = _db(existing)
    delete_weak = AsyncMock()
    with patch("app.services.edge_tier.delete_weak_edge", delete_weak):
        result = await handle_edge_link(
            EdgeLinkInput(
                source_id=47, target_id=57, weight=1.0, co_fire_count=2,
                edge_type="evidence-link", source="mind_janitor",
                context="consolidation provenance",
            ),
            MagicMock(), db, MagicMock(),
        )

    db.add.assert_not_called()
    delete_weak.assert_awaited_once_with(db, 47, 57)
    assert result["audit"]["existing"] is True
    assert result["audit"]["retyped"] is False


@pytest.mark.asyncio
async def test_memory_assertion_retypes_existing_activation_edge():
    existing = MagicMock(
        edge_type="stellate", weight=0.4879, co_fire_count=5,
        context="", source="bootstrap", last_updated_query=0,
    )
    db = _db(existing)
    delete_weak = AsyncMock()
    with patch("app.services.edge_tier.delete_weak_edge", delete_weak):
        result = await handle_edge_link(
            EdgeLinkInput(
                source_id=47, target_id=57, weight=1.0, co_fire_count=2,
                edge_type="evidence-link", source="mind_janitor",
                context="consolidation provenance",
            ),
            MagicMock(), db, MagicMock(),
        )

    db.add.assert_not_called()
    assert existing.edge_type == "evidence-link"
    assert existing.weight == 1.0
    assert existing.co_fire_count == 5
    assert existing.source == "mind_janitor"
    assert existing.context == "consolidation provenance"
    assert result["audit"]["existing"] is True
    assert result["audit"]["retyped"] is True


@pytest.mark.asyncio
async def test_authoritative_topology_forces_promoted_storage_below_threshold():
    db = _db(existing=None)
    delete_weak = AsyncMock()
    with patch("app.services.edge_tier.delete_weak_edge", delete_weak):
        result = await handle_edge_link(
            EdgeLinkInput(
                source_id=7,
                target_id=11,
                weight=0.01,
                co_fire_count=0,
                edge_type="instantiates",
                source="concept_seed",
                storage_tier="promoted",
            ),
            MagicMock(),
            db,
            MagicMock(),
        )

    db.add.assert_called_once()
    edge = db.add.call_args.args[0]
    assert edge.source_id == 7
    assert edge.target_id == 11
    assert edge.edge_type == "instantiates"
    assert result["audit"]["promoted"] is True
    assert result["audit"]["storage_tier"] == "promoted"
    delete_weak.assert_awaited_once_with(db, 7, 11)


@pytest.mark.asyncio
async def test_concept_assertion_retypes_edge_without_lowering_stronger_weight():
    existing = MagicMock(
        edge_type="pyramidal",
        weight=0.8,
        co_fire_count=5,
        context="organic",
        source="organic",
        last_updated_query=44,
    )
    db = _db(existing)
    delete_weak = AsyncMock()
    with patch("app.services.edge_tier.delete_weak_edge", delete_weak):
        result = await handle_edge_link(
            EdgeLinkInput(
                source_id=7,
                target_id=11,
                weight=0.3,
                co_fire_count=1,
                edge_type="instantiates",
                source="concept_seed",
                context="instantiates concept: Flow",
                storage_tier="promoted",
            ),
            MagicMock(),
            db,
            MagicMock(),
        )

    assert existing.edge_type == "instantiates"
    assert existing.weight == 0.8
    assert existing.co_fire_count == 5
    assert existing.source == "concept_seed"
    assert result["audit"]["retyped"] is True
