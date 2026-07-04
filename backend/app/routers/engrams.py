"""Admin API endpoints for engram management."""

import json
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Engram, EngramEdge, EmergentQueue, SystemState
from app.services.engram_service import list_engrams, get_engram
from app.services.ecfr_client import get_ecfr_client, estimate_tokens
from app.seed.engram_loader import load_engram_seeds, auto_embed_engrams

router = APIRouter(prefix="/engrams", tags=["engrams"])


@router.get("/")
async def get_all_engrams(db: AsyncSession = Depends(get_db)):
    """List all active engrams with cache status and neuron-association counts."""
    engrams = await list_engrams(db)
    edge_rows = (await db.execute(
        select(EngramEdge.engram_id, func.count()).group_by(EngramEdge.engram_id)
    )).all()
    linked = {row[0]: row[1] for row in edge_rows}
    return [
        {
            "id": e.id,
            "label": e.label,
            "summary": e.summary,
            "cfr_title": e.cfr_title,
            "cfr_part": e.cfr_part,
            "cfr_section": e.cfr_section,
            "source_api": e.source_api,
            "authority_level": e.authority_level,
            "issuing_body": e.issuing_body,
            "invocations": e.invocations,
            "avg_utility": e.avg_utility,
            "cached": e.cached_text is not None,
            "cached_at": e.cached_at.isoformat() if e.cached_at else None,
            "cached_token_count": e.cached_token_count,
            "has_embedding": e.embedding is not None and e.embedding != "",
            "linked_neurons": linked.get(e.id, 0),
            "is_active": e.is_active,
        }
        for e in engrams
    ]


@router.get("/{engram_id}")
async def get_engram_detail(engram_id: int, db: AsyncSession = Depends(get_db)):
    """Get full engram detail including cache status."""
    e = await get_engram(db, engram_id)
    if e is None:
        return {"error": "Engram not found"}
    return {
        "id": e.id,
        "label": e.label,
        "summary": e.summary,
        "content": e.content,
        "cfr_title": e.cfr_title,
        "cfr_part": e.cfr_part,
        "cfr_section": e.cfr_section,
        "source_api": e.source_api,
        "authority_level": e.authority_level,
        "issuing_body": e.issuing_body,
        "effective_date": e.effective_date.isoformat() if e.effective_date else None,
        "invocations": e.invocations,
        "avg_utility": e.avg_utility,
        "cached": e.cached_text is not None,
        "cached_at": e.cached_at.isoformat() if e.cached_at else None,
        "cached_token_count": e.cached_token_count,
        "cached_text_preview": (e.cached_text[:500] + "...") if e.cached_text and len(e.cached_text) > 500 else e.cached_text,
        "has_embedding": e.embedding is not None and e.embedding != "",
        "last_verified": e.last_verified.isoformat() if e.last_verified else None,
        "is_active": e.is_active,
    }


@router.post("/seed")
async def seed_engrams(force: bool = False, db: AsyncSession = Depends(get_db)):
    """Seed engrams from tenant configuration."""
    result = await load_engram_seeds(db, force=force)
    return result


@router.post("/embed")
async def embed_engrams(db: AsyncSession = Depends(get_db)):
    """Generate embeddings for all engrams missing them."""
    count = await auto_embed_engrams(db)
    return {"embedded": count}


@router.post("/{engram_id}/resolve")
async def resolve_engram(engram_id: int, db: AsyncSession = Depends(get_db)):
    """Test-fetch regulatory text from eCFR for a specific engram."""
    e = await get_engram(db, engram_id)
    if e is None:
        return {"error": "Engram not found"}

    client = get_ecfr_client()
    text_content = await client.fetch_section(
        title=e.cfr_title,
        part=e.cfr_part,
        section=e.cfr_section,
    )

    if text_content is None:
        return {"error": "eCFR API returned no content", "engram_id": engram_id}

    tc = estimate_tokens(text_content)
    now = datetime.utcnow()

    # Update cache
    e.cached_text = text_content
    e.cached_at = now
    e.cached_token_count = tc
    e.last_verified = now
    await db.commit()

    section_ref = f"{e.cfr_title} CFR {e.cfr_part}"
    if e.cfr_section:
        section_ref += f".{e.cfr_section}"

    return {
        "engram_id": engram_id,
        "cfr_ref": section_ref,
        "token_count": tc,
        "text_preview": text_content[:500] + ("..." if len(text_content) > 500 else ""),
        "cached_at": now.isoformat(),
    }


