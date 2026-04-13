"""Tiered edge storage: promoted edges live in neuron_edges table,
weak edges live in JSONB column on the neurons table.

An edge is "promoted" when BOTH thresholds are met:
  weight >= edge_promote_min_weight AND co_fire_count >= edge_promote_min_cofires

Bidirectional convention: edge(A, B) is stored on neuron min(A, B)
keyed by str(max(A, B)).  One copy per edge, no duplication.

JSONB entry format:
  {"w": float, "t": str, "c": int, "s": str, "q": int}
  w=weight, t=edge_type, c=co_fire_count, s=source, q=last_updated_query
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Neuron, NeuronEdge


def holder_key(a: int, b: int) -> tuple[int, int]:
    """Return (holder_id, peer_key) — holder is min, peer is max."""
    assert a != b, "self-edges not allowed"
    if a < b:
        return (a, b)
    return (b, a)


def is_promoted(weight: float, co_fire_count: int) -> bool:
    """True when an edge qualifies for the neuron_edges table."""
    min_w = settings.edge_promote_min_weight
    min_c = settings.edge_promote_min_cofires
    return weight >= min_w and co_fire_count >= min_c


async def get_weak_edge(
    db: AsyncSession, a: int, b: int
) -> dict[str, Any] | None:
    """Read a single weak edge from holder's JSONB column."""
    hid, pid = holder_key(a, b)
    row = await db.execute(
        text(
            "SELECT weak_edges -> :key AS entry "
            "FROM neurons WHERE id = :hid"
        ),
        {"hid": hid, "key": str(pid)},
    )
    result = row.scalar_one_or_none()
    return result


async def upsert_weak_edge(
    db: AsyncSession,
    a: int,
    b: int,
    data: dict[str, Any],
) -> None:
    """Atomic JSONB upsert of a single weak edge entry."""
    hid, pid = holder_key(a, b)
    await db.execute(
        text(
            "UPDATE neurons "
            "SET weak_edges = jsonb_set("
            "  COALESCE(weak_edges, '{}'::jsonb), "
            "  ARRAY[:key], CAST(:val AS jsonb)"
            ") WHERE id = :hid"
        ),
        {"hid": hid, "key": str(pid), "val": _dumps(data)},
    )


async def delete_weak_edge(db: AsyncSession, a: int, b: int) -> None:
    """Atomic removal of a single weak edge key from JSONB."""
    hid, pid = holder_key(a, b)
    await db.execute(
        text(
            "UPDATE neurons "
            "SET weak_edges = weak_edges #- ARRAY[:key] "
            "WHERE id = :hid AND weak_edges ? :key"
        ),
        {"hid": hid, "key": str(pid)},
    )


async def promote_edge(
    db: AsyncSession, a: int, b: int
) -> NeuronEdge | None:
    """Move an edge from JSONB to the neuron_edges table.

    Returns the new NeuronEdge row, or None if the weak edge didn't exist.
    """
    entry = await get_weak_edge(db, a, b)
    if entry is None:
        return None
    hid, pid = holder_key(a, b)
    # Insert into table (source_id is always the lower id for consistency)
    edge = NeuronEdge(
        source_id=hid,
        target_id=pid,
        co_fire_count=entry.get("c", 0),
        weight=entry.get("w", 0.0),
        last_updated_query=entry.get("q", 0),
        edge_type=entry.get("t", "pyramidal"),
        source=entry.get("s", "organic"),
    )
    db.add(edge)
    await delete_weak_edge(db, a, b)
    return edge


async def demote_edge(db: AsyncSession, edge: NeuronEdge) -> None:
    """Move an edge from the neuron_edges table into JSONB."""
    src, tgt = edge.source_id, edge.target_id
    data = {
        "w": edge.weight,
        "t": edge.edge_type or "pyramidal",
        "c": edge.co_fire_count,
        "s": edge.source or "organic",
        "q": edge.last_updated_query,
    }
    await upsert_weak_edge(db, src, tgt, data)
    await db.execute(
        text(
            "DELETE FROM neuron_edges "
            "WHERE source_id = :s AND target_id = :t"
        ),
        {"s": src, "t": tgt},
    )


async def maybe_promote(
    db: AsyncSession, a: int, b: int, weight: float, cofires: int
) -> bool:
    """After a JSONB update, promote the edge if thresholds are met.

    Returns True if promotion occurred.
    """
    if not is_promoted(weight, cofires):
        return False
    edge = await promote_edge(db, a, b)
    return edge is not None


async def maybe_demote(
    db: AsyncSession, src: int, tgt: int, weight: float, cofires: int
) -> bool:
    """After a table update, demote the edge if thresholds are no longer met.

    Returns True if demotion occurred.
    """
    if is_promoted(weight, cofires):
        return False
    row = await db.execute(
        text(
            "SELECT source_id, target_id, co_fire_count, weight, "
            "last_updated_query, edge_type, source "
            "FROM neuron_edges WHERE source_id = :s AND target_id = :t"
        ),
        {"s": src, "t": tgt},
    )
    edge_row = row.one_or_none()
    if edge_row is None:
        return False
    edge = NeuronEdge(
        source_id=edge_row.source_id,
        target_id=edge_row.target_id,
        co_fire_count=edge_row.co_fire_count,
        weight=edge_row.weight,
        last_updated_query=edge_row.last_updated_query,
        edge_type=edge_row.edge_type,
        source=edge_row.source,
    )
    await demote_edge(db, edge)
    return True


def _dumps(data: dict[str, Any]) -> str:
    """Serialize a dict to JSON string for PostgreSQL parameter binding."""
    import json
    return json.dumps(data)
