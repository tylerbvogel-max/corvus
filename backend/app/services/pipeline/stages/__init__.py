"""Concrete stage implementations for the query-prep pipeline.

Each stage is a thin wrapper that delegates to the existing service function;
the refactor is about observability and swappability, not re-implementing the
pipeline logic. Stages are listed in `build_default_pipeline()` in order.
"""

from app.services.pipeline.stages.structural_resolve_stage import StructuralResolveStage
from app.services.pipeline.stages.classify_stage import ClassifyStage
from app.services.pipeline.stages.prefilter_score_stage import PrefilterScoreStage
from app.services.pipeline.stages.continuity_boost_stage import ContinuityBoostStage
from app.services.pipeline.stages.spread_stage import SpreadActivationStage
from app.services.pipeline.stages.inhibitory_stage import InhibitoryStage
from app.services.pipeline.stages.regulatory_resolve_stage import RegulatoryResolveStage
from app.services.pipeline.stages.assemble_stage import AssembleStage


def build_default_pipeline() -> list:
    """The canonical query-prep pipeline, in execution order."""
    return [
        StructuralResolveStage(),
        ClassifyStage(),
        PrefilterScoreStage(),
        ContinuityBoostStage(),
        SpreadActivationStage(),
        InhibitoryStage(),
        RegulatoryResolveStage(),
        AssembleStage(),
    ]


__all__ = [
    "StructuralResolveStage",
    "ClassifyStage",
    "PrefilterScoreStage",
    "ContinuityBoostStage",
    "SpreadActivationStage",
    "InhibitoryStage",
    "RegulatoryResolveStage",
    "AssembleStage",
    "build_default_pipeline",
]
