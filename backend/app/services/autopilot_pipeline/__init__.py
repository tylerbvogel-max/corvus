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
    EvaluationStage,
    GapDetectionStage,
    PersistenceStage,
    PipelineExecutionStage,
    ProposalCurationStage,
    QueryGenerationStage,
    RefinementStage,
)


def build_autopilot_pipeline(config: AutopilotConfig) -> list:
    """The canonical autopilot-tick pipeline, in execution order.

    `config` is reserved for future per-tenant / per-config stage selection
    (e.g. skip evaluation when `eval_model` is the null sentinel). It is
    accepted today so callers do not need to migrate when that capability
    lands — current behavior is the same chain regardless of config.
    """
    assert config is not None, "config is required"
    return [
        GapDetectionStage(),
        QueryGenerationStage(),
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
    "QueryGenerationStage",
    "PipelineExecutionStage",
    "EvaluationStage",
    "RefinementStage",
    "ProposalCurationStage",
    "PersistenceStage",
]
