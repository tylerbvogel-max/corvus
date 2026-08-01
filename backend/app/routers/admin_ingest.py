"""Source ingest: extract a document or URL, propose neurons, apply them.

Extracted from ``admin.py`` by roadmap record ``durability-file-size-seams``
(04c). One job in two shapes: a single-shot path for a document that fits in
one model call, and a chunked batch path with a resumable job record for one
that does not. Both end in the same place — proposals reviewed by an operator,
then applied.

Every neuron this module creates goes through ``action_bus.submit`` with
kind ``neuron.create``, which is why moving it did not touch the CREATE half
of ``architecture/graph_writers.json``: the register lists governed writers,
and the governed writer here is ``app/services/actions/neuron_create.py``, not
this router. The classification pass confirmed that before the move rather
than assuming it — this file constructs no ``Neuron`` and issues no INSERT.
"""

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session
from app.models import Neuron, SystemState, EmergentQueue, BatchJob
from app.middleware.rbac import UserIdentity
from app.services import action_bus

router = APIRouter(prefix="/admin", tags=["admin"])

_ADMIN_ACTOR = UserIdentity(user_id="admin-ingest", role="admin", source="system")


def _extract_pdf_pages(
    pdf_bytes: bytes, page_start: int, page_end: int | None,
) -> tuple[str, int, int, int]:
    import tempfile
    import fitz
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=True) as tmp:
        tmp.write(pdf_bytes)
        tmp.flush()
        doc = fitz.open(tmp.name)
        total_pages = len(doc)
        end = min(page_end or total_pages, total_pages)
        start = max(page_start - 1, 0)
        pages_text = []
        for i in range(start, end):
            page_text = doc[i].get_text()
            if page_text.strip():
                pages_text.append(f"--- Page {i + 1} ---\n{page_text}")
        doc.close()
    return "\n\n".join(pages_text), total_pages, start + 1, end


async def _extract_from_file(
    file: UploadFile, page_start: int, page_end: int | None,
) -> tuple[str, int, str]:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    if file.filename.lower().endswith('.pdf'):
        text, total_pages, start_disp, end_disp = _extract_pdf_pages(
            content, page_start, page_end,
        )
        source_info = f"PDF: {file.filename} (pages {start_disp}-{end_disp} of {total_pages})"
        return text, total_pages, source_info

    text = content.decode('utf-8', errors='replace')
    return text, 0, f"File: {file.filename}"


async def _extract_from_url(
    url: str, page_start: int, page_end: int | None,
) -> tuple[str, int, str]:
    import httpx
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await client.get(url, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; Corvus/1.0)',
            })
            resp.raise_for_status()

            content_type = resp.headers.get('content-type', '')
            if 'pdf' in content_type or url.lower().endswith('.pdf'):
                text, total_pages, start_disp, end_disp = _extract_pdf_pages(
                    resp.content, page_start, page_end,
                )
                return text, total_pages, f"PDF from URL (pages {start_disp}-{end_disp} of {total_pages})"

            return resp.text, 0, f"URL: {url}"
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"URL returned {e.response.status_code}. Site may block automated access — try downloading the file and uploading it instead.")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch URL: {e}")


@router.post("/extract-source")
async def extract_source(
    url: str | None = None,
    file: UploadFile | None = File(None),
    page_start: int = 1,
    page_end: int | None = None,
):
    """Extract text from a PDF file upload or URL for use in ingest-source.

    Supports:
      - PDF file upload (multipart form)
      - URL to a PDF or HTML page
      - Page range selection for large PDFs
    Returns extracted text, page count, and character count.
    """
    if file and file.filename:
        text, total_pages, source_info = await _extract_from_file(file, page_start, page_end)
    elif url:
        text, total_pages, source_info = await _extract_from_url(url, page_start, page_end)
    else:
        raise HTTPException(status_code=400, detail="Provide either a file upload or a URL")

    return {
        "text": text,
        "char_count": len(text),
        "total_pages": total_pages,
        "source_info": source_info,
    }


def _validate_ingest_body(body: dict) -> tuple[str, str, str, str | None, str | None, int | None]:
    source_text = body.get("source_text", "").strip()
    citation = body.get("citation", "").strip()
    source_type = body.get("source_type", "regulatory_primary")
    department = body.get("department")
    role_key = body.get("role_key")
    queue_entry_id = body.get("queue_entry_id")

    if not source_text:
        raise HTTPException(status_code=400, detail="source_text is required")
    if not citation:
        raise HTTPException(status_code=400, detail="citation is required")
    if not department:
        raise HTTPException(status_code=400, detail="department is required — neurons without a department become orphaned")

    return source_text, citation, source_type, department, role_key, queue_entry_id


