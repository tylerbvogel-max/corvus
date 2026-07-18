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
