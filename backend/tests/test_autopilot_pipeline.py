"""Unit tests for the autopilot-tick pipeline runner wiring (Pattern #8).

Isomorphic to `tests/test_pipeline_runner.py` — exercises the same runner
primitive but through fake autopilot stages that mutate an `AutopilotState`
instance. Covers: normal chain, ShortCircuit, hard-fail wrapping, telemetry
capture, and on_stage event emission. Does not hit the DB — `ctx.db` is
`None` because no test stage dereferences it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.models import AutopilotConfig
from app.services.autopilot_pipeline import AutopilotState, build_autopilot_pipeline
from app.services.pipeline import (
    PipelineContext,
    PipelineStageError,
    ShortCircuit,
    run_pipeline,
)


def _make_state() -> AutopilotState:
    """Build a minimal AutopilotState with a bare AutopilotConfig."""
    cfg = AutopilotConfig(
        id=1, enabled=True, directive="test directive",
        interval_minutes=30, max_layer=5, eval_model="haiku",
    )
    return AutopilotState(config=cfg)


class _BumpCostStage:
    """Stage that bumps state.total_cost by `inc` and marks a counter."""

    def __init__(self, name: str, inc: float) -> None:
        self.name = name
        self.inc = inc

    async def run(self, state: AutopilotState, _ctx: object) -> AutopilotState:
        state.total_cost += self.inc
        return state

    def describe(self, out: AutopilotState) -> dict[str, Any]:
        return {"total_cost": round(out.total_cost, 6)}


class _ShortCircuitGapStage:
    """Stage mirroring the no-gap-no-directive short-circuit policy."""

    name = "gap_detection"

    async def run(self, state: AutopilotState, _ctx: object) -> AutopilotState:
        raise ShortCircuit(state)

    def describe(self, _out: AutopilotState) -> dict[str, Any]:
        return {}


class _BoomStage:
    """Stage that raises an exception to exercise hard-fail wrapping."""

    name = "refinement"

    async def run(self, _state: AutopilotState, _ctx: object) -> AutopilotState:
        raise RuntimeError("refinement failed")

    def describe(self, _out: AutopilotState) -> dict[str, Any]:
        return {}


@pytest.mark.asyncio
async def test_autopilot_runner_chains_stages_and_records_telemetry():
    ctx = PipelineContext(db=None)  # type: ignore[arg-type]
    state = _make_state()
    stages = [
        _BumpCostStage("gap_detection", 0.001),
        _BumpCostStage("query_generation", 0.01),
        _BumpCostStage("persistence", 0.1),
    ]
    final = await run_pipeline(stages, state, ctx)
    assert final is state
    assert round(final.total_cost, 6) == round(0.001 + 0.01 + 0.1, 6)
    assert [t.stage for t in ctx.telemetry] == [
        "gap_detection", "query_generation", "persistence",
    ]
    assert all(t.status == "done" for t in ctx.telemetry)
    assert all(t.duration_ms >= 0.0 for t in ctx.telemetry)
    assert ctx.telemetry[-1].detail == {"total_cost": round(0.111, 6)}


@pytest.mark.asyncio
async def test_autopilot_runner_short_circuit_returns_final_value():
    ctx = PipelineContext(db=None)  # type: ignore[arg-type]
    state = _make_state()
    stages = [
        _BumpCostStage("warmup", 0.0),
        _ShortCircuitGapStage(),
        _BumpCostStage("never", 999.0),
    ]
    final = await run_pipeline(stages, state, ctx)
    assert final is state
    assert final.total_cost == 0.0
    assert [t.stage for t in ctx.telemetry] == ["warmup", "gap_detection"]
    assert ctx.telemetry[-1].detail == {"short_circuit": True}


@pytest.mark.asyncio
async def test_autopilot_runner_wraps_stage_exception_in_pipeline_stage_error():
    ctx = PipelineContext(db=None)  # type: ignore[arg-type]
    state = _make_state()
    stages = [_BumpCostStage("gap_detection", 0.0), _BoomStage(), _BumpCostStage("never", 999.0)]
    with pytest.raises(PipelineStageError) as exc_info:
        await run_pipeline(stages, state, ctx)
    assert exc_info.value.stage_name == "refinement"
    assert isinstance(exc_info.value.original, RuntimeError)
    assert ctx.telemetry[-1].stage == "refinement"
    assert ctx.telemetry[-1].status == "error"
    assert ctx.telemetry[-1].error_message == "refinement failed"


@pytest.mark.asyncio
async def test_autopilot_runner_emits_on_stage_events():
    events: list[tuple[str, dict]] = []

    async def capture(stage_name: str, payload: dict) -> None:
        events.append((stage_name, payload))

    ctx = PipelineContext(db=None, on_stage=capture)  # type: ignore[arg-type]
    state = _make_state()
    await run_pipeline(
        [_BumpCostStage("gap_detection", 0.0), _BumpCostStage("persistence", 0.0)],
        state, ctx,
    )
    assert [name for name, _ in events] == ["gap_detection", "persistence"]
    assert all("duration_ms" in payload for _, payload in events)
    assert all(payload["status"] == "done" for _, payload in events)


@pytest.mark.asyncio
async def test_autopilot_runner_telemetry_json_serializable():
    ctx = PipelineContext(db=None)  # type: ignore[arg-type]
    state = _make_state()
    await run_pipeline([_BumpCostStage("gap_detection", 0.0)], state, ctx)
    serialized = ctx.telemetry_json()
    assert isinstance(serialized, list)
    assert serialized[0]["stage"] == "gap_detection"
    assert "duration_ms" in serialized[0]
    assert serialized[0]["status"] == "done"
    # Must survive the round trip into the JSONB column.
    blob = json.dumps(serialized)
    assert json.loads(blob) == serialized


@pytest.mark.asyncio
async def test_build_autopilot_pipeline_returns_ordered_stages():
    """The canonical pipeline factory wires up the expected stage chain."""
    cfg = AutopilotConfig(
        id=1, enabled=True, directive="",
        interval_minutes=30, max_layer=5, eval_model="haiku",
    )
    chain = build_autopilot_pipeline(cfg)
    names = [s.name for s in chain]
    assert names == [
        "gap_detection",
        "query_generation",
        "pipeline_execution",
        "evaluation",
        "refinement",
        "proposal_curation",
        "persistence",
    ]