async def _find_parent_neuron(db: AsyncSession, role_key: str | None, department: str | None):
    parent_neuron = None
    if role_key:
        result = await db.execute(
            select(Neuron).where(Neuron.role_key == role_key, Neuron.layer == 1, Neuron.is_active == True)
        )
        parent_neuron = result.scalar_one_or_none()
    if not parent_neuron and department:
        result = await db.execute(
            select(Neuron).where(Neuron.department == department, Neuron.layer == 0, Neuron.is_active == True)
        )
        parent_neuron = result.scalar_one_or_none()
    return parent_neuron


async def _build_parent_context(db: AsyncSession, parent_neuron) -> str:
    if not parent_neuron:
        return ""
    children_result = await db.execute(
        select(Neuron.id, Neuron.label, Neuron.layer, Neuron.node_type)
        .where(Neuron.parent_id == parent_neuron.id, Neuron.is_active == True)
        .order_by(Neuron.layer, Neuron.label)
        .limit(30)
    )
    children = children_result.all()
    if not children:
        return ""
    context_info = f"\n\nExisting children of '{parent_neuron.label}' (layer {parent_neuron.layer}):\n"
    for c in children:
        context_info += f"  - [{c.node_type} L{c.layer}] {c.label} (id={c.id})\n"
    return context_info


def _build_ingest_prompts(
    citation: str, source_type: str, department: str | None,
    role_key: str | None, source_text: str, context_info: str,
) -> tuple[str, str]:
    system_prompt = f"""You are a knowledge graph architect for Corvus, a 6-layer neuron hierarchy:
L0=Department, L1=Role, L2=Task, L3=System, L4=Decision, L5=Output.

Your job is to segment source material into neuron proposals that fit the hierarchy.
Each neuron should be a self-contained knowledge unit with clear content and a concise summary.

Rules:
- Create neurons at layers 3-5 (System, Decision, Output) — these are knowledge nodes
- Each neuron needs: label (short name), content (detailed knowledge), summary (1-sentence)
- Content should be substantive (50-300 words) and self-contained
- Avoid duplicating what might already exist — check the existing children listed below
- Group related content into single neurons rather than making many tiny ones
- Include specific references, numbers, thresholds, and procedures from the source
{context_info}

Respond with a JSON array of neuron proposals. Each proposal:
{{
  "layer": 3|4|5,
  "node_type": "system"|"decision"|"output",
  "label": "Short descriptive name",
  "content": "Detailed content from the source material...",
  "summary": "One-sentence summary",
  "reason": "Why this neuron is needed"
}}

Output ONLY the JSON array, no other text."""

    user_msg = f"""Citation: {citation}
Source type: {source_type}
Department: {department or 'unspecified'}
Role: {role_key or 'unspecified'}

Source material to segment into neurons:

{source_text[:8000]}"""

    return system_prompt, user_msg


def _parse_proposals(response_text: str) -> list[dict]:
    json_match = re.search(r'\[.*\]', response_text, re.DOTALL)
    if not json_match:
        raise HTTPException(
            status_code=500,
            detail=f"LLM did not return valid JSON array. Response starts with: {response_text[:300]}",
        )
    try:
        return json.loads(json_match.group())
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to parse proposals JSON: {e}. Raw starts with: {json_match.group()[:300]}",
        )


def _enrich_proposals(
    proposals: list[dict], department: str | None, role_key: str | None,
    parent_neuron, source_type: str, citation: str, body: dict,
) -> None:
    for p in proposals:
        p["department"] = department
        p["role_key"] = role_key
        p["parent_id"] = parent_neuron.id if parent_neuron else None
        p["source_type"] = source_type
        p["citation"] = citation
        p["source_url"] = body.get("source_url")
        p["effective_date"] = body.get("effective_date")


