#!/usr/bin/env python3
"""Import a roadmap-state JSON document into Corvus-Mind.

Run from ``backend/`` so the application package and tenant configuration use
the same environment as the service:

    TENANT_ID=corvus-mind venv/bin/python scripts/import_roadmap_ledger.py \
      /path/to/roadmap-state.json --slug corvus-long-horizon \
      --name "Corvus / Long Horizon" --project-path /path/to/corvus
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from app.database import async_session
from app.models import RoadmapLedger
from app.services.roadmap_ledger import validate_state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--description")
    parser.add_argument("--project-path")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing ledger with the same slug and advance its revision.",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    state = validate_state(json.loads(args.source.read_text()))
    async with async_session() as db:
        row = await db.scalar(
            select(RoadmapLedger).where(RoadmapLedger.slug == args.slug).with_for_update()
        )
        if row is not None and not args.replace:
            raise SystemExit(
                f"ledger {args.slug!r} already exists; use --replace to overwrite it"
            )
        if row is None:
            row = RoadmapLedger(
                slug=args.slug,
                name=args.name,
                description=args.description,
                project_path=args.project_path,
                state=state,
            )
            db.add(row)
        else:
            row.name = args.name
            row.description = args.description
            row.project_path = args.project_path
            row.state = state
            row.revision += 1
        await db.commit()
        await db.refresh(row)
        print(json.dumps({
            "id": row.id,
            "slug": row.slug,
            "revision": row.revision,
            "version": row.state["version"],
            "sections": len(row.state["sections"]),
            "nodes": len(row.state["nodes"]),
            "edges": len(row.state["edges"]),
        }, indent=2))


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
