"""Unit tests for the typed pipeline runner (Pattern #5).

Covers: normal chain, ShortCircuit, hard-fail wrapping, telemetry capture,
and on_stage event emission. Does not touch the DB — the `ctx.db` field is
set to `None` because no stage under test dereferences it.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.pipeline import (
    PipelineContext,
    PipelineStageError,
    ShortCircuit,
    run_pipeline,
)


class _AddStage:
    """Stage that adds `inc` to an int input and describes the delta."""

    def __init__(self, name: str, inc: int) -> None:
        self.name = name
        self.inc = inc

    async def run(self, inp: int, _ctx: object) -> int:
        return inp + self.inc

    def describe(self, out: int) -> dict[str, Any]:
        return {"value": out}


class _ShortCircuitStage:
    name = "shorty"

    async def run(self, _inp: Any, _ctx: object) -> Any:
        raise ShortCircuit({"done": True})

    def describe(self, _out: Any) -> dict[str, Any]:
        return {}


class _BoomStage:
    name = "boom"

    async def run(self, _inp: Any, _ctx: object) -> Any:
        raise ValueError("kaboom")

    def describe(self, _out: Any) -> dict[str, Any]:
        return {}


@pytest.mark.asyncio
async def test_runner_chains_stages_and_records_telemetry():
    ctx = PipelineContext(db=None)  # type: ignore[arg-type]
    stages = [_AddStage("a", 1), _AddStage("b", 10), _AddStage("c", 100)]
    result = await run_pipeline(stages, 0, ctx)
    assert result == 111
    assert [t.stage for t in ctx.telemetry] == ["a", "b", "c"]
    assert all(t.status == "done" for t in ctx.telemetry)
    assert all(t.duration_ms >= 0.0 for t in ctx.telemetry)
    assert ctx.telemetry[-1].detail == {"value": 111}


@pytest.mark.asyncio
async def test_runner_short_circuit_returns_final_value():
    ctx = PipelineContext(db=None)  # type: ignore[arg-type]
    stages = [_AddStage("a", 1), _ShortCircuitStage(), _AddStage("never", 999)]
    result = await run_pipeline(stages, 0, ctx)
    assert result == {"done": True}
    # The third stage must not have run.
    assert [t.stage for t in ctx.telemetry] == ["a", "shorty"]
    assert ctx.telemetry[-1].detail == {"short_circuit": True}


@pytest.mark.asyncio
async def test_runner_wraps_stage_exception_in_pipeline_stage_error():
    ctx = PipelineContext(db=None)  # type: ignore[arg-type]
    stages = [_AddStage("a", 1), _BoomStage(), _AddStage("never", 999)]
    with pytest.raises(PipelineStageError) as exc_info:
        await run_pipeline(stages, 0, ctx)
    assert exc_info.value.stage_name == "boom"
    assert isinstance(exc_info.value.original, ValueError)
    # Telemetry must record the failing stage as "error".
    assert ctx.telemetry[-1].stage == "boom"
    assert ctx.telemetry[-1].status == "error"
    assert ctx.telemetry[-1].error_message == "kaboom"


@pytest.mark.asyncio
async def test_runner_emits_on_stage_events():
    events: list[tuple[str, dict]] = []

    async def capture(stage_name: str, payload: dict) -> None:
        events.append((stage_name, payload))

    ctx = PipelineContext(db=None, on_stage=capture)  # type: ignore[arg-type]
    await run_pipeline([_AddStage("a", 1), _AddStage("b", 2)], 0, ctx)
    assert [name for name, _ in events] == ["a", "b"]
    assert all("duration_ms" in payload for _, payload in events)
    assert all(payload["status"] == "done" for _, payload in events)


@pytest.mark.asyncio
async def test_runner_telemetry_json_serializable():
    ctx = PipelineContext(db=None)  # type: ignore[arg-type]
    await run_pipeline([_AddStage("a", 1)], 0, ctx)
    serialized = ctx.telemetry_json()
    assert isinstance(serialized, list)
    assert serialized[0]["stage"] == "a"
    assert "duration_ms" in serialized[0]
    assert serialized[0]["status"] == "done"