@router.get("/stats/summary")
async def engram_stats(db: AsyncSession = Depends(get_db)):
    """Summary statistics for engrams."""
    total = (await db.execute(select(func.count(Engram.id)))).scalar() or 0
    active = (await db.execute(
        select(func.count(Engram.id)).where(Engram.is_active == True)  # noqa: E712
    )).scalar() or 0
    cached = (await db.execute(
        select(func.count(Engram.id)).where(Engram.cached_text != None)  # noqa: E711
    )).scalar() or 0
    embedded = (await db.execute(
        select(func.count(Engram.id)).where(
            Engram.embedding != None,  # noqa: E711
            Engram.embedding != "",
        )
    )).scalar() or 0

    return {
        "total": total,
        "active": active,
        "cached": cached,
        "embedded": embedded,
    }


@router.get("/coverage/gaps")
async def coverage_gaps(db: AsyncSession = Depends(get_db)):
    """Regulations cited in answers but NOT covered by an engram — candidates to seed.

    Reads the tail of the coverage-gap detector: the EmergentQueue
    (domain='regulatory'), i.e. every un-grounded CFR reference an answer produced.
    """
    rows = (await db.execute(
        select(EmergentQueue)
        .where(
            EmergentQueue.domain == "regulatory",
            EmergentQueue.status.in_(("pending", "reviewing")),
        )
        .order_by(EmergentQueue.detection_count.desc(), EmergentQueue.last_detected_at.desc())
    )).scalars().all()

    out = []
    for g in rows:
        try:
            qids = json.loads(g.detected_in_query_ids or "[]")
        except (json.JSONDecodeError, TypeError):
            qids = []
        out.append({
            "id": g.id,
            "citation_pattern": g.citation_pattern,
            "family": g.family,
            "detection_count": g.detection_count,
            "query_count": len(qids),
            "status": g.status,
            "first_detected_at": g.first_detected_at.isoformat() if g.first_detected_at else None,
            "last_detected_at": g.last_detected_at.isoformat() if g.last_detected_at else None,
        })
    return out


class EngramCreate(BaseModel):
    """Payload for seeding a new engram (optionally from a coverage gap)."""
    cfr_title: int
    cfr_part: str
    cfr_section: str | None = None
    label: str
    summary: str | None = None
    content: str | None = None
    authority_level: str = "regulatory"
    issuing_body: str | None = None
    source_api: str = "ecfr"
    gap_id: int | None = None


@router.post("/create")
async def create_engram(body: EngramCreate, db: AsyncSession = Depends(get_db)):
    """Create + embed a new engram. If gap_id is given, mark that coverage gap resolved."""
    label = body.label.strip()
    part = body.cfr_part.strip()
    section = (body.cfr_section or "").strip() or None
    if not label or not part:
        return {"error": "label and cfr_part are required"}

    same = (await db.execute(select(Engram).where(
        Engram.cfr_title == body.cfr_title, Engram.cfr_part == part,
    ))).scalars().all()
    if any((e.cfr_section or None) == section for e in same):
        return {"error": "an engram already exists for that CFR reference"}

    state = (await db.execute(select(SystemState).where(SystemState.id == 1))).scalar_one_or_none()
    engram = Engram(
        label=label, summary=body.summary, content=body.content,
        cfr_title=body.cfr_title, cfr_part=part, cfr_section=section,
        source_api=(body.source_api or "ecfr").strip(),
        authority_level=(body.authority_level or "regulatory").strip(),
        issuing_body=body.issuing_body,
        created_at_query_count=state.total_queries if state else 0,
    )
    db.add(engram)
    await db.flush()

    if body.gap_id is not None:
        gap = await db.get(EmergentQueue, body.gap_id)
        if gap is not None and gap.domain == "regulatory":
            gap.status = "resolved"
            gap.resolved_at = datetime.utcnow()

    await db.commit()
    embedded = await auto_embed_engrams(db)
    return {"status": "created", "engram_id": engram.id, "label": label, "embedded": embedded > 0}
