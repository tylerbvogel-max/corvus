"""Admin endpoints: seed, reset, cost report, checkpoint, scoring health, health check, compliance audit."""

import asyncio
import json
import math
import os
import re
from datetime import datetime, timedelta
from types import MappingProxyType

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import select, func, delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.database import async_session
from app.models import Neuron, Query, NeuronFiring, NeuronEdge, PropagationLog, IntentNeuronMap, SystemState, NeuronRefinement, EmergentQueue, SystemAlert, EvalScore, AutopilotRun, BatchJob
from app.schemas import (
    SeedResponse, ResetResponse, CostReportResponse, CheckpointResponse,
)
from app.seed.loader import load_seed
from app.services import action_bus
from app.middleware.rbac import UserIdentity, require_role

_ADMIN_ACTOR = UserIdentity(user_id="admin-ingest", role="admin", source="system")

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/seed", response_model=SeedResponse)
async def seed_database(force: bool = False, db: AsyncSession = Depends(get_db)):
    """Seed the neuron graph with initial data; pass force=true to re-seed."""
    result = await load_seed(db, force=force)
    return SeedResponse(**result)


@router.post("/reset", response_model=ResetResponse)
async def reset_firings(db: AsyncSession = Depends(get_db)):
    """Clear firing history, co-firing edges, and query data. Keep neuron definitions."""
    await db.execute(delete(PropagationLog))
    await db.execute(delete(NeuronFiring))
    await db.execute(delete(NeuronEdge))
    await db.execute(delete(IntentNeuronMap))
    await db.execute(delete(Query))

    # Reset system state
    state = (await db.execute(select(SystemState).where(SystemState.id == 1))).scalar_one_or_none()
    if state:
        state.global_token_counter = 0
        state.total_queries = 0

    # Reset neuron invocations, utility, and weak edges
    neurons = await db.execute(select(Neuron))
    for neuron in neurons.scalars():
        neuron.invocations = 0
        neuron.avg_utility = 0.5
        neuron.is_active = True
        neuron.weak_edges = None

    await db.commit()

    # Invalidate caches since edges and firings were cleared
    from app.services.adjacency_cache import invalidate_adjacency_cache
    invalidate_adjacency_cache()

    return ResetResponse(status="reset_complete")


@router.post("/retention/purge")
async def retention_purge_now(db: AsyncSession = Depends(get_db)):
    """Run the query-telemetry retention purge now (fwd-tier1, FedRAMP AU-11).

    Honors settings.query_retention_days — returns status "disabled" (and
    purges nothing) when the retention window is 0/unset. Audit-bearing
    artifacts are detached, never deleted; see services/retention.py.
    """
    from app.services.retention import run_retention_purge
    return await run_retention_purge(db)


