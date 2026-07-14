"""Backfill neurons.entities for lessons written before mind-hybrid-recall.

New writes get entities from the distiller's existing LLM call; this script
covers the pre-existing corpus with batched Opus calls (quality-first policy
for rarely-run backend maintenance). Idempotent: only touches rows where
entities IS NULL; safe to re-run after a crash. Entities are derived
retrieval metadata over existing content — label/content/summary are never
modified.

Run (from backend/, venv active):
  TENANT_ID=corvus-locomo PYTHONPATH=. venv/bin/python scripts/backfill_entities.py
Flags: --batch-size 20 --concurrency 2 --dry-run
"""

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

EXTRACT_PROMPT = """You extract named entities from memory records for a retrieval index.

INPUT: a JSON array of records, each {"id": <int>, "text": "<label + content>"}.

For each record, list the named things the record is about: people, pets, places, organizations, projects, tools, foods, and quoted titles of books/movies/games/songs (title text only, no quotes). Only names that actually appear in the text — never infer. Use [] when a record names nothing.

Respond with ONLY a JSON object mapping id to entity list, no prose:
{"<id>": ["<entity>", ...], ...}"""

BACKFILL_MODEL = os.environ.get("BACKFILL_MODEL", "opus")


async def fetch_pending(batch_size: int) -> list[tuple[int, str]]:
    """Rows still lacking entities, oldest first."""
    from sqlalchemy import text
    from app.database import async_session
    async with async_session() as db:
        rows = await db.execute(text(
            "SELECT id, label, coalesce(content, ''), coalesce(summary, '') "
            "FROM neurons WHERE entities IS NULL AND is_active = true "
            "ORDER BY id LIMIT :n"), {"n": batch_size * 50})
    out = []
    for r in rows.all():
        text_body = f"{r[1]}. {r[2] or r[3]}".split("Evidence:")[0].strip()[:1200]
        out.append((r[0], text_body))
    return out


async def extract_batch(batch: list[tuple[int, str]]) -> dict[int, list[str]]:
    """One Opus call → {neuron_id: entities}. Empty dict on parse failure."""
    from app.services.llm_provider import llm_chat
    payload = json.dumps([{"id": nid, "text": txt} for nid, txt in batch])
    reply = await llm_chat(system_prompt=EXTRACT_PROMPT, user_message=payload,
                           max_tokens=4000, model=BACKFILL_MODEL, timeout=600)
    raw = reply.get("text", "")
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        parsed = json.loads(raw[start:end + 1])
    except ValueError:
        return {}
    out: dict[int, list[str]] = {}
    for key, val in parsed.items():
        if isinstance(val, list):
            try:
                out[int(key)] = [str(v) for v in val]
            except ValueError:
                continue
    return out


async def apply_entities(extracted: dict[int, list[str]], dry_run: bool) -> int:
    """Write normalized entity arrays; [] marks 'extracted, none found' so the
    row leaves the IS NULL pending set."""
    from sqlalchemy import text
    from app.database import async_session
    from app.services.recall_lanes import normalize_entities
    if dry_run:
        for nid, ents in list(extracted.items())[:10]:
            print(f"  [dry-run] neuron {nid}: {normalize_entities(ents)}")
        return 0
    written = 0
    async with async_session() as db:
        for nid, ents in extracted.items():
            await db.execute(text(
                "UPDATE neurons SET entities = CAST(:ents AS jsonb) "
                "WHERE id = :nid AND entities IS NULL"),
                {"ents": json.dumps(normalize_entities(ents)), "nid": nid})
            written += 1
        await db.commit()
    return written


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=20)
    ap.add_argument("--concurrency", type=int, default=2)  # 6.5GB RAM: max 2 CLI subprocesses
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    assert os.environ.get("TENANT_ID"), "set TENANT_ID for the target tenant DB"
    assert 1 <= args.concurrency <= 2, "max 2 concurrent CLI subprocesses (OOM)"

    total = 0
    for round_num in range(200):  # bounded (JPL-2); each round drains up to 50 batches
        pending = await fetch_pending(args.batch_size)
        if not pending:
            break
        batches = [pending[i:i + args.batch_size]
                   for i in range(0, len(pending), args.batch_size)]
        sem = asyncio.Semaphore(args.concurrency)

        async def one(batch: list[tuple[int, str]]) -> int:
            async with sem:
                extracted = await extract_batch(batch)
                if not extracted:
                    print(f"  [warn] batch of {len(batch)} returned no parseable "
                          "entities; will retry next round", flush=True)
                    return 0
                return await apply_entities(extracted, args.dry_run)

        counts = await asyncio.gather(*[one(b) for b in batches])
        total += sum(counts)
        print(f"[round {round_num}] {sum(counts)} neurons updated "
              f"(total {total})", flush=True)
        if args.dry_run or sum(counts) == 0:
            break
    print(f"[done] {total} neurons backfilled", flush=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
