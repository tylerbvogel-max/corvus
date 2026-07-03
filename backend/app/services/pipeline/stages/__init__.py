"""Concrete stage implementations for the query-prep pipeline.

Each stage is a thin wrapper that delegates to the existing service function;
the refactor is about observability and swappability, not re-implementing the
pipeline logic. Stages are listed in `build_default_pipeline()` in order.
"""

from types import MappingProxyType

from app.services.pipeline.stages.structural_resolve_stage import StructuralResolveStage
from app.services.pipeline.stages.classify_stage import (
    AdaptiveClassifyStage,
    CheapClassifyStage,
    ClassifyStage,
)
from app.services.pipeline.stages.prefilter_score_stage import PrefilterScoreStage
from app.services.pipeline.stages.continuity_boost_stage import ContinuityBoostStage
from app.services.pipeline.stages.spread_stage import SpreadActivationStage
from app.services.pipeline.stages.inhibitory_stage import InhibitoryStage
from app.services.pipeline.stages.engram_edge_boost_stage import EngramEdgeBoostStage
from app.services.pipeline.stages.regulatory_resolve_stage import RegulatoryResolveStage
from app.services.pipeline.stages.assemble_stage import AssembleStage


_CLASSIFY_STAGE_BY_MODE = MappingProxyType({
    "full": ClassifyStage,
    "cheap": CheapClassifyStage,
    "adaptive": AdaptiveClassifyStage,
})


def build_default_pipeline(recall_mode: str = "full") -> list:
    """The canonical query-prep pipeline, in execution order.

    recall_mode swaps only the classify stage: full (LLM), cheap
    (embed-only), or adaptive (cheap with LLM escalation).
    """
    assert recall_mode in _CLASSIFY_STAGE_BY_MODE, \
        f"recall_mode must be one of {sorted(_CLASSIFY_STAGE_BY_MODE)}, got {recall_mode!r}"
    classify_stage_cls = _CLASSIFY_STAGE_BY_MODE[recall_mode]
    return [
        StructuralResolveStage(),
        classify_stage_cls(),
        PrefilterScoreStage(),
        ContinuityBoostStage(),
        SpreadActivationStage(),
        InhibitoryStage(),
        EngramEdgeBoostStage(),
        RegulatoryResolveStage(),
        AssembleStage(),
    ]


__all__ = [
    "StructuralResolveStage",
    "ClassifyStage",
    "CheapClassifyStage",
    "AdaptiveClassifyStage",
    "PrefilterScoreStage",
    "ContinuityBoostStage",
    "SpreadActivationStage",
    "InhibitoryStage",
    "EngramEdgeBoostStage",
    "RegulatoryResolveStage",
    "AssembleStage",
    "build_default_pipeline",
]
