"""Admin endpoints for the provenance system."""

import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Neuron, NeuronSourceLink, SourceDocument
from app.services.provenance_service import (
    auto_link_from_references,
    bulk_recompute_authority,
    create_source_document,
    detect_stale_neurons,
    get_neuron_sources,
    get_source_document,
    get_source_document_by_canonical,
    get_source_neurons,
    link_neuron_to_source,
    list_source_documents,
    recompute_neuron_authority,
    seed_source_documents,
    supersede_source_document,
    unlink_neuron_from_source,
    update_source_document,
)

router = APIRouter(prefix="/admin", tags=["provenance"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class SourceDocumentCreate(BaseModel):
    canonical_id: str
    family: str
    authority_level: str
    version: Optional[str] = None
    status: str = "active"
    issuing_body: Optional[str] = None
    effective_date: Optional[str] = None
    url: Optional[str] = None
    notes: Optional[str] = None


class SourceDocumentUpdate(BaseModel):
    canonical_id: Optional[str] = None
    family: Optional[str] = None
    authority_level: Optional[str] = None
    version: Optional[str] = None
    status: Optional[str] = None
    issuing_body: Optional[str] = None
    effective_date: Optional[str] = None
    url: Optional[str] = None
    notes: Optional[str] = None


class SupersedeRequest(BaseModel):
    new_canonical_id: str
    new_version: Optional[str] = None
    family: Optional[str] = None
    authority_level: Optional[str] = None
    issuing_body: Optional[str] = None
    effective_date: Optional[str] = None
    url: Optional[str] = None
    notes: Optional[str] = None


class NeuronSourceLinkCreate(BaseModel):
    neuron_id: int
    source_document_id: int
    derivation_type: str = "references"
    section_ref: Optional[str] = None


class ReviewRequest(BaseModel):
    reviewed_by: Optional[str] = None


class SourceDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    canonical_id: str
    family: str
    version: Optional[str]
    status: str
    authority_level: str
    issuing_body: Optional[str]
    effective_date: Optional[str]
    url: Optional[str]
    notes: Optional[str]
    superseded_by_id: Optional[int]
    created_at: Optional[datetime.datetime]

class SourceDocumentDetail(SourceDocumentOut):
    linked_neurons_count: int = 0


class NeuronSourceLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    neuron_id: int
    source_document_id: int
    derivation_type: str
    section_ref: Optional[str]
    review_status: str
    flagged_at: Optional[datetime.datetime]
    reviewed_at: Optional[datetime.datetime]
    reviewed_by: Optional[str]
    link_origin: str
    created_at: Optional[datetime.datetime]

class AuthoritySummaryItem(BaseModel):
    authority_level: Optional[str]
    count: int


# ---------------------------------------------------------------------------
# Source Document CRUD
# ---------------------------------------------------------------------------

@router.post("/source-documents")
async def create_source_doc(body: SourceDocumentCreate, db: AsyncSession = Depends(get_db)):
    doc = await create_source_document(
        db,
        canonical_id=body.canonical_id,
        family=body.family,
        authority_level=body.authority_level,
        version=body.version,
        status=body.status,
        issuing_body=body.issuing_body,
        effective_date=body.effective_date,
        url=body.url,
        notes=body.notes,
    )
    return doc


@router.get("/source-documents")
async def list_source_docs(
    family: Optional[str] = None,
    status: Optional[str] = None,
    authority_level: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    docs = await list_source_documents(db, family=family, status=status, authority_level=authority_level)
    return docs


@router.get("/source-documents/{doc_id}")
async def get_source_doc(doc_id: int, db: AsyncSession = Depends(get_db)):
    doc = await get_source_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Source document not found")
    # Count linked neurons
    result = await db.execute(
        select(sa_func.count()).where(NeuronSourceLink.source_document_id == doc_id)
    )
    linked_count = result.scalar() or 0
    return {
        "id": doc.id,
        "canonical_id": doc.canonical_id,
        "family": doc.family,
        "version": doc.version,
        "status": doc.status,
        "authority_level": doc.authority_level,
        "issuing_body": doc.issuing_body,
        "effective_date": str(doc.effective_date) if doc.effective_date else None,
        "url": doc.url,
        "notes": doc.notes,
        "superseded_by_id": doc.superseded_by_id,
        "created_at": doc.created_at,
        "linked_neurons_count": linked_count,
    }


@router.put("/source-documents/{doc_id}")
async def update_source_doc(doc_id: int, body: SourceDocumentUpdate, db: AsyncSession = Depends(get_db)):
    kwargs = body.model_dump(exclude_unset=True)
    if not kwargs:
        raise HTTPException(status_code=400, detail="No fields to update")
    doc = await update_source_document(db, doc_id, **kwargs)
    if not doc:
        raise HTTPException(status_code=404, detail="Source document not found")
    return doc


@router.post("/source-documents/{doc_id}/supersede")
async def supersede_source_doc(doc_id: int, body: SupersedeRequest, db: AsyncSession = Depends(get_db)):
    extra = body.model_dump(exclude_unset=True)
    new_canonical_id = extra.pop("new_canonical_id")
    new_version = extra.pop("new_version", None)
    result = await supersede_source_document(
        db, doc_id, new_canonical_id, new_version=new_version, **extra
    )
    if not result:
        raise HTTPException(status_code=404, detail="Source document not found")
    return result


# ---------------------------------------------------------------------------
# Neuron-Source Links
# ---------------------------------------------------------------------------

@router.post("/neuron-source-links")
async def create_neuron_source_link(body: NeuronSourceLinkCreate, db: AsyncSession = Depends(get_db)):
    link = await link_neuron_to_source(
        db,
        neuron_id=body.neuron_id,
        source_document_id=body.source_document_id,
        derivation_type=body.derivation_type,
        section_ref=body.section_ref,
        link_origin="manual",
    )
    return link


@router.delete("/neuron-source-links/{link_id}")
async def delete_neuron_source_link(link_id: int, db: AsyncSession = Depends(get_db)):
    result = await unlink_neuron_from_source(db, link_id)
    if not result:
        raise HTTPException(status_code=404, detail="Link not found")
    return {"ok": True}


@router.put("/neuron-source-links/{link_id}/review")
async def review_neuron_source_link(link_id: int, body: ReviewRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(NeuronSourceLink).where(NeuronSourceLink.id == link_id)
    )
    link = result.scalar_one_or_none()
    if not link:
        raise HTTPException(status_code=404, detail="Link not found")
    link.review_status = "current"
    link.reviewed_at = datetime.datetime.utcnow()
    link.reviewed_by = body.reviewed_by
    await db.commit()
    await db.refresh(link)
    return link


# ---------------------------------------------------------------------------
# Provenance Bulk Operations
# ---------------------------------------------------------------------------

@router.post("/provenance/seed-sources")
async def seed_sources(db: AsyncSession = Depends(get_db)):
    result = await seed_source_documents(db)
    return result


@router.post("/provenance/auto-link")
async def auto_link(db: AsyncSession = Depends(get_db)):
    result = await auto_link_from_references(db)
    return result


@router.get("/provenance/stale")
async def stale_neurons(db: AsyncSession = Depends(get_db)):
    result = await detect_stale_neurons(db)
    return result


@router.get("/provenance/authority-summary")
async def authority_summary(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(
            Neuron.authority_level,
            sa_func.count().label("count"),
        )
        .where(Neuron.is_active == True)
        .group_by(Neuron.authority_level)
        .order_by(sa_func.count().desc())
    )
    rows = result.all()
    return [
        {"authority_level": row.authority_level, "count": row.count}
        for row in rows
    ]


@router.post("/provenance/recompute-authority")
async def recompute_authority(db: AsyncSession = Depends(get_db)):
    result = await bulk_recompute_authority(db)
    return result
