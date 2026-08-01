"""Contract tests for the reviewed, UI-facing architecture atlas."""

import json
from pathlib import Path

import pytest

from app.routers.architecture import get_architecture


@pytest.mark.asyncio
async def test_memory_engine_atlas_exposes_verified_runtime_truth():
    payload = await get_architecture()

    assert payload["schema_version"] == 4
    engine = payload["memory_engine"]
    zones = {zone["id"]: zone for zone in engine["zones"]}

    assert {
        "harness-boundary",
        "ingest-governance",
        "recall-engine",
        "maintenance-projection",
        "canonical-graph",
        "derived-state",
    } == set(zones)
    assert all(zone["evidence_ok"] for zone in zones.values())

    canonical = " ".join(zones["canonical-graph"]["details"]).casefold()
    derived = " ".join(zones["derived-state"]["details"]).casefold()
    recall = " ".join(zones["recall-engine"]["details"]).casefold()
    maintenance = " ".join(zones["maintenance-projection"]["details"]).casefold()

    assert "does not use pgvector" in canonical
    assert "text columns" in canonical
    assert "numpy" in derived
    assert "reciprocal-rank fusion" in recall
    assert "codex terra" in maintenance


@pytest.mark.asyncio
async def test_architecture_model_names_describe_alias_routing_not_fixed_providers():
    payload = await get_architecture()
    rendered = str({
        "summary": payload["system_summary"],
        "engine": payload["memory_engine"],
        "processes": payload["processes"],
    }).casefold()

    assert "opus capability grade" in rendered
    assert "codex sol primary by default" in rendered
    assert "sonnet capability grade" in rendered
    assert "codex terra primary by default" in rendered


def test_generated_architecture_is_checkout_path_independent():
    artifact = Path(__file__).resolve().parents[2] / "architecture/architecture.json"
    payload = json.loads(artifact.read_text())

    assert payload["root"] == "."
