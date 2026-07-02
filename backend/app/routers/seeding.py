"""Emergent seeding router — ingest-first structure discovery.

Blank-canvas onboarding for a new org: ingest corpus (document ingest),
bootstrap prior edges (kNN + same-source co-occurrence), discover regions
via Leiden + one bounded LLM labeling call, and re-derive edge types after
region assignment. Structure mutations are staged as human-approval
proposals — nothing here writes taxonomy directly.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import seeding_service


router = APIRouter(prefix="/admin/seeding", tags=["seeding"])


class KnnEdgesRequest(BaseModel):
    k: int = Field(6, ge=1, le=25)
    min_similarity: float = Field(0.35, gt=0.0, lt=1.0)
    dry_run: bool = False


class CooccurrenceRequest(BaseModel):
    window: int = Field(3, ge=1, le=10)
    max_pairs_per_doc: int = Field(200, ge=1, le=2000)
    dry_run: bool = False


class DiscoverRegionsRequest(BaseModel):
    resolution: float = Field(1.0, gt=0.0, le=5.0)
    min_size: int = Field(3, ge=2, le=100)
    only_unregioned: bool = True
    model: str = "opus"


@router.post("/knn-edges")
async def bootstrap_knn(
    req: KnnEdgesRequest, db: AsyncSession = Depends(get_db),
) -> dict:
    """Bootstrap edges from embedding kNN so day-one spread activation works."""
    result = await seeding_service.bootstrap_knn_edges(
        db, k=req.k, min_similarity=req.min_similarity, dry_run=req.dry_run,
    )
    if not req.dry_run:
        await db.commit()
    return result


@router.post("/cooccurrence-edges")
async def bootstrap_cooccurrence(
    req: CooccurrenceRequest, db: AsyncSession = Depends(get_db),
) -> dict:
    """Bootstrap edges from same-source-document co-occurrence."""
    result = await seeding_service.bootstrap_cooccurrence_edges(
        db, window=req.window, max_pairs_per_doc=req.max_pairs_per_doc,
        dry_run=req.dry_run,
    )
    if not req.dry_run:
        await db.commit()
    return result


@router.post("/discover-regions")
async def discover_regions(
    req: DiscoverRegionsRequest, db: AsyncSession = Depends(get_db),
) -> dict:
    """Leiden + LLM labeling -> human-approval region proposals."""
    result = await seeding_service.discover_regions(
        db, resolution=req.resolution, min_size=req.min_size,
        only_unregioned=req.only_unregioned, model=req.model,
    )
    await db.commit()
    return result


@router.post("/retype-edges")
async def retype_edges(db: AsyncSession = Depends(get_db)) -> dict:
    """Re-derive stellate/pyramidal edge types after region assignment."""
    retyped = await seeding_service.retype_edges_by_region(db)
    await db.commit()
    return {"edges_retyped": retyped}
