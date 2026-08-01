"""Authoritative concept topology must enter through typed Action Bus actions."""

import ast
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.action_bus import ActionResult
from app.services.concept_service import (
    ConceptMutationError,
    create_concept_neuron,
    link_concept_to_neurons,
)


def test_concept_service_has_no_direct_authoritative_graph_writer():
    """Honeypot: a future bypass should fail before it reaches architecture drift."""
    path = Path(__file__).parents[1] / "app" / "services" / "concept_service.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    constructors = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"Neuron", "NeuronEdge"}
    ]
    assert constructors == []
    assert "INSERT INTO NEURON_EDGES" not in source.upper()


@pytest.mark.asyncio
async def test_create_concept_uses_neuron_create_action_then_derives_embedding():
    db = AsyncMock()
    neuron = MagicMock(id=91, embedding=None)
    db.get = AsyncMock(return_value=neuron)
    submit = AsyncMock(return_value=ActionResult(
        action_id=12,
        state="applied",
        payload={"neuron_id": 91, "refinement_id": 33},
    ))
    state = MagicMock(total_queries=713)

    with patch(
        "app.services.neuron_service.get_system_state",
        new=AsyncMock(return_value=state),
    ), patch(
        "app.services.action_bus.submit",
        new=submit,
    ), patch(
        "app.services.embedding_service.embed_text",
        return_value=[0.25, 0.75],
    ):
        result = await create_concept_neuron(
            db,
            label="Three Horizons",
            content="A framework for managing present and future systems.",
            summary="Three planning horizons",
        )

    assert result is neuron
    assert json.loads(neuron.embedding) == [0.25, 0.75]
    db.flush.assert_awaited_once()
    call = submit.await_args.kwargs
    assert call["kind"] == "neuron.create"
    assert call["actor_type"] == "system"
    assert call["input_data"]["total_queries"] == 713
    assert call["input_data"]["spec"] == {
        "parent_id": None,
        "layer": -1,
        "node_type": "concept",
        "label": "Three Horizons",
        "content": "A framework for managing present and future systems.",
        "summary": "Three planning horizons",
        "department": None,
        "role_key": None,
        "source_type": "operational",
        "source_origin": "concept",
    }


@pytest.mark.asyncio
async def test_create_concept_fails_closed_when_action_fails():
    db = AsyncMock()
    state = MagicMock(total_queries=1)
    submit = AsyncMock(return_value=ActionResult(
        action_id=13,
        state="failed",
        error="validation rejected",
    ))

    with patch(
        "app.services.neuron_service.get_system_state",
        new=AsyncMock(return_value=state),
    ), patch(
        "app.services.action_bus.submit",
        new=submit,
    ):
        with pytest.raises(ConceptMutationError, match="validation rejected"):
            await create_concept_neuron(db, "Unsafe", "untrusted")

    db.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_concept_links_are_promoted_edge_actions():
    db = AsyncMock()
    submit = AsyncMock(side_effect=[
        ActionResult(action_id=20, state="applied", payload={"promoted": True}),
        ActionResult(action_id=21, state="applied", payload={"promoted": True}),
    ])

    with patch("app.services.action_bus.submit", new=submit):
        count = await link_concept_to_neurons(
            db,
            concept_id=5,
            target_ids=[9, 3],
            weight=0.3,
            concept_label="Flow",
        )

    assert count == 2
    db.flush.assert_awaited_once()
    first = submit.await_args_list[0].kwargs
    second = submit.await_args_list[1].kwargs
    assert first["kind"] == second["kind"] == "edge.link"
    assert first["input_data"] == {
        "source_id": 5,
        "target_id": 9,
        "weight": 0.3,
        "co_fire_count": 1,
        "edge_type": "instantiates",
        "source": "concept_seed",
        "context": "instantiates concept: Flow",
        "last_updated_query": 0,
        "storage_tier": "promoted",
    }
    assert second["input_data"]["source_id"] == 3
    assert second["input_data"]["target_id"] == 5


@pytest.mark.asyncio
async def test_concept_link_fails_closed_when_child_action_fails():
    db = AsyncMock()
    submit = AsyncMock(return_value=ActionResult(
        action_id=22,
        state="failed",
        error="edge rejected",
    ))

    with patch("app.services.action_bus.submit", new=submit):
        with pytest.raises(ConceptMutationError, match="edge rejected"):
            await link_concept_to_neurons(db, 5, [9])

    db.flush.assert_not_awaited()
