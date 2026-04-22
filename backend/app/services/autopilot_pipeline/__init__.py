"""Typed pipeline DAG for the autopilot tick — Pattern #8.

Second consumer of the typed-compute primitive shipped on 2026-04-21 for
Pattern #5. The query-prep pipeline already composes `Stage` instances via
`run_pipeline`; the autopilot tick follows the same shape so both flows
share telemetry, short-circuit semantics, and per-stage hard-fail behavior.

Usage:
    from app.services.autopilot_pipeline import (
        build_autopilot_pipeline, AutopilotState,
    )
    from app.services.pipeline import PipelineContext, run_pipeline

    state = AutopilotState(config=config)
    ctx = PipelineContext(db=db)
    await run_pipeline(build_autopilot_pipeline(config), state, ctx)
    telemetry = ctx.telemetry_json()
"""

from app.models import AutopilotConfig
from app.services.autopilot_pipeline.state import AutopilotState
from app.services.autopilot_pipeline.stages import (
    DirectiveGenerationStage,
    EvaluationStage,
    GapDetectionStage,
    GapTargetedGenerationStage,
    PersistenceStage,
    PipelineExecutionStage,
    ProposalCurationStage,
    QueryGenerationRouter,
    RefinementStage,
)


def build_autopilot_pipeline(config: AutopilotConfig) -> list:
    """The canonical autopilot-tick pipeline, in execution order.

    Query generation is a Pattern #6 BranchStage (`QueryGenerationRouter`) that
    dispatches between gap-targeted and directive-based prompt strategies by
    inspecting `state.gap_source`. The sub-pipelines each contain one stage
    today; adding a third strategy is a new branches[] entry, not an elif.
    """
    assert config is not None, "config is required"
    return [
        GapDetectionStage(),
        QueryGenerationRouter(),
        PipelineExecutionStage(),
        EvaluationStage(),
        RefinementStage(),
        ProposalCurationStage(),
        PersistenceStage(),
    ]


__all__ = [
    "AutopilotState",
    "build_autopilot_pipeline",
    "GapDetectionStage",
    "QueryGenerationRouter",
    "GapTargetedGenerationStage",
    "DirectiveGenerationStage",
    "PipelineExecutionStage",
    "EvaluationStage",
    "RefinementStage",
    "ProposalCurationStage",
    "PersistenceStage",
]
