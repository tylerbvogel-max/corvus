"""Committed source edits invalidate projected high-trust skills."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SkillAwareSession
from app.models import Neuron
from app.services.actions.neuron_refine import NeuronRefineInput, handle_neuron_refine


@pytest.mark.asyncio
async def test_commit_refreshes_changed_source_only_after_commit(monkeypatch):
    events = []

    async def committed(self):
        events.append("commit")

    async def refresh(db, ids):
        events.append(("refresh", ids))
        return {"db_changed": False}

    monkeypatch.setattr(AsyncSession, "commit", committed)
    from app.services import skill_compiler
    monkeypatch.setattr(skill_compiler, "refresh_projections_after_reconsolidation", refresh)
    db = SkillAwareSession()
    db.info["skill_source_changed"] = {7, 3}
    await db.commit()
    assert events == ["commit", ("refresh", [3, 7])]
    assert "skill_source_changed" not in db.info


@pytest.mark.asyncio
async def test_rollback_discards_pending_projection_refresh(monkeypatch):
    events = []

    async def rolled_back(self):
        events.append("rollback")

    async def refresh(db, ids):
        events.append("refresh")
        return {"db_changed": False}

    monkeypatch.setattr(AsyncSession, "rollback", rolled_back)
    from app.services import skill_compiler
    monkeypatch.setattr(skill_compiler, "refresh_projections_after_reconsolidation", refresh)
    db = SkillAwareSession()
    db.info["skill_source_changed"] = {7}
    await db.rollback()
    assert events == ["rollback"]
    assert "skill_source_changed" not in db.info


@pytest.mark.asyncio
async def test_refine_marks_retired_source_for_post_commit_refresh():
    neuron = Neuron(id=57, label="old", content="old", layer=3,
                    node_type="lesson", is_active=True)
    db = MagicMock()
    db.info = {}
    db.get = AsyncMock(return_value=neuron)
    db.flush = AsyncMock()
    db.add = MagicMock(side_effect=lambda obj: setattr(obj, "id", 999))
    payload = NeuronRefineInput(target_neuron_id=57, field="is_active",
                                old_value="true", new_value="false")
    with patch("app.services.neuron_index.invalidate_index"):
        await handle_neuron_refine(
            payload, MagicMock(user_id="test"), db, MagicMock())
    assert neuron.is_active is False
    assert db.info["skill_source_changed"] == {57}
