"""Observed joint retrieval is one clue when ordering skill candidates."""

from __future__ import annotations

from collections import defaultdict
import hashlib
from itertools import combinations
import json
import os
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NeuronFiring


DECLINED_PATH = os.path.expanduser("~/.corvus-mind/declined-skill-candidates.json")


def load_declined() -> dict[str, dict]:
    try:
        data = json.loads(Path(DECLINED_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items()
            if isinstance(key, str) and isinstance(value, dict)
            and isinstance(value.get("kind"), str)}


def save_declined(entries: dict[str, dict]) -> None:
    path = Path(DECLINED_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_suffix(path.suffix + ".tmp")
    staged.write_text(json.dumps(entries, sort_keys=True, indent=2) + "\n",
                      encoding="utf-8")
    staged.replace(path)


def cofire_strength(source_ids: set[int], query_sets: list[set[int]]) -> int:
    """Count distinct source pairs per query, never duplicate firing rows."""
    return sum(
        1 for seen in query_sets
        for _pair in combinations(sorted(source_ids & seen), 2)
    )


def rank_clusters(clusters: list[list], query_sets: list[set[int]]) -> list[list]:
    """Prefer demonstrated joint retrieval, but retain quiet candidates."""
    return sorted(
        clusters,
        key=lambda cluster: (
            cofire_strength({n.id for n in cluster}, query_sets),
            len(cluster),
        ),
        reverse=True,
    )


def candidate_fingerprint(cluster: list) -> str:
    """A rejection lasts only while the cited lesson contents stay the same."""
    canonical = [
        {"id": n.id, "label": getattr(n, "label", None),
         "content": getattr(n, "content", None),
         "department": getattr(n, "department", None)}
        for n in sorted(cluster, key=lambda n: n.id)
    ]
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def pending_clusters(
    clusters: list[list], manifest: list[dict], declined: set[str] | None = None,
) -> list[list]:
    """New clusters and pre-synthesis bundles need the new admission gate."""
    version_by_sources = {
        frozenset(entry.get("sources", [])): entry.get("synthesis_version", 0)
        for entry in manifest if not entry.get("designated")
    }
    return [cluster for cluster in clusters
            if version_by_sources.get(frozenset(n.id for n in cluster), 0) < 2
            and candidate_fingerprint(cluster) not in (declined or set())]


async def observed_query_sets(db: AsyncSession, source_ids: set[int]) -> list[set[int]]:
    if not source_ids:
        return []
    rows = (await db.execute(
        select(NeuronFiring.query_id, NeuronFiring.neuron_id).where(
            NeuronFiring.neuron_id.in_(source_ids),
            NeuronFiring.query_id.is_not(None),
        ).distinct()
    )).all()
    by_query: dict[int, set[int]] = defaultdict(set)
    for query_id, neuron_id in rows:
        by_query[query_id].add(neuron_id)
    return list(by_query.values())
