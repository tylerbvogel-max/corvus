"""Backfill Query.cost_usd + per-slot cost_usd for all historical queries.

The bug: prior to 2026-04-19, `_anthropic_chat` hardcoded `cost_usd: 0.0`, so
any query whose slots ran through the Claude CLI stored zero cost. This script
recomputes costs from stored token counts + MODEL_REGISTRY prices and writes
them back. Idempotent — re-running on already-correct rows is a no-op.

Coverage per row:
- Each slot in results_json → cost_usd recomputed from model + tokens.
  Anthropic: cache-aware (1.25x create, 0.10x read). Others: flat estimate.
- Classify pass → haiku prices × classify_input/output_tokens.
- Self-eval pass → query.eval_model prices × eval_input/output_tokens.
- Query.cost_usd = classify + eval + sum(slot costs).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.database import async_session
from app.models import Query
from app.services.llm_provider import (
    MODEL_REGISTRY,
    _estimate_cost_anthropic,
    estimate_cost,
)


def _slot_cost(slot: dict) -> float:
    model = slot.get("model")
    if not model:
        return 0.0
    info = MODEL_REGISTRY.get(model)
    if not info:
        return 0.0
    in_tok = int(slot.get("input_tokens") or 0)
    out_tok = int(slot.get("output_tokens") or 0)
    if info.provider == "anthropic":
        cc = int(slot.get("cache_creation_tokens") or 0)
        cr = int(slot.get("cache_read_tokens") or 0)
        return _estimate_cost_anthropic(info, in_tok, cc, cr, out_tok)
    return estimate_cost(model, in_tok, out_tok)


def _classify_cost(q: Query) -> float:
    # Classifier always uses haiku (services/classifier.py).
    return estimate_cost("haiku", q.classify_input_tokens or 0, q.classify_output_tokens or 0)


def _eval_cost(q: Query) -> float:
    # Self-eval model stored on query.eval_model; may be None for pre-eval rows.
    if not q.eval_model:
        return 0.0
    return estimate_cost(q.eval_model, q.eval_input_tokens or 0, q.eval_output_tokens or 0)


def _parse_slots(raw: str | None) -> list[dict] | None:
    if not raw:
        return None
    try:
        parsed = raw if isinstance(raw, (list, dict)) else json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        slots = parsed.get("slots")
        return slots if isinstance(slots, list) else None
    return None


async def backfill(apply: bool) -> None:
    async with async_session() as s:
        rows = (await s.execute(select(Query).order_by(Query.id))).scalars().all()
        total_old = 0.0
        total_new = 0.0
        changed = 0
        skipped_noparse = 0
        for q in rows:
            slots = _parse_slots(q.results_json)
            if slots is None:
                # No slot data — still refresh classify+eval roll-up.
                slot_total = 0.0
                new_slots_blob = None
            else:
                new_slots = []
                slot_total = 0.0
                for slot in slots:
                    if not isinstance(slot, dict):
                        skipped_noparse += 1
                        new_slots.append(slot)
                        continue
                    new_cost = _slot_cost(slot)
                    slot_total += new_cost
                    new_slots.append({**slot, "cost_usd": new_cost})
                new_slots_blob = json.dumps(new_slots)

            new_total = _classify_cost(q) + _eval_cost(q) + slot_total
            total_old += float(q.cost_usd or 0.0)
            total_new += new_total
            if abs(new_total - float(q.cost_usd or 0.0)) > 1e-9:
                changed += 1
            if apply:
                q.cost_usd = new_total
                if new_slots_blob is not None:
                    q.results_json = new_slots_blob
        if apply:
            await s.commit()
        print(f"rows scanned:      {len(rows)}")
        print(f"rows changed:      {changed}")
        print(f"slots unparseable: {skipped_noparse}")
        print(f"sum cost_usd old:  ${total_old:,.4f}")
        print(f"sum cost_usd new:  ${total_new:,.4f}")
        print(f"delta:             ${total_new - total_old:+,.4f}")
        print(f"mode:              {'APPLIED' if apply else 'DRY RUN'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Commit changes (default: dry-run)")
    args = parser.parse_args()
    asyncio.run(backfill(args.apply))


if __name__ == "__main__":
    main()
