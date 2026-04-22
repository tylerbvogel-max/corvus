"""Runner for the typed pipeline DAG.

Iterative stack-based traversal that supports both plain `Stage` and
`BranchStage` (Pattern #6 — conditional fan-out). Each frame on the stack
represents one level of the pipeline: the top-level chain or a sub-pipeline
inside a BranchStage. JPL-1 no-recursion is respected — sub-pipelines are
pushed as new frames, not recursive `run_pipeline` calls.

Every stage's execution is timed with `time.perf_counter`, records a
`StageTelemetry` row into the context, and emits an `on_stage` event.
On any non-`ShortCircuit` exception a `PipelineStageError` is raised with
a slash-prefixed stage name like `router_name/branch_key/inner_stage` so
the failure location is unambiguous in logs and SSE error events.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from app.services.pipeline.context import PipelineContext
from app.services.pipeline.stage import (
    PipelineStageError,
    ShortCircuit,
    Stage,
    StageTelemetry,
    is_branch_stage,
)


# Hard cap on branches-inside-branches. Arbitrary bound that catches obvious
# design mistakes; loosen only when a legitimate deeper use case appears.
MAX_BRANCH_DEPTH = 4


@dataclass
class _Frame:
    """One level of the execution stack — either the top chain or a sub-pipeline."""
    stages: list
    current: Any
    depth: int = 0
    label: str = ""  # branch_context prefix for stages in this frame
    idx: int = 0


def _qualified_name(frame: _Frame, stage_name: str) -> str:
    """Compose the label prefix with the stage name for error messages."""
    return f"{frame.label}{stage_name}" if frame.label else stage_name


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


def _record_telemetry(
    ctx: PipelineContext,
    frame: _Frame,
    stage_name: str,
    status: str,
    duration_ms: float,
    detail: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> StageTelemetry:
    """Append a StageTelemetry row tagged with the frame's branch context."""
    telem = StageTelemetry(
        stage=stage_name,
        status=status,
        duration_ms=duration_ms,
        detail=detail or {},
        error_message=error_message,
        branch_context=frame.label or None,
    )
    ctx.telemetry.append(telem)
    return telem


def _payload_for_emit(telem: StageTelemetry) -> dict[str, Any]:
    """Translate a StageTelemetry into the on_stage event payload."""
    payload: dict[str, Any] = {
        "status": telem.status,
        "duration_ms": round(telem.duration_ms, 2),
    }
    if telem.detail:
        payload["detail"] = telem.detail
    if telem.error_message is not None:
        payload["error"] = telem.error_message
    if telem.branch_context:
        payload["branch_context"] = telem.branch_context
    return payload


def _child_label(parent_label: str, stage_name: str, branch_key: str) -> str:
    """Compose the branch_context prefix for a sub-pipeline frame."""
    return f"{parent_label}{stage_name}:{branch_key}/"


async def _run_branch_stage(
    stage: Any,
    frame: _Frame,
    ctx: PipelineContext,
    stack: list[_Frame],
) -> None:
    """Execute a BranchStage: route, record telemetry, push sub-pipeline frame."""
    stage_name = stage.name
    assert isinstance(stage_name, str) and stage_name, "Stage must have a non-empty name"
    t_start = time.perf_counter()
    try:
        key = await stage.route(frame.current, ctx)
    except Exception as original:  # noqa: BLE001 — re-raise as PipelineStageError
        duration_ms = (time.perf_counter() - t_start) * 1000.0
        telem = _record_telemetry(
            ctx, frame, stage_name, "error", duration_ms,
            error_message=str(original),
        )
        await _emit(ctx, stage_name, _payload_for_emit(telem))
        raise PipelineStageError(_qualified_name(frame, stage_name), original) from original

    assert isinstance(key, str), f"{stage_name}.route() must return a str"
    if key == "":
        duration_ms = (time.perf_counter() - t_start) * 1000.0
        telem = _record_telemetry(
            ctx, frame, stage_name, "done", duration_ms,
            detail={"branch_taken": "", "sub_stages": 0},
        )
        await _emit(ctx, stage_name, _payload_for_emit(telem))
        return

    sub = stage.branches.get(key)
    if sub is None:
        duration_ms = (time.perf_counter() - t_start) * 1000.0
        err = KeyError(f"unknown branch key '{key}'")
        telem = _record_telemetry(
            ctx, frame, stage_name, "error", duration_ms,
            error_message=str(err),
        )
        await _emit(ctx, stage_name, _payload_for_emit(telem))
        raise PipelineStageError(_qualified_name(frame, stage_name), err) from err

    duration_ms = (time.perf_counter() - t_start) * 1000.0
    telem = _record_telemetry(
        ctx, frame, stage_name, "done", duration_ms,
        detail={"branch_taken": key, "sub_stages": len(sub)},
    )
    await _emit(ctx, stage_name, _payload_for_emit(telem))

    new_depth = frame.depth + 1
    assert new_depth <= MAX_BRANCH_DEPTH, \
        f"branch depth {new_depth} exceeds MAX_BRANCH_DEPTH={MAX_BRANCH_DEPTH}"
    stack.append(_Frame(
        stages=list(sub),
        current=frame.current,
        depth=new_depth,
        label=_child_label(frame.label, stage_name, key),
    ))


