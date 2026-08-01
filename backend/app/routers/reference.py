"""Reference memory admin surface (mind-reference-class).

Upload a document into the Library as reference memory; list documents
with their live neuron counts; revoke ("forget the book") in one
operation. Memory-surface gated like /recall and /remember — knowledge
tenants (aero/flow) keep their own document-ingest pipeline.
"""

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, get_db
from app.models import (
    DocumentIngestJob, Neuron, NeuronSourceLink, SourceDocument,
)
from app.routers.document_ingest import MAX_FILE_SIZE, _get_format, _job_to_dict
from app.services.document_parser import parse_document
from app.services.reference_class import REFERENCE_AUTHORITY_CAP, REFERENCE_REGION

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/reference", tags=["reference-memory"])


async def _resolve_document(db: AsyncSession, canonical_id: str) -> SourceDocument:
    doc = (await db.execute(
        select(SourceDocument).where(
            SourceDocument.canonical_id == canonical_id).limit(1)
    )).scalar_one_or_none()
    if doc is None:
        raise HTTPException(404, f"no reference document '{canonical_id}'")
    return doc


async def _neuron_counts(db: AsyncSession, doc_ids: list[int]) -> dict[int, dict]:
    """source_document_id → {total, active} linked-neuron counts."""
    if not doc_ids:
        return {}
    rows = (await db.execute(
        select(
            NeuronSourceLink.source_document_id,
            func.count(Neuron.id),
            func.count(Neuron.id).filter(Neuron.is_active.is_(True)),
        ).join(Neuron, Neuron.id == NeuronSourceLink.neuron_id)
        .where(NeuronSourceLink.source_document_id.in_(doc_ids))
        .group_by(NeuronSourceLink.source_document_id)
    )).all()
    return {r[0]: {"total": r[1], "active": r[2]} for r in rows}


def _doc_to_dict(doc: SourceDocument, counts: dict | None) -> dict:
    return {
        "id": doc.id, "canonical_id": doc.canonical_id,
        "version": doc.version, "status": doc.status,
        "authority_level": doc.authority_level, "url": doc.url,
        "notes": doc.notes, "superseded_by_id": doc.superseded_by_id,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "neurons": counts or {"total": 0, "active": 0},
    }


@router.post("/documents")
async def upload_reference_document(
    file: UploadFile = File(...),
    canonical_id: str = Form(...),
    version: str = Form(""),
    url: str = Form(""),
    notes: str = Form(""),
):
    """Ingest a document into the Library as reference memory.

    Parses synchronously, creates the SourceDocument (superseding any
    prior active version with the same canonical_id — its neurons are
    revoked), and runs Opus extraction in the background. Authority is
    always informational; the reference class cannot assert more.
    """
    from app.services.reference_ingest import (
        create_or_supersede_source_document, ensure_library_region,
    )
    assert file.filename, "filename is required"
    if not canonical_id.strip():
        raise HTTPException(400, "canonical_id is required")
    file_format = _get_format(file.filename)
    content = await file.read()
    if not content or len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, f"empty or oversized file ({len(content)} bytes)")
    try:
        full_text, structure = parse_document(content, file_format)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if not full_text.strip():
        raise HTTPException(400, "no text could be extracted")

    job_id = uuid.uuid4().hex[:12]
    async with async_session() as db:
        await ensure_library_region(db)
        doc, superseded = await create_or_supersede_source_document(
            db, canonical_id=canonical_id.strip(),
            version=version.strip() or None, url=url.strip() or None,
            notes=notes.strip() or None)
        job = DocumentIngestJob(
            id=job_id, status="analyzing",
            step=f"parsed: {len(structure.sections)} sections",
            filename=file.filename, file_format=file_format,
            file_size_bytes=len(content), total_pages=structure.total_pages,
            title=structure.title or canonical_id,
            source_type="reference",
            authority_level=REFERENCE_AUTHORITY_CAP,
            citation=canonical_id.strip(), department=REFERENCE_REGION,
            structure_json=json.dumps(structure.to_dict()),
            extracted_text=full_text,
            total_sections=len(structure.sections), current_section=0,
            model="opus")
        db.add(job)
        await db.commit()
        doc_id = doc.id
        result = {"job": _job_to_dict(job),
                  "source_document_id": doc_id,
                  "superseded": superseded}
    asyncio.create_task(_run_ingest_background(job_id, doc_id))
    return result


async def _run_ingest_background(job_id: str, doc_id: int) -> None:
    from app.services.reference_ingest import run_reference_ingest
    try:
        await run_reference_ingest(job_id, doc_id)
    except Exception as exc:  # top-level task boundary — must not die silent
        logger.error("reference ingest %s failed: %s", job_id, exc, exc_info=True)
        try:
            async with async_session() as db:
                job = await db.get(DocumentIngestJob, job_id)
                if job and job.status not in ("done", "cancelled"):
                    job.status = "error"
                    job.step = f"Error: {str(exc)[:180]}"
                    await db.commit()
        except Exception:
            logger.error("failed to mark job %s errored", job_id, exc_info=True)


@router.get("/documents")
async def list_reference_documents(db: AsyncSession = Depends(get_db)):
    """All reference documents with live/total neuron counts."""
    docs = (await db.execute(
        select(SourceDocument).where(SourceDocument.family == "reference")
        .order_by(SourceDocument.created_at.desc())
    )).scalars().all()
    counts = await _neuron_counts(db, [d.id for d in docs])
    return [_doc_to_dict(d, counts.get(d.id)) for d in docs]


@router.get("/documents/{canonical_id}")
async def get_reference_document(
    canonical_id: str, db: AsyncSession = Depends(get_db),
):
    doc = await _resolve_document(db, canonical_id)
    counts = await _neuron_counts(db, [doc.id])
    return _doc_to_dict(doc, counts.get(doc.id))


@router.post("/documents/{canonical_id}/revoke")
async def revoke_reference_document_endpoint(
    canonical_id: str, db: AsyncSession = Depends(get_db),
):
    """'Forget the book': deactivate every reference neuron derived from
    this document. Provenance rows and change events persist (deactivate,
    never delete); human-promoted graduates are retained and reported."""
    from app.services.reference_ingest import revoke_reference_document
    doc = await _resolve_document(db, canonical_id)
    if doc.status != "active":
        raise HTTPException(400, f"document is already {doc.status}")
    report = await revoke_reference_document(
        db, doc, reason=f"operator revoke of {canonical_id}")
    await db.commit()
    return report
