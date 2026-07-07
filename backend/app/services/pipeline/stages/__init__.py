"""Concrete stage implementations for the query-prep pipeline.

Each stage is a thin wrapper that delegates to the existing service function;
the refactor is about observability and swappability, not re-implementing the
pipeline logic. Stages are listed in `build_default_pipeline()` in order.
"""

from types import MappingProxyType

from app.services.pipeline.stages.structural_resolve_stage import StructuralResolveStage
from app.services.pipeline.stages.classify_stage import CheapClassifyStage
from app.services.pipeline.stages.prefilter_score_stage import PrefilterScoreStage
from app.services.pipeline.stages.continuity_boost_stage import ContinuityBoostStage
from app.services.pipeline.stages.spread_stage import SpreadActivationStage
from app.services.pipeline.stages.inhibitory_stage import InhibitoryStage
from app.services.pipeline.stages.engram_edge_boost_stage import EngramEdgeBoostStage
from app.services.pipeline.stages.regulatory_resolve_stage import RegulatoryResolveStage
from app.services.pipeline.stages.assemble_stage import AssembleStage


_CLASSIFY_STAGE_BY_MODE = MappingProxyType({
    "cheap": CheapClassifyStage,
})


def build_default_pipeline(recall_mode: str = "cheap") -> list:
    """The canonical query-prep pipeline, in execution order.

    Classification is embed-only (cheap): the per-query LLM classify was removed
    as lower-performance than the free neighbor-vote. `recall_mode` is retained for
    forward-compatibility but only "cheap" is currently registered.
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
    "CheapClassifyStage",
    "PrefilterScoreStage",
    "ContinuityBoostStage",
    "SpreadActivationStage",
    "InhibitoryStage",
    "EngramEdgeBoostStage",
    "RegulatoryResolveStage",
    "AssembleStage",
    "build_default_pipeline",
]