@router.post("/checkpoint", response_model=CheckpointResponse)
async def create_checkpoint(db: AsyncSession = Depends(get_db)):
    """Export all neurons to a JSON checkpoint file and commit it."""
    result = await db.execute(select(Neuron).order_by(Neuron.id))
    neurons = result.scalars().all()

    data = [
        {
            "id": n.id,
            "parent_id": n.parent_id,
            "layer": n.layer,
            "node_type": n.node_type,
            "label": n.label,
            "content": n.content,
            "summary": n.summary,
            "department": n.department,
            "role_key": n.role_key,
            "invocations": n.invocations,
            "avg_utility": n.avg_utility,
            "is_active": n.is_active,
            "created_at_query_count": n.created_at_query_count,
        }
        for n in neurons
    ]

    # Write checkpoint file
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    checkpoint_dir = os.path.join(backend_dir, "checkpoints")
    os.makedirs(checkpoint_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"neurons_{timestamp}.json"
    filepath = os.path.join(checkpoint_dir, filename)

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)

    # Git add + commit from the backend directory
    proc = await asyncio.create_subprocess_exec(
        "git", "add", "checkpoints/",
        cwd=backend_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    proc = await asyncio.create_subprocess_exec(
        "git", "commit", "-m", f"checkpoint: {filename} ({len(data)} neurons)",
        cwd=backend_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    # Get commit SHA
    proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "HEAD",
        cwd=backend_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    commit_sha = stdout.decode().strip()

    return CheckpointResponse(
        status="ok",
        filename=filename,
        neuron_count=len(data),
        commit_sha=commit_sha,
    )


@router.get("/cost-report", response_model=CostReportResponse)
async def cost_report(db: AsyncSession = Depends(get_db)):
    """Return aggregate token usage and cost statistics across all queries."""
    total_queries = (await db.execute(select(func.count(Query.id)))).scalar() or 0
    total_cost = (await db.execute(select(func.sum(Query.cost_usd)))).scalar() or 0.0
    total_input = (await db.execute(
        select(
            func.sum(Query.classify_input_tokens) + func.sum(Query.execute_input_tokens)
        )
    )).scalar() or 0
    total_output = (await db.execute(
        select(
            func.sum(Query.classify_output_tokens) + func.sum(Query.execute_output_tokens)
        )
    )).scalar() or 0

    from app.services.model_usage_ledger import maintenance_cost_breakdown
    maintenance = maintenance_cost_breakdown()
    maint_total = maintenance["total_equivalent_usd"]

    return CostReportResponse(
        total_queries=total_queries,
        total_cost_usd=round(total_cost, 6),
        avg_cost_per_query=round(total_cost / total_queries, 6) if total_queries > 0 else 0.0,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        maintenance_cost_usd=maint_total,
        maintenance_per_query_usd=round(maint_total / total_queries, 6) if total_queries > 0 else 0.0,
        maintenance_by_workload=maintenance["by_workload"],
        maintenance_since=maintenance["ledger_started_at"],
    )


@router.get("/emergent-queue")
async def get_emergent_queue(
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Get emergent queue entries, optionally filtered by status."""
    stmt = select(EmergentQueue).order_by(EmergentQueue.detection_count.desc())
    if status:
        stmt = stmt.where(EmergentQueue.status == status)
    result = await db.execute(stmt)
    entries = result.scalars().all()

    return {
        "total": len(entries),
        "entries": [
            {
                "id": e.id,
                "citation_pattern": e.citation_pattern,
                "domain": e.domain,
                "family": e.family,
                "detection_count": e.detection_count,
                "first_detected_at": e.first_detected_at.isoformat() if e.first_detected_at else None,
                "last_detected_at": e.last_detected_at.isoformat() if e.last_detected_at else None,
                "detected_in_neuron_ids": json.loads(e.detected_in_neuron_ids or "[]"),
                "detected_in_query_ids": json.loads(e.detected_in_query_ids or "[]"),
                "status": e.status,
                "resolved_neuron_id": e.resolved_neuron_id,
                "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
                "notes": e.notes,
            }
            for e in entries
        ],
    }


@router.post("/emergent-queue/{entry_id}/dismiss")
async def dismiss_queue_entry(
    entry_id: int,
    notes: str = "",
    db: AsyncSession = Depends(get_db),
):
    """Dismiss an emergent queue entry with a reason."""
    entry = await db.get(EmergentQueue, entry_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Queue entry not found")
    entry.status = "dismissed"
    entry.notes = notes
    await db.commit()
    return {"status": "dismissed", "id": entry_id}


def _build_citation_lookup(neurons) -> dict[str, int]:
    citation_lookup: dict[str, int] = {}
    for n in neurons:
        if n.citation and n.source_type in ("regulatory_primary", "technical_primary"):
            citation_lookup[n.citation] = n.id
    return citation_lookup


def _resolve_reference(ref: dict, citation_lookup: dict[str, int]) -> int | None:
    for cit, nid in citation_lookup.items():
        if ref["pattern"] in cit or cit in ref["pattern"]:
            return nid
    return None


async def _enqueue_unresolved_reference(
    db: AsyncSession, ref: dict, neuron_id: int,
) -> tuple[int, int]:
    existing = (await db.execute(
        select(EmergentQueue).where(EmergentQueue.citation_pattern == ref["pattern"])
    )).scalar_one_or_none()

    if existing:
        if existing.status != "resolved":
            existing.detection_count += 1
            existing.last_detected_at = datetime.now()
            ids = json.loads(existing.detected_in_neuron_ids or "[]")
            if neuron_id not in ids:
                ids.append(neuron_id)
                existing.detected_in_neuron_ids = json.dumps(ids)
            return 0, 1
        return 0, 0

    db.add(EmergentQueue(
        citation_pattern=ref["pattern"],
        domain=ref["domain"],
        family=ref["family"],
        detection_count=1,
        detected_in_neuron_ids=json.dumps([neuron_id]),
    ))
    return 1, 0


async def _process_neuron_references(
    db: AsyncSession, neuron, refs: list[dict], citation_lookup: dict[str, int],
    family_counts: dict[str, int],
) -> tuple[int, int, int, int]:
    resolved = 0
    unresolved = 0
    new_queue = 0
    incremented_queue = 0

    for ref in refs:
        matched_id = _resolve_reference(ref, citation_lookup)
        if matched_id:
            ref["resolved_neuron_id"] = matched_id
            ref["resolved_at"] = datetime.now().isoformat()
            resolved += 1
        else:
            unresolved += 1
            family_counts[ref["family"]] = family_counts.get(ref["family"], 0) + 1
            nq, iq = await _enqueue_unresolved_reference(db, ref, neuron.id)
            new_queue += nq
            incremented_queue += iq

    neuron.external_references = json.dumps(refs)
    return resolved, unresolved, new_queue, incremented_queue


@router.post("/scan-references")
async def scan_references(db: AsyncSession = Depends(get_db)):
    """Retroactive scan: detect external references in all neurons and seed the emergent queue."""
    from app.services.reference_detector import detect_neuron_references

    result = await db.execute(select(Neuron).where(Neuron.is_active == True))
    neurons = result.scalars().all()

    neurons_scanned = 0
    neurons_with_refs = 0
    total_refs = 0
    resolved = 0
    unresolved = 0
    new_queue = 0
    incremented_queue = 0
    family_counts: dict[str, int] = {}
    citation_lookup = _build_citation_lookup(neurons)

    for neuron in neurons:
        neurons_scanned += 1
        refs = detect_neuron_references(neuron.content, neuron.summary)
        if not refs:
            neuron.external_references = None
            continue

        neurons_with_refs += 1
        total_refs += len(refs)
        r, u, nq, iq = await _process_neuron_references(
            db, neuron, refs, citation_lookup, family_counts,
        )
        resolved += r
        unresolved += u
        new_queue += nq
        incremented_queue += iq

    await db.commit()

    top_families = sorted(family_counts.items(), key=lambda x: -x[1])[:10]

    return {
        "neurons_scanned": neurons_scanned,
        "neurons_with_references": neurons_with_refs,
        "total_references_found": total_refs,
        "resolved": resolved,
        "unresolved": unresolved,
        "new_queue_entries": new_queue,
        "existing_queue_entries_incremented": incremented_queue,
        "top_unresolved_families": [
            {"family": f, "count": c} for f, c in top_families
        ],
    }


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


@router.post("/prune-edges")
async def prune_edges(db: AsyncSession = Depends(get_db)):
    """Prune stale low-weight co-firing edges to control graph density."""
    from app.config import settings

    # Count before
    before = (await db.execute(select(func.count()).select_from(NeuronEdge))).scalar() or 0

    # Get current query count for staleness check
    state = (await db.execute(select(SystemState).where(SystemState.id == 1))).scalar_one_or_none()
    total_queries = state.total_queries if state else 0
    stale_threshold = total_queries - settings.edge_prune_stale_queries

    # Delete edges that have fired only once and are stale
    await db.execute(text(
        "DELETE FROM neuron_edges "
        "WHERE co_fire_count < :min_cofires AND last_updated_query < :stale"
    ), {"min_cofires": settings.edge_prune_min_cofires, "stale": max(0, stale_threshold)})

    # Also prune stale weak edges from JSONB
    min_c = settings.edge_prune_min_cofires
    await db.execute(text("""
        UPDATE neurons
        SET weak_edges = (
            SELECT jsonb_object_agg(key, value)
            FROM jsonb_each(weak_edges)
            WHERE (value->>'c')::int >= :min_c
        )
        WHERE weak_edges IS NOT NULL
    """), {"min_c": min_c})

    await db.commit()

    # Invalidate adjacency cache since edges were deleted
    from app.services.adjacency_cache import invalidate_adjacency_cache
    invalidate_adjacency_cache()

    after = (await db.execute(select(func.count()).select_from(NeuronEdge))).scalar() or 0

    return {
        "status": "pruned",
        "edges_before": before,
        "edges_after": after,
        "edges_removed": before - after,
    }


_SCORING_SIGNALS = ["burst", "impact", "precision", "novelty", "recency", "relevance"]


def _stats(values: list[float]) -> dict:
    """Compute mean, stddev, min, max, count for a list of floats."""
    if not values:
        return {"mean": 0, "stddev": 0, "min": 0, "max": 0, "count": 0}
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / max(1, n - 1)
    return {
        "mean": round(mean, 4),
        "stddev": round(math.sqrt(variance), 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "count": n,
    }


def _aggregate_signal(queries: list[dict], signal: str) -> list[float]:
    """Collect all neuron-level values for a signal across queries."""
    vals: list[float] = []
    for q in queries:
        vals.extend(q["signals"].get(signal, []))
    return vals


def _query_means(queries: list[dict], signal: str) -> list[float]:
    """Per-query mean for a signal."""
    means: list[float] = []
    for q in queries:
        vals = q["signals"].get(signal, [])
        if vals:
            means.append(sum(vals) / len(vals))
    return means


async def _reference_neuron_ids(db: AsyncSession) -> set[int]:
    """Ids of reference-class neurons (mind-reference-class), matched on
    all three class axes like reference_exclusion_filters. Scoring-health
    drift assumes ORGANIC corpus growth; bulk document ingest (and bulk
    revocation) violate that assumption, so reference-class neurons are
    segmented out of the headline signal population and reported in
    their own Library block instead."""
    from sqlalchemy import or_
    rows = (await db.execute(
        select(Neuron.id).where(or_(
            Neuron.source_origin == "document",
            Neuron.node_type.in_(("reference", "document")),
            Neuron.department == "Library",
        ))
    )).scalars().all()
    return set(rows)


def _parse_query_scores(
    rows, signals: list[str], neuron_ids: set[int] | None = None,
    exclude: bool = True,
) -> list[dict]:
    """Parse raw query rows into structured score dicts with per-signal values.

    neuron_ids segments by population: exclude=True keeps neurons NOT in
    the set (experiential headline); exclude=False keeps ONLY the set
    (the Library block). None = no filtering (legacy behavior).
    """
    query_scores: list[dict] = []
    for qid, scores_json, created_at in rows:
        try:
            scores = json.loads(scores_json) if scores_json else []
        except json.JSONDecodeError:
            continue
        if not scores:
            continue
        signal_values: dict[str, list[float]] = {s: [] for s in signals}
        for neuron_score in scores:
            if neuron_ids is not None:
                member = neuron_score.get("neuron_id") in neuron_ids
                if member == exclude:
                    continue
            for s in signals:
                val = neuron_score.get(s)
                if val is not None:
                    signal_values[s].append(float(val))
        if not any(signal_values[s] for s in signals):
            continue  # a query with no surviving population tells us nothing
        query_scores.append({
            "query_id": qid,
            "created_at": created_at.isoformat() if created_at else None,
            "signals": signal_values,
        })
    return query_scores


def _scoring_distribution(
    query_scores: list[dict],
    recent_window: int,
    baseline_window: int,
    drift_threshold: float,
) -> tuple[dict, list[dict]]:
    """Build per-signal distribution stats and detect drift. Returns (signals_report, drift_alerts)."""
    total = len(query_scores)
    if total <= recent_window:
        baseline_qs = query_scores
        recent_qs = query_scores
        can_detect_drift = False
    else:
        recent_qs = query_scores[-recent_window:]
        baseline_start = max(0, total - recent_window - baseline_window)
        baseline_qs = query_scores[baseline_start:total - recent_window]
        can_detect_drift = len(baseline_qs) >= 5

    signals_report: dict = {}
    drift_alerts: list[dict] = []

    for sig in _SCORING_SIGNALS:
        baseline_vals = _aggregate_signal(baseline_qs, sig)
        recent_vals = _aggregate_signal(recent_qs, sig)
        baseline_means = _query_means(baseline_qs, sig)
        recent_means = _query_means(recent_qs, sig)

        b_stats = _stats(baseline_vals)
        r_stats = _stats(recent_vals)
        bm_stats = _stats(baseline_means)
        rm_stats = _stats(recent_means)

        drifted = False
        z_score = 0.0
        if can_detect_drift and bm_stats["stddev"] > 0.001 and len(recent_means) >= 3:
            z_score = (rm_stats["mean"] - bm_stats["mean"]) / bm_stats["stddev"]
            drifted = abs(z_score) > drift_threshold

        signals_report[sig] = {
            "baseline": b_stats, "recent": r_stats,
            "baseline_query_means": bm_stats, "recent_query_means": rm_stats,
            "z_score": round(z_score, 3), "drifted": drifted,
        }

        if drifted:
            direction = "increased" if z_score > 0 else "decreased"
            drift_alerts.append({
                "signal": sig, "direction": direction, "z_score": round(z_score, 3),
                "baseline_mean": bm_stats["mean"], "recent_mean": rm_stats["mean"],
                "message": f"{sig} has {direction} significantly (z={z_score:.1f}): "
                           f"baseline \u03bc={bm_stats['mean']:.3f} \u2192 recent \u03bc={rm_stats['mean']:.3f}",
            })

    return signals_report, drift_alerts, can_detect_drift


def _scoring_timeline(query_scores: list[dict]) -> list[dict]:
    """Build per-query timeline for charting (last 50 queries)."""
    timeline_qs = query_scores[-50:]
    per_query_timeline: list[dict] = []
    for q in timeline_qs:
        entry: dict = {"query_id": q["query_id"], "created_at": q["created_at"]}
        for sig in _SCORING_SIGNALS:
            vals = q["signals"].get(sig, [])
            entry[sig] = round(sum(vals) / len(vals), 4) if vals else 0
        per_query_timeline.append(entry)
    return per_query_timeline


@router.get("/scoring-health")
async def scoring_health(
    baseline_window: int = 50,
    recent_window: int = 20,
    drift_threshold: float = 2.0,
    db: AsyncSession = Depends(get_db),
):
    """Compute per-signal scoring distribution stats and detect drift.

    Compares the most recent `recent_window` queries against a trailing
    `baseline_window` (the queries just before the recent window).
    Drift is flagged when the recent mean deviates by more than
    `drift_threshold` standard deviations from the baseline mean.
    """
    needed = baseline_window + recent_window
    result = await db.execute(
        select(Query.id, Query.neuron_scores_json, Query.created_at)
        .where(Query.neuron_scores_json.isnot(None))
        .order_by(Query.id.desc())
        .limit(needed)
    )
    rows = result.all()

    if len(rows) < 5:
        return {
            "status": "insufficient_data",
            "queries_available": len(rows),
            "minimum_required": 5,
            "signals": {},
            "drift_alerts": [],
            "per_query_timeline": [],
        }

    # Segment by origination (mind-reference-class): the headline signals
    # and drift alerts cover the EXPERIENTIAL population only — organic
    # growth is what the z-test assumes. Reference-class (Library) neurons
    # arrive and leave in bulk (document ingest / revocation), so they get
    # their own stats block and never trip drift alerts.
    ref_ids = await _reference_neuron_ids(db)
    query_scores = _parse_query_scores(rows, _SCORING_SIGNALS, ref_ids or None)
    query_scores.reverse()  # chronological order (oldest first)
    total = len(query_scores)

    signals_report, drift_alerts, can_detect_drift = _scoring_distribution(
        query_scores, recent_window, baseline_window, drift_threshold,
    )

    library_block = None
    if ref_ids:
        lib_scores = _parse_query_scores(
            rows, _SCORING_SIGNALS, ref_ids, exclude=False)
        lib_scores.reverse()
        if lib_scores:
            lib_signals, _lib_alerts, _ = _scoring_distribution(
                lib_scores, recent_window, baseline_window, drift_threshold)
            library_block = {
                "queries_with_reference_hits": len(lib_scores),
                "signals": lib_signals,
                "note": ("reference-class population (document-ingested); "
                         "growth is curated, not organic — informational "
                         "only, never drift-alerted"),
            }

    return {
        "status": "ok",
        "queries_analyzed": total,
        "baseline_window": min(baseline_window, total),
        "recent_window": min(recent_window, total),
        "can_detect_drift": can_detect_drift,
        "drift_threshold": drift_threshold,
        "signals": signals_report,
        "drift_alerts": drift_alerts,
        "per_query_timeline": _scoring_timeline(query_scores),
        "segmentation": {"reference_neurons": len(ref_ids),
                         "library": library_block},
    }


# ── Health Check: automated drift alerting + circuit breaker + quality monitoring ──

# Go/no-go thresholds (configurable)
CIRCUIT_BREAKER_THRESHOLDS = MappingProxyType({
    "min_avg_eval_overall": 2.5,       # Below this → circuit breaker trips
    "min_avg_user_rating": 0.3,        # Below this → circuit breaker trips
    "max_zero_hit_pct": 0.40,          # >40% zero-hit queries → warning
    "drift_z_threshold": 2.0,          # Z-score threshold for drift alerts
    "eval_window": 20,                 # Number of recent queries to evaluate
})


async def _maybe_create_alert(
    db: AsyncSession, alert_type: str, severity: str, message: str,
    detail_json: str, signal: str | None = None,
) -> dict | None:
    """Create a SystemAlert if no unacknowledged alert of this type (+signal) exists."""
    filters = [SystemAlert.alert_type == alert_type, SystemAlert.acknowledged == False]
    if signal is not None:
        filters.append(SystemAlert.signal == signal)
    existing = await db.execute(select(SystemAlert).where(*filters))
    if existing.scalar_one_or_none():
        return None
    alert = SystemAlert(
        alert_type=alert_type, severity=severity, signal=signal,
        message=message, detail_json=detail_json,
    )
    db.add(alert)
    result = {"type": alert_type, "message": message}
    if signal is not None:
        result["signal"] = signal
    return result


async def _health_database(db: AsyncSession, window: int) -> dict:
    """Check eval quality and user ratings against circuit breaker thresholds."""
    eval_result = await db.execute(
        select(EvalScore.overall).order_by(EvalScore.id.desc()).limit(int(window))
    )
    eval_scores = [row[0] for row in eval_result.all()]
    avg_eval = sum(eval_scores) / len(eval_scores) if eval_scores else None

    rating_result = await db.execute(
        select(Query.user_rating).where(Query.user_rating.isnot(None))
        .order_by(Query.id.desc()).limit(int(window))
    )
    ratings = [row[0] for row in rating_result.all()]
    avg_rating = sum(ratings) / len(ratings) if ratings else None

    tripped = False
    reasons: list[str] = []
    alerts: list[dict] = []

    if avg_eval is not None and avg_eval < CIRCUIT_BREAKER_THRESHOLDS["min_avg_eval_overall"]:
        tripped = True
        reasons.append(f"avg eval overall {avg_eval:.2f} < {CIRCUIT_BREAKER_THRESHOLDS['min_avg_eval_overall']}")
        created = await _maybe_create_alert(
            db, "quality_drop", "critical",
            f"Average eval overall dropped to {avg_eval:.2f} (threshold: {CIRCUIT_BREAKER_THRESHOLDS['min_avg_eval_overall']})",
            json.dumps({"avg_eval": avg_eval, "window": len(eval_scores)}),
        )
        if created:
            alerts.append(created)

    if avg_rating is not None and avg_rating < CIRCUIT_BREAKER_THRESHOLDS["min_avg_user_rating"]:
        tripped = True
        reasons.append(f"avg user rating {avg_rating:.2f} < {CIRCUIT_BREAKER_THRESHOLDS['min_avg_user_rating']}")

    return {
        "tripped": tripped, "reasons": reasons, "alerts": alerts,
        "avg_eval": avg_eval, "eval_count": len(eval_scores),
        "avg_rating": avg_rating, "rating_count": len(ratings),
    }


async def _health_pipeline(db: AsyncSession, window: int) -> dict:
    """Check zero-hit rate and API model version changes."""
    recent_queries = await db.execute(
        select(Query.selected_neuron_ids).order_by(Query.id.desc()).limit(int(window))
    )
    rows = recent_queries.all()
    reasons: list[str] = []
    if rows:
        zero_hits = sum(1 for (ids,) in rows if not ids or ids == "[]")
        zero_pct = zero_hits / len(rows)
        if zero_pct > CIRCUIT_BREAKER_THRESHOLDS["max_zero_hit_pct"]:
            reasons.append(f"zero-hit rate {zero_pct:.0%} > {CIRCUIT_BREAKER_THRESHOLDS['max_zero_hit_pct']:.0%}")

    version_result = await db.execute(
        select(Query.model_version).where(Query.model_version.isnot(None))
        .order_by(Query.id.desc()).limit(int(window))
    )
    versions = [row[0] for row in version_result.all()]
    unique_versions = list(set(versions)) if versions else []
    model_version_changed = len(unique_versions) > 1

    alerts: list[dict] = []
    if model_version_changed:
        created = await _maybe_create_alert(
            db, "api_change", "info",
            f"Multiple model versions detected in recent queries: {', '.join(unique_versions)}",
            json.dumps({"versions": unique_versions}),
        )
        if created:
            alerts.append(created)

    return {
        "reasons": reasons, "alerts": alerts,
        "model_versions": unique_versions, "model_version_changed": model_version_changed,
    }


async def _health_graph(db: AsyncSession) -> tuple[list[dict], dict]:
    """Run drift detection via scoring_health and create alerts for drifted signals."""
    drift_result = await scoring_health(db=db)
    alerts: list[dict] = []
    if drift_result.get("status") == "ok" and drift_result.get("drift_alerts"):
        for da in drift_result["drift_alerts"]:
            created = await _maybe_create_alert(
                db, "drift", "warning", da["message"],
                json.dumps(da), signal=da["signal"],
            )
            if created:
                alerts.append(created)
    return alerts, drift_result


async def _fetch_active_alerts(db: AsyncSession) -> list[dict]:
    """Fetch all unacknowledged system alerts."""
    result = await db.execute(
        select(SystemAlert).where(SystemAlert.acknowledged == False)
        .order_by(SystemAlert.created_at.desc())
    )
    return [
        {
            "id": a.id, "type": a.alert_type, "severity": a.severity,
            "signal": a.signal, "message": a.message,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in result.scalars().all()
    ]


@router.get("/health-check")
async def health_check(db: AsyncSession = Depends(get_db)):
    """Production health check: drift detection, quality monitoring, circuit breaker.

    Runs the scoring drift computation, checks eval quality and user ratings,
    detects API model version changes, and persists alerts to system_alerts.
    Returns go/no-go status.
    """
    window = CIRCUIT_BREAKER_THRESHOLDS["eval_window"]
    alerts_created: list[dict] = []

    graph_alerts, drift_result = await _health_graph(db)
    alerts_created.extend(graph_alerts)

    db_health = await _health_database(db, window)
    alerts_created.extend(db_health["alerts"])

    pipeline_health = await _health_pipeline(db, window)
    alerts_created.extend(pipeline_health["alerts"])

    reasons = db_health["reasons"] + pipeline_health["reasons"]
    circuit_breaker_tripped = db_health["tripped"] or bool(pipeline_health["reasons"])

    if circuit_breaker_tripped:
        created = await _maybe_create_alert(
            db, "circuit_breaker", "critical",
            f"Circuit breaker tripped: {'; '.join(reasons)}",
            json.dumps({"reasons": reasons}),
        )
        if created:
            alerts_created.append(created)

    await db.commit()
    active_alerts = await _fetch_active_alerts(db)

    return {
        "status": "tripped" if circuit_breaker_tripped else "ok",
        "circuit_breaker_tripped": circuit_breaker_tripped,
        "reasons": reasons,
        "avg_eval_overall": round(db_health["avg_eval"], 3) if db_health["avg_eval"] is not None else None,
        "avg_user_rating": round(db_health["avg_rating"], 3) if db_health["avg_rating"] is not None else None,
        "eval_count": db_health["eval_count"],
        "rating_count": db_health["rating_count"],
        "model_versions": pipeline_health["model_versions"],
        "model_version_changed": pipeline_health["model_version_changed"],
        "drift_alerts_count": len(drift_result.get("drift_alerts", [])),
        "active_alerts": active_alerts,
        "new_alerts": alerts_created,
        "thresholds": CIRCUIT_BREAKER_THRESHOLDS,
    }


@router.get("/alerts")
async def get_alerts(
    include_acknowledged: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Get system alerts, optionally including acknowledged ones."""
    q = select(SystemAlert).order_by(SystemAlert.created_at.desc())
    if not include_acknowledged:
        q = q.where(SystemAlert.acknowledged == False)
    result = await db.execute(q.limit(100))
    return [
        {
            "id": a.id,
            "type": a.alert_type,
            "severity": a.severity,
            "signal": a.signal,
            "message": a.message,
            "detail": json.loads(a.detail_json) if a.detail_json else None,
            "acknowledged": a.acknowledged,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in result.scalars().all()
    ]


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: int, db: AsyncSession = Depends(get_db)):
    """Acknowledge (dismiss) a system alert."""
    alert = await db.get(SystemAlert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.acknowledged = True
    alert.acknowledged_at = datetime.utcnow()
    await db.commit()
    return {"status": "acknowledged", "alert_id": alert_id}


@router.post("/alerts/acknowledge-all")
async def acknowledge_all_alerts(db: AsyncSession = Depends(get_db)):
    """Acknowledge all active alerts."""
    result = await db.execute(
        select(SystemAlert).where(SystemAlert.acknowledged == False)
    )
    count = 0
    for alert in result.scalars().all():
        alert.acknowledged = True
        alert.acknowledged_at = datetime.utcnow()
        count += 1
    await db.commit()
    return {"status": "acknowledged", "count": count}


# ── Compliance Audit: PII scan, bias assessment, scoring baselines, provenance audit ──

# PII patterns for neuron content scanning.
# Aerospace content has FAR/DFARS clause numbers (e.g. 52.246-2, 252.204-7012)
# and example/placeholder emails in technical neurons — these are excluded.
_DFARS_PATTERN = re.compile(r"\b\d{2,3}\.\d{3}-\d{4}\b")  # matches 52.246-2102, 252.204-7012
_EXAMPLE_EMAIL = re.compile(r"@(example|placeholder|test|acme)\.", re.I)
_PII_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "SSN"),  # SSN requires dashes (123-45-6789)
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "email"),
    (re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"), "credit_card"),
    (re.compile(r"(?<!\d\.)\b\d{3}[-\s]\d{3}[-\s]\d{4}\b"), "phone"),  # phone, not preceded by digit.
]


def _is_false_positive(text: str, match_str: str, pii_type: str) -> bool:
    """Filter out known false positives in aerospace content."""
    if pii_type == "phone" and _DFARS_PATTERN.search(match_str):
        return True
    if pii_type == "phone":
        # Check if this match is part of a DFARS/FAR clause number
        idx = text.find(match_str)
        if idx > 0 and text[idx - 1] == '.':
            return True  # preceded by dot = clause number like 252.204-7012
    if pii_type == "email" and _EXAMPLE_EMAIL.search(match_str):
        return True
    return False


def _audit_pii_exposure(neurons) -> dict:
    """Scan neurons for PII patterns (MET-3). Returns pii_scan section."""
    pii_findings: list[dict] = []
    for neuron in neurons:
        for field_name, text_val in [("content", neuron.content), ("summary", neuron.summary), ("label", neuron.label)]:
            if not text_val:
                continue
            for pattern, pii_type in _PII_PATTERNS:
                matches = pattern.findall(text_val)
                real_matches = [m for m in matches if not _is_false_positive(text_val, m, pii_type)]
                if real_matches:
                    pii_findings.append({
                        "neuron_id": neuron.id,
                        "neuron_label": neuron.label[:80],
                        "department": neuron.department,
                        "field": field_name,
                        "pii_type": pii_type,
                        "match_count": len(real_matches),
                        "excerpt": real_matches[0][:20] + "..." if len(real_matches[0]) > 20 else real_matches[0],
                    })
    return {
        "findings": pii_findings,
        "total_findings": len(pii_findings),
        "neurons_with_pii": len(set(f["neuron_id"] for f in pii_findings)),
        "clean": len(pii_findings) == 0,
    }


def _audit_neuron_coverage(neurons, total_neurons: int) -> dict:
    """Compute per-department/layer/source coverage tallies. Returns coverage context dict."""
    dept_counts: dict[str, int] = {}
    dept_invocations: dict[str, int] = {}
    dept_utility: dict[str, list[float]] = {}
    layer_counts: dict[int, int] = {}
    source_type_counts: dict[str, int] = {}

    for neuron in neurons:
        dept = neuron.department or "(none)"
        dept_counts[dept] = dept_counts.get(dept, 0) + 1
        dept_invocations[dept] = dept_invocations.get(dept, 0) + neuron.invocations
        dept_utility.setdefault(dept, []).append(neuron.avg_utility)
        layer_counts[neuron.layer] = layer_counts.get(neuron.layer, 0) + 1
        st = neuron.source_type or "unknown"
        source_type_counts[st] = source_type_counts.get(st, 0) + 1

    dept_values = list(dept_counts.values())
    dept_mean = sum(dept_values) / len(dept_values) if dept_values else 0
    dept_variance = sum((v - dept_mean) ** 2 for v in dept_values) / max(1, len(dept_values) - 1) if len(dept_values) > 1 else 0
    dept_cv = math.sqrt(dept_variance) / dept_mean if dept_mean > 0 else 0

    dept_coverage = sorted([
        {
            "department": dept, "neuron_count": count,
            "pct_of_total": round(count / total_neurons * 100, 1) if total_neurons else 0,
            "total_invocations": dept_invocations.get(dept, 0),
            "avg_utility": round(sum(dept_utility.get(dept, [0.5])) / len(dept_utility.get(dept, [0.5])), 3),
        }
        for dept, count in dept_counts.items()
    ], key=lambda x: -x["neuron_count"])

    return {
        "dept_counts": dept_counts, "dept_invocations": dept_invocations,
        "dept_utility": dept_utility, "layer_counts": layer_counts,
        "source_type_counts": source_type_counts, "dept_cv": dept_cv,
        "dept_coverage": dept_coverage,
    }


async def _audit_bias_assessment(db: AsyncSession, coverage: dict, total_neurons: int) -> dict:
    """Build bias/coverage assessment section (MET-4). Returns bias_assessment dict."""
    eval_by_mode = await db.execute(
        select(
            EvalScore.answer_mode, func.count(EvalScore.id),
            func.avg(EvalScore.accuracy), func.avg(EvalScore.completeness),
            func.avg(EvalScore.clarity), func.avg(EvalScore.faithfulness),
            func.avg(EvalScore.overall),
        ).group_by(EvalScore.answer_mode)
    )
    eval_disaggregation = [
        {
            "mode": row[0], "count": row[1],
            "avg_accuracy": round(float(row[2] or 0), 2),
            "avg_completeness": round(float(row[3] or 0), 2),
            "avg_clarity": round(float(row[4] or 0), 2),
            "avg_faithfulness": round(float(row[5] or 0), 2),
            "avg_overall": round(float(row[6] or 0), 2),
        }
        for row in eval_by_mode.all()
    ]
    return {
        "department_coverage": coverage["dept_coverage"],
        "department_count": len(coverage["dept_counts"]),
        "coverage_cv": round(coverage["dept_cv"], 3),
        "coverage_imbalanced": coverage["dept_cv"] > 0.5,
        "layer_distribution": {f"L{k}": v for k, v in sorted(coverage["layer_counts"].items())},
        "eval_disaggregation": eval_disaggregation,
    }


def _percentile(vals: list[float], p: float) -> float:
    """Compute the p-th percentile of a sorted list using linear interpolation."""
    if not vals:
        return 0.0
    sorted_vals = sorted(vals)
    k = (len(sorted_vals) - 1) * (p / 100)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


async def _audit_scoring_baselines(db: AsyncSession) -> dict:
    """Compute scoring baselines from recent queries (MET-1). Returns scoring_baselines section."""
    scores_result = await db.execute(
        select(Query.neuron_scores_json)
        .where(Query.neuron_scores_json.isnot(None))
        .order_by(Query.id.desc()).limit(200)
    )
    all_signal_values: dict[str, list[float]] = {s: [] for s in _SCORING_SIGNALS}
    queries_parsed = 0
    for (scores_json,) in scores_result.all():
        try:
            scores = json.loads(scores_json) if scores_json else []
        except json.JSONDecodeError:
            continue
        if not scores:
            continue
        queries_parsed += 1
        for ns in scores:
            for s in _SCORING_SIGNALS:
                val = ns.get(s)
                if val is not None:
                    all_signal_values[s].append(float(val))

    scoring_baselines: dict[str, dict] = {}
    for sig in _SCORING_SIGNALS:
        vals = all_signal_values[sig]
        n = len(vals)
        if n == 0:
            scoring_baselines[sig] = {"count": 0, "mean": 0, "stddev": 0, "min": 0, "max": 0, "p25": 0, "p50": 0, "p75": 0, "p95": 0}
            continue
        mean = sum(vals) / n
        variance = sum((v - mean) ** 2 for v in vals) / max(1, n - 1)
        scoring_baselines[sig] = {
            "count": n, "mean": round(mean, 4), "stddev": round(math.sqrt(variance), 4),
            "min": round(min(vals), 4), "max": round(max(vals), 4),
            "p25": round(_percentile(vals, 25), 4), "p50": round(_percentile(vals, 50), 4),
            "p75": round(_percentile(vals, 75), 4), "p95": round(_percentile(vals, 95), 4),
        }

    return {
        "queries_analyzed": queries_parsed,
        "signals": scoring_baselines,
        "all_signal_values": all_signal_values,
        "metric_rationale": {
            "burst": "Recency-weighted firing frequency. Higher = neuron is trending. Prevents stale content from dominating.",
            "impact": "EMA of user feedback ratings. Reflects demonstrated usefulness in past queries.",
            "precision": "Keyword overlap between query and neuron content. Direct relevance signal.",
            "novelty": "Inverse of invocation frequency. Promotes under-used neurons to prevent echo chambers.",
            "recency": "Temporal decay since last firing. Newer content gets a natural boost.",
            "relevance": "LLM-assessed semantic similarity (when available). Highest-fidelity signal but most expensive.",
        },
    }


def _audit_provenance(neurons) -> dict:
    """Check primary-source neurons for missing citations, URLs, staleness (A007)."""
    missing_citation = []
    missing_source_url = []
    stale_neurons = []
    now = datetime.utcnow()

    for neuron in neurons:
        is_primary = neuron.source_type in ("regulatory_primary", "technical_primary")
        if not is_primary:
            continue
        entry = {"neuron_id": neuron.id, "label": neuron.label[:80],
                 "department": neuron.department, "source_type": neuron.source_type}
        if not neuron.citation:
            missing_citation.append(entry)
        if not neuron.source_url:
            missing_source_url.append(entry)
        if neuron.last_verified:
            days_since = (now - neuron.last_verified).days
            if days_since > 365:
                stale_neurons.append({
                    **entry,
                    "last_verified": neuron.last_verified.isoformat(),
                    "days_since_verified": days_since,
                })

    return {
        "missing_citations": missing_citation,
        "missing_citations_count": len(missing_citation),
        "missing_source_urls": missing_source_url,
        "missing_source_urls_count": len(missing_source_url),
        "stale_neurons": stale_neurons,
        "stale_neurons_count": len(stale_neurons),
    }


def _ci95(values: list[float]) -> dict:
    """Compute mean and 95% confidence interval."""
    n = len(values)
    if n < 2:
        return {"mean": round(values[0], 3) if values else 0, "ci_lower": 0, "ci_upper": 0, "n": n, "stderr": 0}
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    stderr = math.sqrt(variance / n)
    t_val = 2.0 if n < 30 else 1.96
    margin = t_val * stderr
    return {
        "mean": round(mean, 3), "ci_lower": round(mean - margin, 3),
        "ci_upper": round(mean + margin, 3), "n": n, "stderr": round(stderr, 4),
    }


def _cross_validate_mode(overall_vals: list[float], mode: str, k_folds: int = 5) -> dict:
    """Run k-fold cross-validation on overall scores for a single mode."""
    import random as _random
    n = len(overall_vals)
    if n < k_folds:
        return {"folds": k_folds, "n": n, "fold_means": [], "fold_cv": 0,
                "stable": True, "message": "Too few samples for cross-validation"}

    shuffled = list(overall_vals)
    _random.Random(hash(mode)).shuffle(shuffled)
    fold_size = n // k_folds
    fold_means = []
    for i in range(k_folds):
        start = i * fold_size
        end = start + fold_size if i < k_folds - 1 else n
        fold_vals = shuffled[start:end]
        fold_means.append(round(sum(fold_vals) / len(fold_vals), 3))

    fm_mean = sum(fold_means) / len(fold_means)
    fm_var = sum((v - fm_mean) ** 2 for v in fold_means) / max(1, len(fold_means) - 1)
    fm_cv = math.sqrt(fm_var) / fm_mean if fm_mean > 0 else 0

    return {
        "folds": k_folds, "n": n, "fold_means": fold_means,
        "fold_cv": round(fm_cv, 4), "stable": fm_cv < 0.10,
        "message": "Stable" if fm_cv < 0.10 else "High variance across folds \u2014 results may not be robust",
    }


async def _audit_validity_reliability(db: AsyncSession, all_signal_values: dict[str, list[float]]) -> dict:
    """Compute confidence intervals, cross-validation, signal robustness (MET-2)."""
    all_evals = await db.execute(
        select(EvalScore.answer_mode, EvalScore.accuracy, EvalScore.completeness,
               EvalScore.clarity, EvalScore.faithfulness, EvalScore.overall)
        .order_by(EvalScore.id)
    )
    eval_rows = all_evals.all()

    modes_data: dict[str, dict[str, list[float]]] = {}
    for mode, acc, comp, clar, faith, overall in eval_rows:
        if mode not in modes_data:
            modes_data[mode] = {"accuracy": [], "completeness": [], "clarity": [], "faithfulness": [], "overall": []}
        modes_data[mode]["accuracy"].append(float(acc))
        modes_data[mode]["completeness"].append(float(comp))
        modes_data[mode]["clarity"].append(float(clar))
        modes_data[mode]["faithfulness"].append(float(faith))
        modes_data[mode]["overall"].append(float(overall))

    confidence_intervals = {mode: {dim: _ci95(vals) for dim, vals in dims.items()} for mode, dims in modes_data.items()}
    cross_validation = {mode: _cross_validate_mode(dims["overall"], mode) for mode, dims in modes_data.items()}

    signal_robustness: dict[str, dict] = {}
    for sig in _SCORING_SIGNALS:
        vals = all_signal_values[sig]
        if not vals:
            signal_robustness[sig] = {"cv": 0, "robust": True, "n": 0}
            continue
        mean = sum(vals) / len(vals)
        variance = sum((v - mean) ** 2 for v in vals) / max(1, len(vals) - 1)
        cv = math.sqrt(variance) / mean if mean > 0 else 0
        signal_robustness[sig] = {"cv": round(cv, 4), "robust": cv < 1.5, "n": len(vals)}

    return {
        "confidence_intervals": confidence_intervals,
        "cross_validation": cross_validation,
        "signal_robustness": signal_robustness,
        "total_evals": len(eval_rows),
    }


async def _audit_dept_eval_quality(db: AsyncSession) -> list[dict]:
    """Query per-department eval quality for fairness analysis."""
    dept_eval_result = await db.execute(text("""
        SELECT n.department, e.answer_mode,
               COUNT(*) as cnt,
               AVG(e.overall) as avg_overall,
               AVG(e.faithfulness) as avg_faith
        FROM eval_scores e
        JOIN queries q ON e.query_id = q.id
        JOIN LATERAL (
            SELECT DISTINCT n2.department
            FROM neurons n2
            WHERE n2.id = ANY(
                SELECT (jsonb_array_elements_text(q.selected_neuron_ids::jsonb))::int
            ) AND n2.department IS NOT NULL
        ) n ON true
        WHERE q.selected_neuron_ids IS NOT NULL
          AND q.selected_neuron_ids != '[]'
        GROUP BY n.department, e.answer_mode
        ORDER BY n.department, e.answer_mode
    """))
    return [
        {
            "department": row[0], "answer_mode": row[1], "eval_count": row[2],
            "avg_overall": round(float(row[3]), 2), "avg_faithfulness": round(float(row[4]), 2),
        }
        for row in dept_eval_result.fetchall()
    ]


def _audit_remediation(
    dept_cv: float, dept_counts: dict, dept_invocations: dict,
    total_neurons: int, dept_eval_quality: list[dict],
) -> list[dict]:
    """Generate automated remediation recommendations for coverage/quality/utilization gaps."""
    items: list[dict] = []

    if dept_cv > 0.5:
        fair_share = total_neurons / len(dept_counts) if dept_counts else 0
        for dept, count in dept_counts.items():
            if count < fair_share * 0.5:
                deficit = round(fair_share - count)
                items.append({
                    "type": "coverage_gap",
                    "severity": "high" if count < fair_share * 0.25 else "medium",
                    "department": dept,
                    "message": f"{dept} has {count} neurons ({round(count/total_neurons*100, 1)}% of total), well below fair share of ~{round(fair_share)}. Consider adding ~{deficit} neurons.",
                    "action": f"Review and ingest evidence targeting {dept} topics to grow coverage.",
                })

    if dept_eval_quality:
        neuron_evals = [d for d in dept_eval_quality if "neuron" in d["answer_mode"]]
        if len(neuron_evals) >= 2:
            avg_all = sum(d["avg_overall"] for d in neuron_evals) / len(neuron_evals)
            for d in neuron_evals:
                if d["avg_overall"] < avg_all - 0.5 and d["eval_count"] >= 3:
                    items.append({
                        "type": "quality_gap", "severity": "medium", "department": d["department"],
                        "message": f"{d['department']} neuron-assisted evals average {d['avg_overall']:.1f} vs system avg {avg_all:.1f} \u2014 content quality may need improvement.",
                        "action": f"Review and refine neuron content in {d['department']} department. Run targeted blind evals to confirm.",
                    })

    inv_values = [v for v in dept_invocations.values() if v > 0]
    if inv_values:
        median_inv = sorted(inv_values)[len(inv_values) // 2]
        for dept, inv in dept_invocations.items():
            if inv < median_inv * 0.1 and dept_counts.get(dept, 0) > 10:
                items.append({
                    "type": "utilization_gap", "severity": "low", "department": dept,
                    "message": f"{dept} has {dept_counts[dept]} neurons but only {inv} invocations (median is {median_inv}). Content may not match real queries.",
                    "action": f"Review {dept} neuron labels and summaries for relevance. Consider sample queries to test activation.",
                })

    return items


def _audit_fairness(coverage: dict, total_neurons: int, dept_eval_quality: list[dict]) -> dict:
    """Assemble fairness analysis section from coverage and eval quality data."""
    dept_invocations = coverage["dept_invocations"]
    dept_utility = coverage["dept_utility"]
    dept_cv = coverage["dept_cv"]

    inv_values = [v for v in dept_invocations.values() if v > 0]
    invocation_disparity = round(max(inv_values) / min(inv_values), 1) if len(inv_values) >= 2 and min(inv_values) > 0 else None

    dept_avg_utilities = [sum(dept_utility[d]) / len(dept_utility[d]) for d in dept_utility if dept_utility[d]]
    utility_range = round(max(dept_avg_utilities) - min(dept_avg_utilities), 4) if len(dept_avg_utilities) >= 2 else 0

    remediation_items = _audit_remediation(
        dept_cv, coverage["dept_counts"], dept_invocations, total_neurons, dept_eval_quality,
    )

    return {
        "department_eval_quality": dept_eval_quality,
        "invocation_disparity_ratio": invocation_disparity,
        "utility_range": utility_range,
        "coverage_cv": round(dept_cv, 3),
        "remediation_plan": remediation_items,
        "remediation_count": len(remediation_items),
        "fairness_pass": dept_cv <= 0.5 and len(remediation_items) == 0,
    }


async def run_compliance_audit(db: AsyncSession) -> dict:
    """Reusable compliance audit logic. Returns the full audit dict.

    Used by both the /compliance-audit endpoint and snapshot creation.
    Orchestrates per-check helpers for PII, bias, scoring, provenance,
    validity, and fairness.
    """
    result = await db.execute(select(Neuron).where(Neuron.is_active == True))
    neurons = result.scalars().all()
    total_neurons = len(neurons)

    pii_scan = _audit_pii_exposure(neurons)
    coverage = _audit_neuron_coverage(neurons, total_neurons)
    bias_assessment = await _audit_bias_assessment(db, coverage, total_neurons)
    scoring = await _audit_scoring_baselines(db)
    provenance = _audit_provenance(neurons)
    provenance["source_type_distribution"] = coverage["source_type_counts"]
    validity = await _audit_validity_reliability(db, scoring["all_signal_values"])
    dept_eval_quality = await _audit_dept_eval_quality(db)
    fairness = _audit_fairness(coverage, total_neurons, dept_eval_quality)

    # Remove internal-only key before returning
    scoring_baselines = {k: v for k, v in scoring.items() if k != "all_signal_values"}

    return {
        "total_neurons": total_neurons,
        "pii_scan": pii_scan,
        "bias_assessment": bias_assessment,
        "scoring_baselines": scoring_baselines,
        "provenance_audit": provenance,
        "validity_reliability": validity,
        "fairness_analysis": fairness,
    }


@router.get("/compliance-audit")
async def compliance_audit(db: AsyncSession = Depends(get_db)):
    """Comprehensive compliance audit covering MET-1 through MET-5, A007."""
    return await run_compliance_audit(db)


# ── Governance Dashboard: live metrics for AI objectives, change log, system health ──

async def _governance_overview(db: AsyncSession) -> dict:
    """Fetch total counts and quality KPIs for governance dashboard."""
    total_neurons = (await db.execute(select(func.count(Neuron.id)).where(Neuron.is_active == True))).scalar() or 0
    total_queries = (await db.execute(select(func.count(Query.id)))).scalar() or 0
    total_evals = (await db.execute(select(func.count(EvalScore.id)))).scalar() or 0
    total_refinements = (await db.execute(select(func.count(NeuronRefinement.id)))).scalar() or 0

    avg_eval = (await db.execute(select(func.avg(EvalScore.overall)))).scalar()
    avg_eval = round(float(avg_eval), 2) if avg_eval else None

    avg_faith = (await db.execute(select(func.avg(EvalScore.faithfulness)))).scalar()
    avg_faith = round(float(avg_faith), 2) if avg_faith else None

    avg_rating_result = await db.execute(
        select(func.avg(Query.user_rating)).where(Query.user_rating.isnot(None))
    )
    avg_rating = avg_rating_result.scalar()
    avg_rating = round(float(avg_rating), 2) if avg_rating else None
    rated_count = (await db.execute(
        select(func.count(Query.id)).where(Query.user_rating.isnot(None))
    )).scalar() or 0

    return {
        "totals": {
            "neurons": total_neurons, "queries": total_queries,
            "evaluations": total_evals, "refinements": total_refinements,
            "rated_queries": rated_count,
        },
        "quality": {
            "avg_eval": avg_eval, "avg_faith": avg_faith, "avg_rating": avg_rating,
        },
        "total_queries": total_queries,
    }


async def _governance_cost_kpis(db: AsyncSession, total_queries: int) -> dict:
    """Compute cost KPIs: total, per-query, per-1M tokens, run vs opus split."""
    total_cost = (await db.execute(select(func.sum(Query.cost_usd)))).scalar() or 0.0
    avg_cost = round(total_cost / total_queries, 6) if total_queries > 0 else 0.0
    total_tokens_result = await db.execute(select(
        func.sum(Query.classify_input_tokens + Query.execute_input_tokens
                 + Query.classify_output_tokens + Query.execute_output_tokens)
    ))
    total_tokens = total_tokens_result.scalar() or 0
    cost_per_1m = round(total_cost / total_tokens * 1_000_000, 2) if total_tokens > 0 else None

    slot_cost_result = await db.execute(text("""
        SELECT
            SUM(CASE WHEN (slot->>'mode') LIKE '%opus%' THEN (slot->>'cost_usd')::float ELSE 0 END) as opus_cost,
            SUM(CASE WHEN (slot->>'mode') LIKE '%opus%'
                THEN (slot->>'input_tokens')::float + (slot->>'output_tokens')::float ELSE 0 END) as opus_tokens,
            SUM(CASE WHEN (slot->>'mode') NOT LIKE '%opus%' THEN (slot->>'cost_usd')::float ELSE 0 END) as run_slot_cost,
            SUM(CASE WHEN (slot->>'mode') NOT LIKE '%opus%'
                THEN (slot->>'input_tokens')::float + (slot->>'output_tokens')::float ELSE 0 END) as run_slot_tokens
        FROM queries, jsonb_array_elements(queries.results_json::jsonb) AS slot
        WHERE queries.results_json IS NOT NULL
    """))
    opus_cost_total, opus_token_total, run_slot_cost, run_slot_tokens = slot_cost_result.one()
    opus_cost_1m = round(float(opus_cost_total) / float(opus_token_total) * 1_000_000, 2) if opus_token_total and opus_token_total > 0 else None

    classify_cost_result = await db.execute(text("""
        SELECT COALESCE(SUM(classify_input_tokens + classify_output_tokens), 0) FROM queries
    """))
    classify_tokens = float(classify_cost_result.scalar() or 0)
    classify_cost_est_result = await db.execute(text("""
        SELECT COALESCE(SUM(
            classify_input_tokens * 1.00 / 1000000.0 +
            classify_output_tokens * 5.00 / 1000000.0
        ), 0) FROM queries
    """))
    classify_cost_est = float(classify_cost_est_result.scalar() or 0)

    run_total_cost = (run_slot_cost or 0) + classify_cost_est
    run_total_tokens = (run_slot_tokens or 0) + classify_tokens
    run_cost_per_1m = round(run_total_cost / run_total_tokens * 1_000_000, 2) if run_total_tokens > 0 else None

    return {
        "avg_cost": avg_cost, "total_cost": round(total_cost, 4),
        "cost_per_1m": cost_per_1m, "run_cost_per_1m": run_cost_per_1m,
        "opus_cost_1m": opus_cost_1m,
    }


async def _governance_parity(db: AsyncSession, run_cost_per_1m, opus_cost_1m) -> dict:
    """Compute parity index and value score KPIs."""
    opus_eval_result = await db.execute(
        select(func.avg(EvalScore.overall)).where(EvalScore.answer_mode.like("opus_%"))
    )
    avg_opus_eval = opus_eval_result.scalar()

    neuron_eval_result = await db.execute(
        select(func.avg(EvalScore.overall)).where(EvalScore.answer_mode.like("%_neuron"))
    )
    avg_neuron_eval = neuron_eval_result.scalar()

    parity_index = round(float(avg_neuron_eval) / float(avg_opus_eval), 3) if avg_neuron_eval and avg_opus_eval else None
    value_score = None
    if avg_neuron_eval and run_cost_per_1m and opus_cost_1m and opus_cost_1m > 0:
        value_score = round((float(avg_neuron_eval) / 5.0) / (run_cost_per_1m / opus_cost_1m), 2)

    return {
        "parity_index": parity_index, "value_score": value_score,
        "avg_opus_eval": round(float(avg_opus_eval), 2) if avg_opus_eval else None,
        "avg_neuron_eval": round(float(avg_neuron_eval), 2) if avg_neuron_eval else None,
    }


async def _governance_compliance(db: AsyncSession) -> dict:
    """Fetch coverage CV, zero-hit rate, change activity, and alert count."""
    dept_count = (await db.execute(
        select(func.count(func.distinct(Neuron.department)))
        .where(Neuron.department.isnot(None), Neuron.is_active == True)
    )).scalar() or 0

    dept_neuron_counts = await db.execute(
        select(Neuron.department, func.count(Neuron.id))
        .where(Neuron.is_active == True, Neuron.department.isnot(None))
        .group_by(Neuron.department)
    )
    dept_vals = [row[1] for row in dept_neuron_counts.all()]
    if len(dept_vals) >= 2:
        d_mean = sum(dept_vals) / len(dept_vals)
        d_var = sum((v - d_mean) ** 2 for v in dept_vals) / (len(dept_vals) - 1)
        coverage_cv = round(math.sqrt(d_var) / d_mean, 3) if d_mean > 0 else 0.0
    else:
        coverage_cv = 0.0

    recent_q = await db.execute(
        select(Query.selected_neuron_ids).order_by(Query.id.desc()).limit(50)
    )
    recent_rows = recent_q.all()
    zero_hits = sum(1 for (ids,) in recent_rows if not ids or ids == "[]") if recent_rows else 0
    zero_hit_pct = round(zero_hits / len(recent_rows) * 100, 1) if recent_rows else 0

    thirty_days_ago = datetime.utcnow() - timedelta(days=30)
    recent_refinements = (await db.execute(
        select(func.count(NeuronRefinement.id)).where(NeuronRefinement.created_at >= thirty_days_ago)
    )).scalar() or 0
    recent_autopilot = (await db.execute(
        select(func.count(AutopilotRun.id)).where(AutopilotRun.created_at >= thirty_days_ago)
    )).scalar() or 0

    recent_changes_q = await db.execute(
        select(NeuronRefinement.id, NeuronRefinement.action, NeuronRefinement.field,
               NeuronRefinement.reason, NeuronRefinement.neuron_id, NeuronRefinement.created_at)
        .order_by(NeuronRefinement.created_at.desc()).limit(10)
    )
    recent_changes = [
        {"id": r[0], "action": r[1], "field": r[2], "reason": (r[3] or "")[:120],
         "neuron_id": r[4], "created_at": r[5].isoformat() if r[5] else None}
        for r in recent_changes_q.all()
    ]

    active_alert_count = (await db.execute(
        select(func.count(SystemAlert.id)).where(SystemAlert.acknowledged == False)
    )).scalar() or 0

    return {
        "dept_count": dept_count, "coverage_cv": coverage_cv,
        "zero_hit_pct": zero_hit_pct,
        "change_activity": {
            "refinements_30d": recent_refinements,
            "autopilot_runs_30d": recent_autopilot,
            "recent_changes": recent_changes,
        },
        "active_alerts": active_alert_count,
    }


@router.get("/governance-dashboard")
async def governance_dashboard(db: AsyncSession = Depends(get_db)):
    """Aggregate live governance metrics: AI objectives progress, change activity, quality trends.

    Feeds the Governance page with computed KPI status against defined targets.
    """
    overview = await _governance_overview(db)
    cost = await _governance_cost_kpis(db, overview["total_queries"])
    parity = await _governance_parity(db, cost["run_cost_per_1m"], cost["opus_cost_1m"])
    compliance = await _governance_compliance(db)

    return {
        "totals": {
            **overview["totals"],
            "departments": compliance["dept_count"],
        },
        "kpis": {
            "avg_eval_overall": overview["quality"]["avg_eval"],
            "avg_faithfulness": overview["quality"]["avg_faith"],
            "avg_user_rating": overview["quality"]["avg_rating"],
            "avg_cost_per_query": cost["avg_cost"],
            "total_cost_usd": cost["total_cost"],
            "cost_per_1m_tokens": cost["cost_per_1m"],
            "run_cost_per_1m": cost["run_cost_per_1m"],
            "zero_hit_pct": compliance["zero_hit_pct"],
            "parity_index": parity["parity_index"],
            "value_score": parity["value_score"],
            "avg_opus_eval": parity["avg_opus_eval"],
            "avg_neuron_eval": parity["avg_neuron_eval"],
            "opus_cost_per_1m": round(cost["opus_cost_1m"], 2) if cost["opus_cost_1m"] else None,
            "coverage_cv": compliance["coverage_cv"],
        },
        "change_activity": compliance["change_activity"],
        "active_alerts": compliance["active_alerts"],
    }


@router.post("/embed-neurons")
async def embed_neurons(force: bool = False, db: AsyncSession = Depends(get_db)):
    """Generate semantic embeddings for all neurons.

    Runs sentence-transformers/all-MiniLM-L6-v2 locally to produce 384-dim
    vectors stored on each neuron. These enable semantic similarity scoring
    (cortical topography) instead of keyword matching.

    Pass force=true to re-embed neurons that already have embeddings.
    """
    import concurrent.futures
    from app.services.embedding_service import embed_batch

    # Load neurons needing embeddings
    if force:
        result = await db.execute(
            select(Neuron).where(Neuron.is_active == True)
        )
    else:
        result = await db.execute(
            select(Neuron).where(Neuron.is_active == True, Neuron.embedding.is_(None))
        )
    neurons = result.scalars().all()

    if not neurons:
        return {"embedded": 0, "message": "All neurons already have embeddings"}

    # Build text blobs: label + content (same text used for scoring)
    texts = []
    for n in neurons:
        parts = [n.label or ""]
        if n.content:
            parts.append(n.content[:1000])  # Cap content to avoid huge inputs
        texts.append(" ".join(parts).strip() or "empty")

    # Run embedding in thread pool (CPU-bound, don't block event loop)
    loop = asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        embeddings = await loop.run_in_executor(pool, embed_batch, texts)

    # Store embeddings
    for neuron, emb in zip(neurons, embeddings):
        neuron.embedding = json.dumps(emb)

    await db.commit()

    # Invalidate the semantic prefilter cache so it reloads with new embeddings
    from app.services.semantic_prefilter import invalidate_cache
    invalidate_cache()

    return {
        "embedded": len(neurons),
        "dimensions": len(embeddings[0]) if embeddings else 0,
        "message": f"Embedded {len(neurons)} neurons with 384-dim vectors",
    }


@router.post("/seed-inhibitory-regulators")
async def seed_inhibitory_regulators_endpoint(db: AsyncSession = Depends(get_db)):
    """Create inhibitory regulators (GABAergic interneurons) for each department and high-volume role."""
    from app.services.inhibitory_service import seed_inhibitory_regulators
    created = await seed_inhibitory_regulators(db)
    await db.commit()
    return {"created": created, "message": f"Seeded {created} inhibitory regulators"}


@router.get("/inhibitory-regulators")
async def list_inhibitory_regulators(db: AsyncSession = Depends(get_db)):
    """List all inhibitory regulators with their stats."""
    from app.models import InhibitoryRegulator
    result = await db.execute(select(InhibitoryRegulator).order_by(InhibitoryRegulator.region_type, InhibitoryRegulator.region_value))
    regulators = result.scalars().all()
    return [
        {
            "id": r.id,
            "region_type": r.region_type,
            "region_value": r.region_value,
            "inhibition_strength": r.inhibition_strength,
            "activation_threshold": r.activation_threshold,
            "max_survivors": r.max_survivors,
            "redundancy_cosine_threshold": r.redundancy_cosine_threshold,
            "total_suppressions": r.total_suppressions,
            "total_activations": r.total_activations,
            "avg_post_suppression_utility": r.avg_post_suppression_utility,
            "is_active": r.is_active,
        }
        for r in regulators
    ]


@router.post("/classify-edges")
async def classify_edges(db: AsyncSession = Depends(get_db)):
    """Classify existing co-firing edges as stellate (intra-department) or pyramidal (cross-department).

    Looks up the department of each edge's source and target neurons.
    Same department = stellate (local processor), different = pyramidal (long-range).
    """
    # Reclassify all edges except 'instantiates' (concept neuron edges are manually typed)
    result = await db.execute(text("""
        UPDATE neuron_edges e
        SET edge_type = CASE
            WHEN src.department = tgt.department THEN 'stellate'
            ELSE 'pyramidal'
        END
        FROM neurons src, neurons tgt
        WHERE e.source_id = src.id AND e.target_id = tgt.id
          AND (e.edge_type IS NULL OR e.edge_type != 'instantiates')
        RETURNING e.source_id, e.target_id, e.edge_type
    """))
    rows = result.all()
    await db.commit()

    stellate = sum(1 for _, _, t in rows if t == "stellate")
    pyramidal = sum(1 for _, _, t in rows if t == "pyramidal")

    return {
        "classified": len(rows),
        "stellate": stellate,
        "pyramidal": pyramidal,
        "message": f"Classified {len(rows)} edges: {stellate} stellate, {pyramidal} pyramidal",
    }


# ── Concept Neurons ──


@router.get("/concept-neurons")
async def list_concept_neurons(db: AsyncSession = Depends(get_db)):
    """List all concept neurons with their instantiation edge counts."""
    from app.services.concept_service import get_concept_neurons
    return await get_concept_neurons(db)


@router.post("/concept-neurons")
async def create_concept_neuron_endpoint(
    label: str,
    content: str,
    summary: str | None = None,
    db: AsyncSession = Depends(get_db),
    identity: UserIdentity = Depends(require_role("admin")),
):
    """Create a new concept neuron (layer=-1, department-agnostic)."""
    from app.services.concept_service import create_concept_neuron
    from app.services.semantic_prefilter import update_cache_incremental

    neuron = await create_concept_neuron(
        db, label, content, summary,
        actor=identity, actor_type="user",
    )
    await db.commit()
    await update_cache_incremental(db, [neuron.id])

    return {
        "id": neuron.id,
        "label": neuron.label,
        "node_type": neuron.node_type,
        "layer": neuron.layer,
        "message": f"Created concept neuron #{neuron.id}: {neuron.label}",
    }


@router.post("/concept-neurons/{concept_id}/link")
async def link_concept_neuron(
    concept_id: int,
    target_ids: list[int],
    weight: float = 0.5,
    db: AsyncSession = Depends(get_db),
    identity: UserIdentity = Depends(require_role("admin")),
):
    """Create instantiation edges from a concept neuron to target neurons."""
    from app.services.concept_service import link_concept_to_neurons

    # Verify concept neuron exists
    concept = await db.get(Neuron, concept_id)
    if not concept or concept.node_type != "concept":
        raise HTTPException(status_code=404, detail=f"Concept neuron #{concept_id} not found")

    count = await link_concept_to_neurons(
        db, concept_id, target_ids, weight,
        concept_label=concept.label,
        actor=identity, actor_type="user",
    )
    await db.commit()
    from app.services.adjacency_cache import invalidate_adjacency_cache
    invalidate_adjacency_cache()

    return {
        "concept_id": concept_id,
        "edges_created": count,
        "message": f"Linked {count} neurons to concept #{concept_id}",
    }


@router.post("/concept-neurons/seed-three-horizons")
async def seed_three_horizons_endpoint(db: AsyncSession = Depends(get_db)):
    """Seed the Three Horizons framework as a concept neuron with instantiation edges."""
    from app.services.concept_service import seed_three_horizons
    return await seed_three_horizons(db)


@router.post("/concept-neurons/seed-all")
async def seed_all_concepts_endpoint(db: AsyncSession = Depends(get_db)):
    """Seed all concept neurons from the built-in registry (idempotent — skips existing)."""
    from app.services.concept_service import seed_all_concepts
    return await seed_all_concepts(db)


@router.post("/concept-neurons/relink")
async def relink_concepts_endpoint(db: AsyncSession = Depends(get_db)):
    """Re-run pattern matching for all existing concept neurons.

    Catches neurons missed by original seeding (e.g., label-only matches).
    Uses upsert — existing edges keep their weight if higher.
    """
    from app.services.concept_service import relink_existing_concepts
    return await relink_existing_concepts(db)


@router.post("/bootstrap-firings")
async def bootstrap_firings_endpoint(dry_run: bool = False, db: AsyncSession = Depends(get_db)):
    """Pre-seed co-firing edges and invocation estimates based on structural/semantic priors.

    All edges tagged source='bootstrap' for traceability.
    Pass dry_run=true to preview without writing.
    """
    from app.services.bootstrap_service import bootstrap_firings
    return await bootstrap_firings(db, dry_run=dry_run)


@router.get("/bootstrap-stats")
async def bootstrap_stats_endpoint(db: AsyncSession = Depends(get_db)):
    """Return statistics about bootstrap vs organic edge provenance."""
    from app.services.bootstrap_service import get_bootstrap_stats
    return await get_bootstrap_stats(db)


@router.post("/purge-bootstrap")
async def purge_bootstrap_endpoint(db: AsyncSession = Depends(get_db)):
    """Remove all bootstrap-sourced edges and reset bootstrap-only invocations.

    Use if bootstrap priors are causing unwanted bias.
    """
    from app.services.bootstrap_service import purge_bootstrap
    return await purge_bootstrap(db)


@router.post("/intra-department-bridge")
async def intra_department_bridge_endpoint(
    similarity_threshold: float = 0.45,
    dry_run: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Bridge role clusters within the same department using embedding similarity.

    Creates cross-role edges where neurons are semantically similar but lack
    connections due to role boundary isolation. Fixes intra-department
    fragmentation (e.g., Finance/cost_estimator isolated from Finance/financial_analyst).

    Args:
        similarity_threshold: Cosine similarity cutoff (0-1). Lower = more edges. Default 0.45.
        dry_run: Preview without writing.
    """
    from app.services.bootstrap_service import intra_department_bridge
    return await intra_department_bridge(db, similarity_threshold=similarity_threshold, dry_run=dry_run)
