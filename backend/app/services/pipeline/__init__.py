"""Typed pipeline DAG — Pattern #5.

Each stage is a named, typed unit with observable telemetry. A stage failure
hard-fails the whole query with a `PipelineStageError` carrying the stage name
so the caller can surface which stage broke without parsing tracebacks.

Usage:
    from app.services.pipeline import run_pipeline, PipelineContext
    ctx = PipelineContext(db=db, on_stage=on_stage)
    result = await run_pipeline([stage_a, stage_b, stage_c], initial_input, ctx)
    # ctx.telemetry now holds per-stage timing + status
"""

from app.services.pipeline.stage import (
    BranchStage,
    Stage,
    StageTelemetry,
    PipelineStageError,
    ShortCircuit,
    is_branch_stage,
)
from app.services.pipeline.context import PipelineContext
from app.services.pipeline.runner import MAX_BRANCH_DEPTH, run_pipeline

__all__ = [
    "BranchStage",
    "Stage",
    "StageTelemetry",
    "PipelineStageError",
    "ShortCircuit",
    "PipelineContext",
    "MAX_BRANCH_DEPTH",
    "is_branch_stage",
    "run_pipeline",
]
