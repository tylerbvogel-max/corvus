"""Document ingestion router: upload, parse, extract, and propose neurons from documents.

Endpoints under /admin/documents/.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session, get_db
from app.models import DocumentIngestJob
from app.services.document_parser import parse_document

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/documents", tags=["document-ingest"])

# Supported file extensions
SUPPORTED_FORMATS = frozenset({"pdf", "docx", "doc", "html", "htm", "txt"})
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB


def _short_id() -> str:
    """Generate a short unique ID for jobs."""
    return uuid.uuid4().hex[:12]


def _get_format(filename: str) -> str:
    """Extract and validate file format from filename."""
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in SUPPORTED_FORMATS:
        raise HTTPException(
            400,
            f"Unsupported format: .{ext}. Supported: {', '.join(sorted(SUPPORTED_FORMATS))}",
        )
    return ext


def _job_to_dict(job: DocumentIngestJob) -> dict:
    """Serialize a job to a JSON-friendly dict."""
    return {
        "id": job.id,
        "status": job.status,
        "step": job.step,
        "filename": job.filename,
        "file_format": job.file_format,
        "file_size_bytes": job.file_size_bytes,
        "total_pages": job.total_pages,
        "title": job.title,
        "source_type": job.source_type,
        "authority_level": job.authority_level,
        "citation": job.citation,
        "source_url": job.source_url,
        "department": job.department,
        "role_key": job.role_key,
        "total_sections": job.total_sections,
        "current_section": job.current_section,
        "proposal_ids": json.loads(job.proposal_ids_json) if job.proposal_ids_json else [],
        "cost_usd": job.cost_usd,
        "input_tokens": job.input_tokens,
        "output_tokens": job.output_tokens,
        "model": job.model,
        "duplicates_flagged": job.duplicates_flagged,
        "errors": json.loads(job.errors_json) if job.errors_json else [],
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(""),
    source_type: str = Form("operational"),
    authority_level: str = Form("guidance"),
    citation: str = Form(""),
    source_url: str = Form(""),
    department: str = Form(""),
    role_key: str = Form(""),
    model: str = Form("opus"),
):
    """Upload a document and start the two-pass ingestion pipeline.

    Pass 1 (structure analysis) runs synchronously and returns the job with structure.
    Pass 2 (LLM extraction) runs as a background task.
    """
    assert file.filename, "filename is required"
    file_format = _get_format(file.filename)

    content = await file.read()
    if not content:
        raise HTTPException(400, "Empty file")
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(400, f"File too large: {len(content)} bytes (max {MAX_FILE_SIZE})")

    job_id = _short_id()

    # Pass 1: Structure extraction (synchronous, no LLM cost)
    try:
        full_text, structure = parse_document(content, file_format)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        logger.error("Document parsing failed: %s", exc)
        raise HTTPException(500, f"Failed to parse document: {exc}")

    if not full_text.strip():
        raise HTTPException(400, "No text could be extracted from the document")

    doc_title = title.strip() or structure.title or file.filename

    async with async_session() as db:
        job = DocumentIngestJob(
            id=job_id,
            status="analyzing",
            step=f"Structure analysis complete: {len(structure.sections)} sections detected",
            filename=file.filename,
            file_format=file_format,
            file_size_bytes=len(content),
            total_pages=structure.total_pages,
            title=doc_title,
            source_type=source_type,
            authority_level=authority_level,
            citation=citation,
            source_url=source_url or None,
            department=department or None,
            role_key=role_key or None,
            structure_json=json.dumps(structure.to_dict()),
            extracted_text=full_text,
            total_sections=len(structure.sections),
            current_section=0,
            model=model,
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        result = _job_to_dict(job)

    # Pass 2: LLM extraction (background task)
    asyncio.create_task(_run_extraction_background(job_id))

    return result


async def _run_extraction_background(job_id: str) -> None:
    """Wrapper to run extraction and handle top-level errors."""
    try:
        from app.services.document_extractor import run_document_extraction
        await run_document_extraction(job_id)
    except Exception as exc:
        logger.error("Background extraction failed for job %s: %s", job_id, exc, exc_info=True)
        try:
            async with async_session() as db:
                job = await db.get(DocumentIngestJob, job_id)
                if job and job.status not in ("done", "cancelled"):
                    job.status = "error"
                    job.step = f"Error: {str(exc)[:180]}"
                    errors = json.loads(job.errors_json) if job.errors_json else []
                    errors.append(str(exc))
                    job.errors_json = json.dumps(errors)
                    await db.commit()
        except Exception:
            logger.error("Failed to update job %s error status", job_id, exc_info=True)


@router.get("/")
async def list_jobs(
    db: AsyncSession = Depends(get_db),
    status: str | None = None,
    limit: int = 50,
):
    """List document ingest jobs, newest first."""
    assert 1 <= limit <= 200, "limit must be between 1 and 200"

    query = select(DocumentIngestJob).order_by(DocumentIngestJob.created_at.desc())
    if status:
        query = query.where(DocumentIngestJob.status == status)
    query = query.limit(limit)

    result = await db.execute(query)
    jobs = result.scalars().all()
    return [_job_to_dict(j) for j in jobs]


@router.get("/{job_id}")
async def get_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a single job's status and progress."""
    job = await db.get(DocumentIngestJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _job_to_dict(job)


@router.get("/{job_id}/structure")
async def get_job_structure(
    job_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get the extracted document structure tree."""
    job = await db.get(DocumentIngestJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if not job.structure_json:
        raise HTTPException(400, "Structure not yet available")
    return json.loads(job.structure_json)


@router.post("/{job_id}/cancel")
async def cancel_job(
    job_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Cancel an in-progress job. The extraction loop checks for cancellation between sections."""
    job = await db.get(DocumentIngestJob, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status in ("done", "error"):
        raise HTTPException(400, f"Cannot cancel job in status '{job.status}'")

    job.status = "cancelled"
    job.step = "Cancelled by user"
    await db.commit()
    await db.refresh(job)
    return _job_to_dict(job)
