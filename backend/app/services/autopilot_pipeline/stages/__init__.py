"""Concrete stage implementations for the autopilot-tick pipeline.

Each stage is a thin wrapper around an existing autopilot helper — the
Pattern #8 refactor is about observability and swappability (telemetry,
short-circuit, composable stage chain), not re-implementing the tick logic.
Stages are listed in `build_autopilot_pipeline()` in execution order.
"""

from app.services.autopilot_pipeline.stages.evaluation_stage import EvaluationStage
from app.services.autopilot_pipeline.stages.gap_detection_stage import GapDetectionStage
from app.services.autopilot_pipeline.stages.persistence_stage import PersistenceStage
from app.services.autopilot_pipeline.stages.pipeline_execution_stage import PipelineExecutionStage
from app.services.autopilot_pipeline.stages.proposal_curation_stage import ProposalCurationStage
from app.services.autopilot_pipeline.stages.query_generation_stage import (
    DirectiveGenerationStage,
    GapTargetedGenerationStage,
    QueryGenerationRouter,
)
from app.services.autopilot_pipeline.stages.refinement_stage import RefinementStage


__all__ = [
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
