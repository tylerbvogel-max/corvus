"""Runner for the typed pipeline DAG.

Iterates stages in order, times each one with `time.perf_counter`, and records
a `StageTelemetry` row into the context. On any stage exception it appends an
error telemetry row, emits an error event to `on_stage`, then raises
`PipelineStageError(stage_name, original)` — hard-fail per Pattern #5 v1.
"""

import time
from typing import Any, Sequence

from app.services.pipeline.context import PipelineContext
from app.services.pipeline.stage import (
    PipelineStageError,
    ShortCircuit,
    Stage,
    StageTelemetry,
)


async def _emit(ctx: PipelineContext, stage_name: str, payload: dict) -> None:
    if ctx.on_stage is not None:
        await ctx.on_stage(stage_name, payload)


def _safe_describe(stage: Stage, out: Any) -> dict[str, Any]:
    """Call stage.describe() defensively — a broken describe should not kill the query."""
    describe = getattr(stage, "describe", None)
    if describe is None:
        return {}
    try:
        detail = describe(out)
    except Exception:  # noqa: BLE001 — telemetry must never take down the pipeline
        return {}
    return detail if isinstance(detail, dict) else {}


async def run_pipeline(
    stages: Sequence[Stage],
    initial_input: Any,
    ctx: PipelineContext,
) -> Any:
    """Run stages in order, threading output → input, timing each one.

    Preconditions (JPL Rule 5):
    - stages is a non-empty, finite sequence (bounded loop — JPL Rule 2).
    - ctx is a PipelineContext.

    Behavior:
    - Normal: output of stage N is input of stage N+1; returns final output.
    - `ShortCircuit(value)`: stage opts out of remaining stages, returns value.
    - Any other exception: telemetry recorded, PipelineStageError raised.
    """
    assert isinstance(ctx, PipelineContext), "ctx must be a PipelineContext"
    assert len(stages) > 0, "stages sequence must be non-empty"

    current: Any = initial_input
    for stage in stages:
        stage_name = stage.name
        assert isinstance(stage_name, str) and stage_name, "Stage must have a non-empty name"
        t_start = time.perf_counter()
        try:
            current = await stage.run(current, ctx)
        except ShortCircuit as sc:
            duration_ms = (time.perf_counter() - t_start) * 1000.0
            telem = StageTelemetry(
                stage=stage_name,
                status="done",
                duration_ms=duration_ms,
                detail={"short_circuit": True},
            )
            ctx.telemetry.append(telem)
            await _emit(ctx, stage_name, {
                "status": "done",
                "duration_ms": round(duration_ms, 2),
                "detail": telem.detail,
            })
            return sc.final_value
        except Exception as original:  # noqa: BLE001 — we re-raise as PipelineStageError
            duration_ms = (time.perf_counter() - t_start) * 1000.0
            telem = StageTelemetry(
                stage=stage_name,
                status="error",
                duration_ms=duration_ms,
                error_message=str(original),
            )
            ctx.telemetry.append(telem)
            await _emit(ctx, stage_name, {
                "status": "error",
                "duration_ms": round(duration_ms, 2),
                "error": str(original),
            })
            raise PipelineStageError(stage_name, original) from original

        duration_ms = (time.perf_counter() - t_start) * 1000.0
        detail = _safe_describe(stage, current)
        telem = StageTelemetry(
            stage=stage_name,
            status="done",
            duration_ms=duration_ms,
            detail=detail,
        )
        ctx.telemetry.append(telem)
        await _emit(ctx, stage_name, {
            "status": "done",
            "duration_ms": round(duration_ms, 2),
            "detail": detail,
        })

    return current