async def _run_regular_stage(
    stage: Stage,
    frame: _Frame,
    ctx: PipelineContext,
    stack: list[_Frame],
) -> tuple[bool, Any]:
    """Execute a plain Stage. Returns (should_return, return_value).

    should_return=True signals the outer loop to exit with return_value
    (used for top-level ShortCircuit).
    """
    stage_name = stage.name
    assert isinstance(stage_name, str) and stage_name, "Stage must have a non-empty name"
    t_start = time.perf_counter()
    try:
        frame.current = await stage.run(frame.current, ctx)
    except ShortCircuit as sc:
        duration_ms = (time.perf_counter() - t_start) * 1000.0
        telem = _record_telemetry(
            ctx, frame, stage_name, "done", duration_ms,
            detail={"short_circuit": True},
        )
        await _emit(ctx, stage_name, _payload_for_emit(telem))
        stack.pop()
        if stack:
            stack[-1].current = sc.final_value
            return (False, None)
        return (True, sc.final_value)
    except Exception as original:  # noqa: BLE001 — re-raise as PipelineStageError
        duration_ms = (time.perf_counter() - t_start) * 1000.0
        telem = _record_telemetry(
            ctx, frame, stage_name, "error", duration_ms,
            error_message=str(original),
        )
        await _emit(ctx, stage_name, _payload_for_emit(telem))
        raise PipelineStageError(_qualified_name(frame, stage_name), original) from original

    duration_ms = (time.perf_counter() - t_start) * 1000.0
    telem = _record_telemetry(
        ctx, frame, stage_name, "done", duration_ms,
        detail=_safe_describe(stage, frame.current),
    )
    await _emit(ctx, stage_name, _payload_for_emit(telem))
    return (False, None)


async def run_pipeline(
    stages: Sequence[Stage],
    initial_input: Any,
    ctx: PipelineContext,
) -> Any:
    """Run stages in order, threading output → input, timing each one.

    Preconditions (JPL Rule 5):
    - stages is a non-empty, finite sequence.
    - ctx is a PipelineContext.

    Behavior:
    - Normal: output of stage N is input of stage N+1; returns final output.
    - `BranchStage`: route() picks a sub-pipeline; its output replaces the
      current value; parent chain continues.
    - `ShortCircuit(value)` at top level: returns value immediately.
    - `ShortCircuit(value)` inside a branch: branch returns value; parent continues.
    - Any other exception: telemetry recorded, PipelineStageError raised.
    """
    assert isinstance(ctx, PipelineContext), "ctx must be a PipelineContext"
    assert len(stages) > 0, "stages sequence must be non-empty"

    stack: list[_Frame] = [_Frame(stages=list(stages), current=initial_input)]
    final_value: Any = initial_input

    while stack:
        frame = stack[-1]
        if frame.idx >= len(frame.stages):
            finished = stack.pop()
            if stack:
                stack[-1].current = finished.current
            else:
                final_value = finished.current
            continue

        stage = frame.stages[frame.idx]
        frame.idx += 1

        if is_branch_stage(stage):
            await _run_branch_stage(stage, frame, ctx, stack)
            continue

        should_return, ret_value = await _run_regular_stage(stage, frame, ctx, stack)
        if should_return:
            return ret_value

    return final_value
