"""Regulatory resolve: serve fired engrams' regulatory text from the LOCAL cache.

The query hot path (resolve_engrams) is cache-only — it never touches the eCFR
API. The live API is confined to a scheduled job (warm_engram_cache) that rides
the consolidation heartbeat and materializes every engram's text into the
engram.cached_text column, exactly like the neuron index materializes scoring
inputs. This keeps a 30s-timeout network call (x N engrams, formerly sequential)
off every query. CFR text is stable, so serving cache regardless of TTL on the
hot path is safe; freshness is the warm job's responsibility.
"""

import asyncio
import datetime
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Engram
from app.services.ecfr_client import get_ecfr_client, estimate_tokens


@dataclass
class ResolvedRegulation:
    """A single resolved regulatory text from an engram."""
    engram_id: int
    cfr_ref: str              # e.g. "48 CFR 31.205-14"
    text: str                 # full or extracted section text
    token_count: int
    source: str               # "live_api" | "cache" | "fallback_summary"
    fetched_at: datetime.datetime


def _cfr_ref(engram: Engram) -> str:
    """Build a human-readable CFR reference string."""
    section = f".{engram.cfr_section}" if engram.cfr_section else ""
    return f"{engram.cfr_title} CFR {engram.cfr_part}{section}"


def _cache_is_valid(engram: Engram) -> bool:
    """Check if the engram's cached text is still within TTL."""
    if not engram.cached_text or not engram.cached_at:
        return False
    ttl_hours = engram.cache_ttl_hours or settings.engram_cache_ttl_hours
    expiry = engram.cached_at + datetime.timedelta(hours=ttl_hours)
    return datetime.datetime.utcnow() < expiry


async def resolve_engrams(
    db: AsyncSession,
    fired_engrams: list[tuple[Engram, float]],
    token_budget: int,
) -> list[ResolvedRegulation]:
    """Assemble fired engrams' regulatory text from the LOCAL cache only.

    Cache-only hot path: no eCFR API call, no writes — pure DB reads. Serves
    engram.cached_text (populated by warm_engram_cache) regardless of TTL, with
    the engram summary as fallback when the section is too large for the budget
    or not yet warmed. Highest-scored engrams first; stops at the token budget.
    """
    if not fired_engrams:
        return []

    fired_engrams.sort(key=lambda pair: pair[1], reverse=True)
    results: list[ResolvedRegulation] = []
    tokens_used = 0

    for engram, _score in fired_engrams:
        ref = _cfr_ref(engram)
        stamp = engram.cached_at or datetime.datetime.utcnow()

        # Prefer full cached text.
        if engram.cached_text:
            tc = engram.cached_token_count or estimate_tokens(engram.cached_text)
            if tokens_used + tc <= token_budget:
                results.append(ResolvedRegulation(
                    engram_id=engram.id, cfr_ref=ref, text=engram.cached_text,
                    token_count=tc, source="cache", fetched_at=stamp,
                ))
                tokens_used += tc
                continue

        # Fallback: summary (section too large for remaining budget, or not yet
        # warmed into the cache — the next warm job will populate it).
        if engram.summary:
            note = "full text cached" if engram.cached_text else "pending cache"
            summary_tc = estimate_tokens(engram.summary)
            if tokens_used + summary_tc <= token_budget:
                results.append(ResolvedRegulation(
                    engram_id=engram.id, cfr_ref=ref,
                    text=f"[Summary — {note}] {engram.summary}",
                    token_count=summary_tc, source="fallback_summary", fetched_at=stamp,
                ))
                tokens_used += summary_tc

    return results


async def warm_engram_cache(db: AsyncSession, force: bool = False) -> dict:
    """Materialize eCFR text for all active engrams into the local cache.

    The daily/heartbeat job that keeps the live API off the query hot path.
    Concurrency-bounded by settings.engram_max_concurrent_fetches; refreshes only
    stale entries (cache older than TTL) unless force=True. Best-effort: a failed
    fetch leaves the prior cache intact so resolve_engrams keeps serving it.
    """
    from sqlalchemy import select
    from app.models import Engram

    engrams = list((await db.execute(
        select(Engram).where(Engram.is_active == True)  # noqa: E712
    )).scalars().all())
    client = get_ecfr_client()  # already bounds concurrency (its own semaphore)
    stats = {"total": len(engrams), "fetched": 0, "failed": 0, "fresh": 0}
    now = datetime.datetime.utcnow()

    async def _warm(engram: Engram) -> None:
        if not force and _cache_is_valid(engram):
            stats["fresh"] += 1
            return
        try:
            text_content = await client.fetch_section(
                title=engram.cfr_title, part=engram.cfr_part,
                section=engram.cfr_section,
            )
        except Exception:  # network/parse failure -> keep prior cache
            text_content = None
        if text_content:
            engram.cached_text = text_content
            engram.cached_at = now
            engram.cached_token_count = estimate_tokens(text_content)
            engram.last_verified = now
            stats["fetched"] += 1
        else:
            stats["failed"] += 1

    await asyncio.gather(*[_warm(e) for e in engrams])
    await db.flush()
    return stats


# Fire-and-forget warm scheduling. Module ref prevents mid-flight GC of the task.
_warm_task = None


def schedule_engram_cache_warm(force: bool = False) -> bool:
    """Schedule warm_engram_cache as a background task on its OWN db session.

    Fire-and-forget: returns immediately so a slow/large eCFR fetch never blocks
    the caller (e.g. the consolidation heartbeat). No-op (returns False) if a warm
    is already in flight or no event loop is running (sync/test context).
    """
    global _warm_task
    if _warm_task is not None and not _warm_task.done():
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    _warm_task = loop.create_task(_warm_in_background(force))
    return True


async def _warm_in_background(force: bool) -> None:
    """Run the warm job on a fresh session (the caller's is already committed/closed)."""
    import logging
    from app.database import async_session
    try:
        async with async_session() as db:
            await warm_engram_cache(db, force=force)
            await db.commit()
    except Exception as exc:  # background job: log, never propagate into the loop
        logging.getLogger(__name__).warning("engram cache warm failed: %s", exc)