@router.post("/ingest-source")
async def ingest_source(
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Accept source text + metadata and use LLM to segment into neuron proposals.

    Request body:
      - source_text: str (the raw source content to segment)
      - citation: str (e.g. "FAR 31.205-6")
      - source_type: str ("regulatory_primary" | "regulatory_interpretive" | "technical_primary" | "technical_pattern")
      - source_url: str | None
      - effective_date: str | None (YYYY-MM-DD)
      - department: str | None (target department)
      - role_key: str | None (target role)
      - queue_entry_id: int | None (if resolving an emergent queue entry)
    """
    from app.services.llm_provider import llm_chat

    source_text, citation, source_type, department, role_key, queue_entry_id = _validate_ingest_body(body)
    parent_neuron = await _find_parent_neuron(db, role_key, department)
    context_info = await _build_parent_context(db, parent_neuron)
    system_prompt, user_msg = _build_ingest_prompts(
        citation, source_type, department, role_key, source_text, context_info,
    )

    try:
        result = await llm_chat(system_prompt, user_msg, max_tokens=8192, model="sonnet")
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"LLM call failed: {e}")

    proposals = _parse_proposals(result["text"].strip())
    _enrich_proposals(proposals, department, role_key, parent_neuron, source_type, citation, body)

    return {
        "proposals": proposals,
        "count": len(proposals),
        "citation": citation,
        "source_type": source_type,
        "department": department,
        "role_key": role_key,
        "parent_id": parent_neuron.id if parent_neuron else None,
        "parent_label": parent_neuron.label if parent_neuron else None,
        "queue_entry_id": queue_entry_id,
        "llm_cost": {
            "input_tokens": result["input_tokens"],
            "output_tokens": result["output_tokens"],
            "cost_usd": result["cost_usd"],
        },
    }


def _validate_proposal_placements(proposals: list[dict]) -> None:
    unplaced = []
    for i, p in enumerate(proposals):
        missing = []
        if not p.get("department"):
            missing.append("department")
        if not p.get("role_key"):
            missing.append("role_key")
        if not p.get("parent_id"):
            missing.append("parent_id")
        if missing:
            unplaced.append({"index": i, "label": p.get("label", "(no label)"), "missing": missing})

    if unplaced:
        raise HTTPException(status_code=400, detail={
            "message": f"{len(unplaced)} of {len(proposals)} proposals lack required placement fields and would be orphaned",
            "unplaced": unplaced,
        })


async def _create_neurons_from_proposals(
    db: AsyncSession, proposals: list[dict], query_count: int,
) -> list[int]:
    created_ids: list[int] = []
    for p in proposals:
        spec = {
            "parent_id": p.get("parent_id"),
            "layer": p.get("layer", 3),
            "node_type": p.get("node_type", "system"),
            "label": p.get("label", ""),
            "content": p.get("content", ""),
            "summary": p.get("summary", ""),
            "department": p.get("department"),
            "role_key": p.get("role_key"),
            "source_origin": "ingest",
            "source_type": p.get("source_type", "operational"),
            "citation": p.get("citation"),
            "source_url": p.get("source_url"),
        }
        result = await action_bus.submit(
            db=db, kind="neuron.create", actor=_ADMIN_ACTOR,
            actor_type="user",
            input_data={
                "spec": spec,
                "total_queries": query_count,
                "reason": f"Ingested from {p.get('citation', 'unknown source')}",
            },
        )
        assert result.state == "applied", (
            f"neuron.create failed during admin ingest: {result.error}"
        )
        created_ids.append(result.payload["neuron_id"])
    return created_ids


async def _resolve_queue_entry(
    db: AsyncSession, queue_entry_id: int, created_ids: list[int],
) -> None:
    entry = await db.get(EmergentQueue, queue_entry_id)
    if entry:
        entry.status = "resolved"
        entry.resolved_neuron_id = created_ids[0]
        entry.resolved_at = datetime.now()
        entry.notes = f"Resolved via ingest: created {len(created_ids)} neurons"


async def _create_referencing_edges(
    db: AsyncSession, queue_entry_id: int, created_ids: list[int], query_count: int,
) -> int:
    entry = await db.get(EmergentQueue, queue_entry_id)
    if not entry:
        return 0

    edges_created = 0
    referencing_ids = json.loads(entry.detected_in_neuron_ids or "[]")
    for ref_id in referencing_ids:
        for new_id in created_ids:
            if ref_id == new_id:
                continue
            existing = await db.execute(
                select(NeuronEdge).where(
                    NeuronEdge.source_id == ref_id,
                    NeuronEdge.target_id == new_id,
                )
            )
            if existing.scalar_one_or_none():
                continue
            result = await action_bus.submit(
                db=db, kind="edge.link", actor=_ADMIN_ACTOR,
                actor_type="user",
                input_data={
                    "source_id": ref_id,
                    "target_id": new_id,
                    "weight": 0.3,
                    "co_fire_count": 1,
                    "source": "emergent_queue",
                    "last_updated_query": query_count,
                },
            )
            if result.state == "applied":
                edges_created += 1
    return edges_created


@router.post("/ingest-source/apply")
async def apply_ingest_source(
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Apply approved neuron proposals from ingest-source.

    Request body:
      - proposals: list of approved neuron proposals (from ingest-source response)
      - queue_entry_id: int | None (emergent queue entry to resolve)
    """
    proposals = body.get("proposals", [])
    queue_entry_id = body.get("queue_entry_id")

    if not proposals:
        raise HTTPException(status_code=400, detail="No proposals to apply")

    _validate_proposal_placements(proposals)

    state = (await db.execute(select(SystemState).where(SystemState.id == 1))).scalar_one_or_none()
    query_count = state.total_queries if state else 0

    created_ids = await _create_neurons_from_proposals(db, proposals, query_count)

    edges_created = 0
    if queue_entry_id and created_ids:
        await _resolve_queue_entry(db, queue_entry_id, created_ids)
        edges_created = await _create_referencing_edges(db, queue_entry_id, created_ids, query_count)

    await db.commit()

    return {
        "status": "applied",
        "neurons_created": len(created_ids),
        "neuron_ids": created_ids,
        "edges_created": edges_created,
        "queue_entry_resolved": queue_entry_id is not None,
    }


# ── Batch Ingestion (background chunked processing) ──

# In-memory cancel flags — lightweight, no need to persist
_batch_cancel_flags: dict[str, bool] = {}


def _chunk_text(text: str, chunk_size: int = 18000, overlap: int = 300) -> list[str]:
    """Split text into overlapping chunks, breaking at paragraph boundaries."""
    chunks = []
    pos = 0
    while pos < len(text):
        end = min(pos + chunk_size, len(text))
        # Try to break at a paragraph boundary
        if end < len(text):
            break_at = text.rfind('\n\n', pos + chunk_size // 2, end)
            if break_at > pos:
                end = break_at
        chunks.append(text[pos:end].strip())
        pos = end - overlap if end < len(text) else end
    return [c for c in chunks if c]


def _build_batch_system_prompt(context_info: str) -> str:
    return f"""You are a knowledge graph architect for Corvus, a 6-layer neuron hierarchy:
L0=Department, L1=Role, L2=Task, L3=System, L4=Decision, L5=Output.

Your job is to segment source material into neuron proposals that fit the hierarchy.
Each neuron should be a self-contained knowledge unit with clear content and a concise summary.

Rules:
- Create neurons at layers 3-5 (System, Decision, Output) — these are knowledge nodes
- Each neuron needs: label (short name), content (detailed knowledge), summary (1-sentence)
- Content should be substantive (50-300 words) and self-contained
- Avoid duplicating what might already exist — check the existing children listed below
- Group related content into single neurons rather than making many tiny ones
- Include specific references, numbers, thresholds, and procedures from the source
- You are processing one chunk of a larger document — focus on this chunk's content
{context_info}

Respond with a JSON array of neuron proposals. Each proposal:
{{
  "layer": 3|4|5,
  "node_type": "system"|"decision"|"output",
  "label": "Short descriptive name",
  "content": "Detailed content from the source material...",
  "summary": "One-sentence summary",
  "reason": "Why this neuron is needed"
}}

Output ONLY the JSON array, no other text."""


def _create_batch_job_record(
    job_id: str,
    chunks: list[str],
    source_text: str,
    citation: str,
    source_type: str,
    department: str | None,
    role_key: str | None,
    parent_neuron,
    queue_entry_id: int | None,
    model: str,
    body: dict,
    system_prompt: str,
) -> BatchJob:
    return BatchJob(
        id=job_id,
        status="running",
        step="Starting...",
        total_chunks=len(chunks),
        current_chunk=0,
        citation=citation,
        source_type=source_type,
        department=department,
        role_key=role_key,
        parent_id=parent_neuron.id if parent_neuron else None,
        parent_label=parent_neuron.label if parent_neuron else None,
        queue_entry_id=queue_entry_id,
        total_chars=len(source_text),
        model=model,
        source_url=body.get("source_url"),
        effective_date=body.get("effective_date"),
        chunks_json=json.dumps(chunks),
        system_prompt=system_prompt,
    )


async def _update_batch_job(job_id: str, **kwargs):
    """Update batch job fields in the database."""
    async with async_session() as db:
        job = await db.get(BatchJob, job_id)
        if job:
            for k, v in kwargs.items():
                setattr(job, k, v)
            await db.commit()


async def _load_batch_job_state(job_id: str) -> dict | None:
    async with async_session() as db:
        job = await db.get(BatchJob, job_id)
        if not job:
            return None
        return {
            "chunks": json.loads(job.chunks_json or "[]"),
            "system_prompt": job.system_prompt or "",
            "citation": job.citation,
            "source_type": job.source_type,
            "department": job.department,
            "role_key": job.role_key,
            "parent_id": job.parent_id,
            "source_url": job.source_url,
            "effective_date": job.effective_date,
            "model": job.model,
            "all_proposals": json.loads(job.proposals_json or "[]"),
            "errors": json.loads(job.errors_json or "[]"),
            "total_cost": job.cost_usd,
            "total_input": job.input_tokens,
            "total_output": job.output_tokens,
        }


def _build_batch_chunk_user_msg(
    state: dict, chunk: str, chunk_idx: int, total_chunks: int,
    labels_so_far: list[str],
) -> str:
    dedup_hint = ""
    if labels_so_far:
        dedup_hint = "\n\nNeurons already created from earlier chunks (DO NOT duplicate):\n"
        for lbl in labels_so_far[-30:]:
            dedup_hint += f"  - {lbl}\n"

    return f"""Citation: {state['citation']}
Source type: {state['source_type']}
Department: {state['department'] or 'unspecified'}
Role: {state['role_key'] or 'unspecified'}
Chunk {chunk_idx + 1} of {total_chunks}
{dedup_hint}
Source material to segment into neurons:

{chunk}"""


def _enrich_batch_proposals(proposals: list[dict], state: dict) -> None:
    for p in proposals:
        p["department"] = state["department"]
        p["role_key"] = state["role_key"]
        p["parent_id"] = state["parent_id"]
        p["source_type"] = state["source_type"]
        p["citation"] = state["citation"]
        p["source_url"] = state["source_url"]
        p["effective_date"] = state["effective_date"]


async def _run_batch_ingest(job_id: str, start_chunk: int = 0):
    """Background task: process chunks sequentially through the chosen model."""
    from app.services.llm_provider import llm_chat

    state = await _load_batch_job_state(job_id)
    if not state:
        return

    chunks = state["chunks"]
    all_proposals = state["all_proposals"]
    errors = state["errors"]
    total_cost = state["total_cost"]
    total_input = state["total_input"]
    total_output = state["total_output"]
    labels_so_far = [p.get("label", "") for p in all_proposals]

    for i in range(start_chunk, len(chunks)):
        if _batch_cancel_flags.get(job_id):
            await _update_batch_job(job_id, status="cancelled", step=f"Cancelled at chunk {i + 1}/{len(chunks)}")
            _batch_cancel_flags.pop(job_id, None)
            return

        await _update_batch_job(
            job_id, current_chunk=i + 1,
            step=f"Processing chunk {i + 1} of {len(chunks)}", status="running",
        )

        user_msg = _build_batch_chunk_user_msg(state, chunks[i], i, len(chunks), labels_so_far)

        try:
            result = await llm_chat(state["system_prompt"], user_msg, max_tokens=8192, model=state["model"])
            json_match = re.search(r'\[.*\]', result["text"].strip(), re.DOTALL)
            if json_match:
                proposals = json.loads(json_match.group())
                _enrich_batch_proposals(proposals, state)
                for p in proposals:
                    labels_so_far.append(p.get("label", ""))
                all_proposals.extend(proposals)
            total_cost += result["cost_usd"]
            total_input += result["input_tokens"]
            total_output += result["output_tokens"]
        except Exception as e:
            errors.append(f"Chunk {i + 1}: {str(e)[:200]}")

        await _update_batch_job(
            job_id, proposals_json=json.dumps(all_proposals),
            errors_json=json.dumps(errors), cost_usd=total_cost,
            input_tokens=total_input, output_tokens=total_output,
        )

    await _update_batch_job(
        job_id, status="done",
        step=f"Complete: {len(all_proposals)} proposals from {len(chunks)} chunks",
    )


@router.post("/ingest-source/batch")
async def start_batch_ingest(
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Start a background batch ingestion job that processes large source texts in chunks.

    Request body: same as ingest-source, but source_text can be arbitrarily large.
    Returns a job_id to poll for progress.
    """
    import uuid

    source_text, citation, source_type, department, role_key, queue_entry_id = (
        _validate_ingest_body(body)
    )
    model = body.get("model", "haiku")
    chunk_size = body.get("chunk_size", 18000)

    parent_neuron = await _find_parent_neuron(db, role_key, department)
    context_info = await _build_parent_context(db, parent_neuron)
    system_prompt = _build_batch_system_prompt(context_info)

    chunks = _chunk_text(source_text, chunk_size=chunk_size)
    job_id = str(uuid.uuid4())[:8]

    batch_job = _create_batch_job_record(
        job_id=job_id,
        chunks=chunks,
        source_text=source_text,
        citation=citation,
        source_type=source_type,
        department=department,
        role_key=role_key,
        parent_neuron=parent_neuron,
        queue_entry_id=queue_entry_id,
        model=model,
        body=body,
        system_prompt=system_prompt,
    )
    db.add(batch_job)
    await db.commit()

    asyncio.ensure_future(_run_batch_ingest(job_id))

    return {
        "job_id": job_id,
        "total_chunks": len(chunks),
        "total_chars": len(source_text),
        "status": "running",
    }


@router.get("/ingest-source/batch/{job_id}")
async def get_batch_ingest_status(job_id: str, db: AsyncSession = Depends(get_db)):
    """Poll for batch ingestion job progress."""
    job = await db.get(BatchJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    proposals = json.loads(job.proposals_json or "[]")
    errors = json.loads(job.errors_json or "[]")

    return {
        "job_id": job_id,
        "status": job.status,
        "step": job.step,
        "total_chunks": job.total_chunks,
        "current_chunk": job.current_chunk,
        "proposals_count": len(proposals),
        "proposals": proposals if job.status in ("done", "interrupted") else [],
        "errors": errors,
        "cost_usd": job.cost_usd,
        "input_tokens": job.input_tokens,
        "output_tokens": job.output_tokens,
        "citation": job.citation,
        "department": job.department,
        "role_key": job.role_key,
        "parent_id": job.parent_id,
        "parent_label": job.parent_label,
        "queue_entry_id": job.queue_entry_id,
    }


@router.post("/ingest-source/batch/{job_id}/cancel")
async def cancel_batch_ingest(job_id: str, db: AsyncSession = Depends(get_db)):
    """Cancel a running batch ingestion job."""
    job = await db.get(BatchJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _batch_cancel_flags[job_id] = True
    return {"status": "cancelling", "job_id": job_id}


@router.post("/ingest-source/batch/{job_id}/resume")
async def resume_batch_ingest(job_id: str, db: AsyncSession = Depends(get_db)):
    """Resume an interrupted batch ingestion job from where it left off."""
    job = await db.get(BatchJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in ("interrupted", "error"):
        raise HTTPException(status_code=400, detail=f"Cannot resume job with status '{job.status}' — only interrupted or error jobs can be resumed")

    start_chunk = job.current_chunk  # resume from the chunk it was on
    job.status = "running"
    job.step = f"Resuming from chunk {start_chunk + 1}/{job.total_chunks}"
    await db.commit()

    asyncio.ensure_future(_run_batch_ingest(job_id, start_chunk=start_chunk))

    return {
        "job_id": job_id,
        "status": "running",
        "resuming_from_chunk": start_chunk + 1,
        "total_chunks": job.total_chunks,
        "existing_proposals": len(json.loads(job.proposals_json or "[]")),
    }


@router.get("/ingest-source/batch")
async def list_batch_jobs(db: AsyncSession = Depends(get_db)):
    """List all batch ingestion jobs (active and recent)."""
    result = await db.execute(
        select(BatchJob).order_by(BatchJob.created_at.desc()).limit(50)
    )
    batch_jobs = result.scalars().all()

    jobs = []
    for job in batch_jobs:
        errors = json.loads(job.errors_json or "[]")
        proposals = json.loads(job.proposals_json or "[]")
        jobs.append({
            "job_id": job.id,
            "status": job.status,
            "step": job.step,
            "total_chunks": job.total_chunks,
            "current_chunk": job.current_chunk,
            "proposals_count": len(proposals),
            "errors": errors,
            "cost_usd": job.cost_usd,
            "input_tokens": job.input_tokens,
            "output_tokens": job.output_tokens,
            "citation": job.citation,
            "queue_entry_id": job.queue_entry_id,
            "created_at": job.created_at.isoformat() if job.created_at else None,
        })
    return {"jobs": jobs, "active_count": sum(1 for j in jobs if j["status"] == "running")}
