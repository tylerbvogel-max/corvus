"""Neuron inspection endpoints."""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select, func as sa_func, text, or_, and_

from app.database import get_db
from app.models import Neuron, NeuronEdge, NeuronRefinement, Query as QueryModel
from app.schemas import NeuronDetail, NeuronScoreDetail, NeuronRefinementOut
from app.services.neuron_service import (
    get_neuron,
    get_neuron_tree,
    get_graph_stats,
    get_system_state,
    score_candidates,
)

router = APIRouter(prefix="/neurons", tags=["neurons"])


@router.get("/tree")
async def neurons_tree(
    department: str | None = None,
    role_key: str | None = None,
    max_depth: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    return await get_neuron_tree(db, department, role_key, max_depth=max_depth)


@router.get("/children")
async def neuron_children(
    parent_id: int | None = None,
    department: str | None = None,
    offset: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """Fetch direct children of a neuron (or roots if parent_id is None).

    Returns flat list with child_count for lazy-load tree rendering.
    """
    conditions = [Neuron.is_active == True, Neuron.layer >= 0]  # Exclude concept neurons (layer=-1)
    if parent_id is not None:
        conditions.append(Neuron.parent_id == parent_id)
    else:
        conditions.append(Neuron.parent_id.is_(None))
    if department:
        conditions.append(Neuron.department == department)

    # Subquery for child count using aliased child table
    from sqlalchemy.orm import aliased
    Child = aliased(Neuron)
    child_count_sub = (
        select(sa_func.count(Child.id))
        .where(Child.parent_id == Neuron.id)
        .correlate(Neuron)
        .scalar_subquery()
    )

    stmt = (
        select(
            Neuron.id, Neuron.layer, Neuron.node_type, Neuron.label,
            Neuron.department, Neuron.role_key, Neuron.invocations,
            Neuron.avg_utility, Neuron.parent_id, child_count_sub.label("child_count"),
        )
        .where(and_(*conditions))
        .order_by(Neuron.layer, Neuron.id)
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return [
        {
            "id": r[0], "layer": r[1], "node_type": r[2], "label": r[3],
            "department": r[4], "role_key": r[5], "invocations": r[6],
            "avg_utility": r[7], "parent_id": r[8], "child_count": r[9] or 0,
        }
        for r in result.all()
    ]


@router.get("/stats")
async def neurons_stats(db: AsyncSession = Depends(get_db)):
    return await get_graph_stats(db)


@router.get("/capacity")
async def neurons_capacity(db: AsyncSession = Depends(get_db)):
    """Total token capacity of the active neuron graph."""
    # Total chars of content in active neurons (content + summary)
    content_chars = (await db.execute(
        select(sa_func.coalesce(sa_func.sum(sa_func.length(Neuron.content)), 0))
        .where(Neuron.is_active == True, Neuron.content.isnot(None))
    )).scalar()
    summary_chars = (await db.execute(
        select(sa_func.coalesce(sa_func.sum(sa_func.length(Neuron.summary)), 0))
        .where(Neuron.is_active == True, Neuron.summary.isnot(None))
    )).scalar()
    active_count = (await db.execute(
        select(sa_func.count(Neuron.id)).where(Neuron.is_active == True)
    )).scalar()
    # ~4 chars per token
    return {
        "active_neurons": active_count,
        "total_content_tokens": content_chars // 4,
        "total_summary_tokens": summary_chars // 4,
        "total_tokens": (content_chars + summary_chars) // 4,
    }


@router.get("/refinements", response_model=list[NeuronRefinementOut])
async def list_refinements(
    action: str | None = None,
    field: str | None = None,
    neuron_id: int | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 500,
    db: AsyncSession = Depends(get_db),
):
    """List refinements with optional filters for action, field, neuron, and date range."""
    stmt = select(NeuronRefinement).order_by(NeuronRefinement.created_at.desc())
    if action:
        stmt = stmt.where(NeuronRefinement.action == action)
    if field:
        stmt = stmt.where(NeuronRefinement.field == field)
    if neuron_id:
        stmt = stmt.where(NeuronRefinement.neuron_id == neuron_id)
    if since:
        stmt = stmt.where(NeuronRefinement.created_at >= datetime.fromisoformat(since))
    if until:
        stmt = stmt.where(NeuronRefinement.created_at <= datetime.fromisoformat(until))
    stmt = stmt.limit(min(limit, 2000))
    rows = (await db.execute(stmt)).scalars().all()
    return await _build_refinement_list(db, rows)


async def _build_refinement_list(
    db: AsyncSession, rows: list[NeuronRefinement],
) -> list[NeuronRefinementOut]:
    """Convert refinement rows to output schema with joined labels."""
    out: list[NeuronRefinementOut] = []
    for r in rows:
        neuron = await db.get(Neuron, r.neuron_id)
        query = await db.get(QueryModel, r.query_id) if r.query_id else None
        snippet = None
        if query:
            msg = query.user_message or ""
            snippet = (msg[:80] + "...") if len(msg) > 80 else msg
        out.append(NeuronRefinementOut(
            id=r.id, query_id=r.query_id, neuron_id=r.neuron_id,
            action=r.action, field=r.field,
            old_value=r.old_value, new_value=r.new_value,
            reason=r.reason,
            created_at=r.created_at.isoformat() if r.created_at else None,
            neuron_label=neuron.label if neuron else None,
            query_snippet=snippet,
        ))
    return out


@router.get("/clusters")
async def neuron_clusters(
    min_weight: float = 0.3,
    min_size: int = 3,
    db: AsyncSession = Depends(get_db),
):
    """Discover cross-department neuron clusters via label propagation on co-firing edges."""
    from app.services.clustering import find_clusters
    clusters = await find_clusters(db, min_weight=min_weight, min_size=min_size)
    return {"cluster_count": len(clusters), "clusters": clusters}


async def _chord_concept_layer(db: AsyncSession, min_weight: float) -> list[dict]:
    stmt = text("""
        SELECT c.label AS concept_label,
               COALESCE(n2.department, 'Unknown') AS target_dept,
               SUM(e.weight) AS total_weight, COUNT(*) AS edge_count
        FROM neuron_edges e
        JOIN neurons c ON (c.id = e.source_id OR c.id = e.target_id) AND c.node_type = 'concept'
        JOIN neurons n2 ON n2.id = CASE WHEN c.id = e.source_id THEN e.target_id ELSE e.source_id END
        WHERE e.edge_type = 'instantiates'
          AND e.weight >= :min_weight
          AND n2.node_type != 'concept'
        GROUP BY c.label, n2.department
        ORDER BY total_weight DESC
    """)
    result = await db.execute(stmt, {"min_weight": min_weight})
    return [
        {
            "source_dept": r.concept_label,
            "target_dept": r.target_dept,
            "source_department": "Concepts",
            "target_department": r.target_dept,
            "total_weight": float(r.total_weight),
            "edge_count": r.edge_count,
        }
        for r in result.fetchall()
    ]


async def _chord_role_layer(db: AsyncSession, min_weight: float) -> list[dict]:
    stmt = text("""
        SELECT r1.label AS source_label, r2.label AS target_label,
               n1.role_key AS source_role, n2.role_key AS target_role,
               n1.department AS source_dept, n2.department AS target_dept,
               SUM(e.weight) AS total_weight, COUNT(*) AS edge_count
        FROM neuron_edges e
        JOIN neurons n1 ON e.source_id = n1.id
        JOIN neurons n2 ON e.target_id = n2.id
        LEFT JOIN neurons r1 ON r1.role_key = n1.role_key AND r1.layer = 1
        LEFT JOIN neurons r2 ON r2.role_key = n2.role_key AND r2.layer = 1
        WHERE e.weight >= :min_weight
          AND n1.role_key IS NOT NULL
          AND n2.role_key IS NOT NULL
        GROUP BY n1.role_key, n2.role_key, n1.department, n2.department,
                 r1.label, r2.label
    """)
    result = await db.execute(stmt, {"min_weight": min_weight})
    return [
        {
            "source_dept": r.source_label or r.source_role,
            "target_dept": r.target_label or r.target_role,
            "source_department": r.source_dept,
            "target_department": r.target_dept,
            "total_weight": r.total_weight,
            "edge_count": r.edge_count,
        }
        for r in result.fetchall()
    ]


async def _chord_generic_layer(db: AsyncSession, layer: int, min_weight: float) -> list[dict]:
    stmt = text("""
        SELECT n1.label AS source_label, n2.label AS target_label,
               n1.department AS source_dept, n2.department AS target_dept,
               SUM(e.weight) AS total_weight, COUNT(*) AS edge_count
        FROM neuron_edges e
        JOIN neurons n1 ON e.source_id = n1.id
        JOIN neurons n2 ON e.target_id = n2.id
        WHERE e.weight >= :min_weight
          AND n1.layer = :layer AND n2.layer = :layer
        GROUP BY n1.id, n2.id, n1.label, n2.label,
                 n1.department, n2.department
    """)
    result = await db.execute(stmt, {"layer": layer, "min_weight": min_weight})
    return [
        {
            "source_dept": r.source_label,
            "target_dept": r.target_label,
            "source_department": r.source_dept,
            "target_department": r.target_dept,
            "total_weight": r.total_weight,
            "edge_count": r.edge_count,
        }
        for r in result.fetchall()
    ]


@router.get("/edges/department-chord")
async def department_chord(layer: int = 1, min_weight: float = 0.15, db: AsyncSession = Depends(get_db)):
    """Aggregate co-firing edges grouped at a specific neuron layer.

    layer=-1 returns concept neuron instantiation edges grouped by concept × department.
    """
    if layer == -1:
        return await _chord_concept_layer(db, min_weight)

    if layer < 1 or layer > 5:
        raise HTTPException(status_code=400, detail="Layer must be 1-5")
    if min_weight < 0 or min_weight > 1:
        raise HTTPException(status_code=400, detail="min_weight must be 0-1")

    if layer == 1:
        return await _chord_role_layer(db, min_weight)
    return await _chord_generic_layer(db, layer, min_weight)


@router.get("/edges/layer-flow")
async def layer_flow(min_weight: float = 0.15, db: AsyncSession = Depends(get_db)):
    """Aggregate co-firing edges between layers, grouped by (layer, department).

    Returns nodes (layer × department) and links (flow between them) suitable for Sankey layout.
    """
    if min_weight < 0 or min_weight > 1:
        raise HTTPException(status_code=400, detail="min_weight must be 0-1")

    # Get cross-layer flows aggregated by (source_layer, source_dept) → (target_layer, target_dept)
    stmt = text("""
        SELECT n1.layer AS source_layer, n1.department AS source_dept,
               n2.layer AS target_layer, n2.department AS target_dept,
               SUM(e.weight) AS total_weight, COUNT(*) AS edge_count
        FROM neuron_edges e
        JOIN neurons n1 ON e.source_id = n1.id
        JOIN neurons n2 ON e.target_id = n2.id
        WHERE e.weight >= :min_weight
        GROUP BY n1.layer, n1.department, n2.layer, n2.department
    """)
    result = await db.execute(stmt, {"min_weight": min_weight})
    rows = result.fetchall()

    # Build node set and links
    node_set: dict[str, dict] = {}
    links = []
    for r in rows:
        src_key = f"L{r.source_layer}:{r.source_dept}"
        tgt_key = f"L{r.target_layer}:{r.target_dept}"
        if src_key not in node_set:
            node_set[src_key] = {"key": src_key, "layer": r.source_layer, "department": r.source_dept}
        if tgt_key not in node_set:
            node_set[tgt_key] = {"key": tgt_key, "layer": r.target_layer, "department": r.target_dept}
        links.append({
            "source": src_key,
            "target": tgt_key,
            "total_weight": float(r.total_weight),
            "edge_count": r.edge_count,
        })

    # Also get neuron counts per (layer, department) for node sizing
    count_stmt = text("""
        SELECT layer, department, COUNT(*) AS neuron_count
        FROM neurons WHERE is_active = true
        GROUP BY layer, department
    """)
    count_result = await db.execute(count_stmt)
    for r in count_result.fetchall():
        key = f"L{r.layer}:{r.department}"
        if key in node_set:
            node_set[key]["neuron_count"] = r.neuron_count

    return {
        "nodes": list(node_set.values()),
        "links": links,
    }


def _parse_spread_queries(queries) -> tuple[list[tuple], set[int]]:
    all_neuron_ids: set[int] = set()
    parsed_queries: list[tuple] = []
    for q in queries:
        try:
            scores = json.loads(q.neuron_scores_json)
        except (json.JSONDecodeError, TypeError):
            continue
        promoted = [s for s in scores if s.get("spread_boost", 0) > 0]
        if not promoted:
            parsed_queries.append((q, scores, []))
            continue
        for s in promoted:
            all_neuron_ids.add(s["neuron_id"])
        parsed_queries.append((q, scores, promoted))
    return parsed_queries, all_neuron_ids


async def _fetch_neuron_map(db: AsyncSession, neuron_ids: set[int]) -> dict[int, dict]:
    if not neuron_ids:
        return {}
    n_result = await db.execute(
        select(Neuron.id, Neuron.label, Neuron.department).where(
            Neuron.id.in_(neuron_ids)
        )
    )
    return {row[0]: {"label": row[1], "department": row[2]} for row in n_result.all()}


def _build_spread_entry(
    q, scores: list, promoted: list,
    neuron_map: dict[int, dict],
    neuron_spread_counts: dict[int, int],
    dept_corridors: dict[str, int],
) -> dict:
    source_depts: set[str] = set()
    for s in scores:
        if s.get("spread_boost", 0) == 0:
            nid = s["neuron_id"]
            if nid in neuron_map and neuron_map[nid]["department"]:
                source_depts.add(neuron_map[nid]["department"])

    promoted_depts: set[str] = set()
    promoted_neurons = []
    for p in promoted:
        nid = p["neuron_id"]
        neuron_spread_counts[nid] = neuron_spread_counts.get(nid, 0) + 1
        info = neuron_map.get(nid, {"label": f"#{nid}", "department": "Unknown"})
        promoted_neurons.append({
            "neuron_id": nid,
            "label": info["label"],
            "department": info["department"],
            "boost": round(p["spread_boost"], 4),
        })
        if info["department"]:
            promoted_depts.add(info["department"])

    cross_dept = bool(promoted_depts - source_depts) if source_depts else False
    for sd in source_depts:
        for pd in promoted_depts:
            if sd != pd:
                key = " \u2192 ".join(sorted([sd, pd]))
                dept_corridors[key] = dept_corridors.get(key, 0) + 1

    return {
        "query_id": q.id,
        "user_message": q.user_message[:120],
        "created_at": q.created_at.isoformat() if q.created_at else None,
        "promoted_count": len(promoted),
        "avg_boost": round(sum(p["spread_boost"] for p in promoted) / len(promoted), 4) if promoted else 0,
        "max_boost": round(max((p["spread_boost"] for p in promoted), default=0), 4),
        "cross_dept": cross_dept,
        "promoted_neurons": promoted_neurons,
    }


def _build_spread_summary(
    parsed_queries: list[tuple],
    entries: list[dict],
    neuron_spread_counts: dict[int, int],
    dept_corridors: dict[str, int],
    neuron_map: dict[int, dict],
) -> dict:
    top_neurons = sorted(neuron_spread_counts.items(), key=lambda x: x[1], reverse=True)[:20]
    top_neuron_list = [
        {
            "neuron_id": nid,
            "label": neuron_map.get(nid, {}).get("label", f"#{nid}"),
            "department": neuron_map.get(nid, {}).get("department", "Unknown"),
            "spread_count": count,
        }
        for nid, count in top_neurons
    ]
    top_corridors = sorted(dept_corridors.items(), key=lambda x: x[1], reverse=True)[:10]
    total_queries = len(parsed_queries)
    queries_with_spread = sum(1 for _, _, p in parsed_queries if p)
    return {
        "total_queries": total_queries,
        "queries_with_spread": queries_with_spread,
        "spread_rate": round(queries_with_spread / total_queries, 4) if total_queries else 0,
        "entries": entries,
        "top_neurons": top_neuron_list,
        "top_corridors": [{"pair": k, "count": v} for k, v in top_corridors],
    }


@router.get("/edges/spread-log")
async def spread_log(limit: int = 100, db: AsyncSession = Depends(get_db)):
    """Aggregate spread activation history across recent queries."""
    result = await db.execute(
        select(QueryModel)
        .where(QueryModel.neuron_scores_json.isnot(None))
        .order_by(QueryModel.created_at.desc())
        .limit(limit)
    )
    queries = result.scalars().all()

    parsed_queries, all_neuron_ids = _parse_spread_queries(queries)
    neuron_map = await _fetch_neuron_map(db, all_neuron_ids)

    entries = []
    neuron_spread_counts: dict[int, int] = {}
    dept_corridors: dict[str, int] = {}

    for q, scores, promoted in parsed_queries:
        entry = _build_spread_entry(
            q, scores, promoted, neuron_map,
            neuron_spread_counts, dept_corridors,
        )
        entries.append(entry)

    return _build_spread_summary(
        parsed_queries, entries, neuron_spread_counts, dept_corridors, neuron_map,
    )


@router.get("/edges/spread-trail")
async def spread_trail(query_id: int, db: AsyncSession = Depends(get_db)):
    """Get spread activation trail for a query."""
    query = await db.get(QueryModel, query_id)
    if not query:
        raise HTTPException(status_code=404, detail="Query not found")
    if not query.neuron_scores_json:
        return {"nodes": [], "edges": []}

    scores = json.loads(query.neuron_scores_json)
    if not scores:
        return {"nodes": [], "edges": []}

    neuron_ids = [s["neuron_id"] for s in scores]
    spread_ids = {s["neuron_id"] for s in scores if s.get("spread_boost", 0) > 0}
    non_spread_ids = {s["neuron_id"] for s in scores if s.get("spread_boost", 0) == 0}

    if not spread_ids:
        return {"nodes": [], "edges": []}

    # Fetch edges connecting spread-boosted neurons to any scored neuron
    edge_result = await db.execute(
        select(NeuronEdge).where(
            or_(
                and_(NeuronEdge.source_id.in_(non_spread_ids), NeuronEdge.target_id.in_(spread_ids)),
                and_(NeuronEdge.source_id.in_(spread_ids), NeuronEdge.target_id.in_(non_spread_ids)),
                and_(NeuronEdge.source_id.in_(spread_ids), NeuronEdge.target_id.in_(spread_ids)),
            )
        )
    )
    edges = edge_result.scalars().all()
    if not edges:
        return {"nodes": [], "edges": []}

    # Collect involved neuron IDs
    involved_ids = set()
    for e in edges:
        involved_ids.add(e.source_id)
        involved_ids.add(e.target_id)

    # Fetch neuron details
    neuron_result = await db.execute(select(Neuron).where(Neuron.id.in_(involved_ids)))
    neurons = {n.id: n for n in neuron_result.scalars().all()}

    score_map = {s["neuron_id"]: s for s in scores}

    nodes = []
    for nid in involved_ids:
        n = neurons.get(nid)
        s = score_map.get(nid, {})
        nodes.append({
            "id": nid,
            "label": n.label if n else f"#{nid}",
            "department": n.department if n else None,
            "layer": n.layer if n else 0,
            "combined": s.get("combined", 0),
            "spread_boost": s.get("spread_boost", 0),
        })

    return {
        "nodes": nodes,
        "edges": [
            {"source_id": e.source_id, "target_id": e.target_id, "weight": e.weight}
            for e in edges
        ],
    }


async def _bfs_edge_graph(
    db: AsyncSession, neuron_id: int, hops: int,
) -> tuple[dict[int, tuple[int, float]], list[tuple[int, int, float, int]]]:
    discovered: dict[int, tuple[int, float]] = {neuron_id: (0, 1.0)}
    frontier = {neuron_id}
    all_edge_pairs: list[tuple[int, int, float, int]] = []

    for hop in range(1, hops + 1):
        if not frontier:
            break
        frontier_list = list(frontier)
        result = await db.execute(
            select(NeuronEdge)
            .where(or_(
                NeuronEdge.source_id.in_(frontier_list),
                NeuronEdge.target_id.in_(frontier_list),
            ))
            .order_by(NeuronEdge.weight.desc())
        )
        edges = result.scalars().all()

        next_frontier: set[int] = set()
        for e in edges:
            all_edge_pairs.append((e.source_id, e.target_id, e.weight, e.co_fire_count))
            for nid in (e.source_id, e.target_id):
                if nid not in discovered:
                    parent = e.target_id if nid == e.source_id else e.source_id
                    parent_weight = discovered[parent][1] if parent in discovered else 1.0
                    discovered[nid] = (hop, parent_weight * e.weight)
                    next_frontier.add(nid)
        frontier = next_frontier

    return discovered, all_edge_pairs


def _select_top_neighbors(
    discovered: dict[int, tuple[int, float]], neuron_id: int, limit: int,
) -> tuple[list[tuple], list[tuple], set[int]]:
    neighbor_entries = {nid: info for nid, info in discovered.items() if nid != neuron_id}

    hop1 = [(nid, info) for nid, info in neighbor_entries.items() if info[0] == 1]
    hop1.sort(key=lambda x: x[1][1], reverse=True)
    hop1 = hop1[:limit]

    hop2plus = [(nid, info) for nid, info in neighbor_entries.items() if info[0] >= 2]
    hop2plus.sort(key=lambda x: x[1][1], reverse=True)
    hop2plus = hop2plus[:limit]

    keep_ids = {neuron_id} | {nid for nid, _ in hop1} | {nid for nid, _ in hop2plus}
    return hop1, hop2plus, keep_ids


def _build_neighbor_list(
    hop1: list[tuple], hop2plus: list[tuple],
    neuron_map: dict, all_edge_pairs: list[tuple],
) -> list[dict]:
    neighbors = []
    for nid, (hop_dist, weight_product) in list(hop1) + list(hop2plus):
        n = neuron_map.get(nid)
        if not n:
            continue
        best_weight = 0.0
        best_cofire = 0
        for src, tgt, w, cf in all_edge_pairs:
            if (src == nid or tgt == nid) and w > best_weight:
                best_weight = w
                best_cofire = cf
        neighbors.append({
            "id": n.id,
            "label": n.label,
            "department": n.department,
            "layer": n.layer,
            "node_type": n.node_type,
            "weight": best_weight,
            "co_fire_count": best_cofire,
            "hop": hop_dist,
        })
    return neighbors


def _dedupe_subgraph_edges(
    all_edge_pairs: list[tuple], keep_ids: set[int],
) -> list[dict]:
    seen_edges: set[tuple[int, int]] = set()
    graph_edges = []
    for src, tgt, w, cf in all_edge_pairs:
        if src in keep_ids and tgt in keep_ids:
            key = (min(src, tgt), max(src, tgt))
            if key not in seen_edges:
                seen_edges.add(key)
                graph_edges.append({
                    "source": src, "target": tgt,
                    "weight": w, "co_fire_count": cf,
                })
    return graph_edges


@router.get("/{neuron_id}/edges")
async def neuron_edges(neuron_id: int, limit: int = 15, hops: int = 2, db: AsyncSession = Depends(get_db)):
    """Multi-hop neighbors by edge weight for a neuron.

    Returns neighbors with hop distance (1 = direct co-fire, 2+ = reached
    through intermediate nodes).  Edges between all returned nodes are
    included so the frontend can draw the full subgraph.
    """
    neuron = await get_neuron(db, neuron_id)
    if not neuron:
        raise HTTPException(status_code=404, detail="Neuron not found")

    hops = min(hops, 3)
    discovered, all_edge_pairs = await _bfs_edge_graph(db, neuron_id, hops)
    hop1, hop2plus, keep_ids = _select_top_neighbors(discovered, neuron_id, limit)

    if keep_ids:
        n_result = await db.execute(select(Neuron).where(Neuron.id.in_(keep_ids)))
        neuron_map = {n.id: n for n in n_result.scalars().all()}
    else:
        neuron_map = {}

    neighbors = _build_neighbor_list(hop1, hop2plus, neuron_map, all_edge_pairs)
    graph_edges = _dedupe_subgraph_edges(all_edge_pairs, keep_ids)

    return {
        "center": {
            "id": neuron.id,
            "label": neuron.label,
            "department": neuron.department,
            "layer": neuron.layer,
        },
        "neighbors": neighbors,
        "edges": graph_edges,
    }


async def _graph3d_nodes(db: AsyncSession) -> list[dict]:
    """All active neurons with the fields the 3D universe renders/filters on."""
    result = await db.execute(
        select(
            Neuron.id, Neuron.label, Neuron.department, Neuron.layer,
            Neuron.node_type, Neuron.abstraction_type, Neuron.role_key,
            Neuron.invocations, Neuron.avg_utility, Neuron.centrality,
            Neuron.parent_id,
        ).where(Neuron.is_active == True)
    )
    return [
        {
            "id": r.id, "label": r.label, "department": r.department,
            "layer": r.layer, "node_type": r.node_type,
            "abstraction_type": r.abstraction_type, "role_key": r.role_key,
            "invocations": r.invocations or 0,
            "avg_utility": float(r.avg_utility or 0),
            "centrality": float(r.centrality or 0),
            "parent_id": r.parent_id,
        }
        for r in result.fetchall()
    ]


async def _graph3d_edges(
    db: AsyncSession, min_weight: float, max_edges: int, per_node: int,
) -> list[dict]:
    """Coverage-first edge selection: every neuron's top-K edges by weight
    (keeps the whole web connected instead of one dense clump), then fill
    the remaining budget with the globally strongest edges."""
    from sqlalchemy import text as sa_text

    ranked_result = await db.execute(sa_text("""
        SELECT source_id, target_id, weight, co_fire_count, edge_type FROM (
            SELECT sub.*, ROW_NUMBER() OVER (
                PARTITION BY nid ORDER BY weight DESC
            ) AS rn
            FROM (
                SELECT source_id AS nid, source_id, target_id, weight,
                       co_fire_count, edge_type
                FROM neuron_edges WHERE weight >= :mw
                UNION ALL
                SELECT target_id AS nid, source_id, target_id, weight,
                       co_fire_count, edge_type
                FROM neuron_edges WHERE weight >= :mw
            ) sub
        ) ranked WHERE rn <= :k
        ORDER BY weight DESC
    """), {"mw": min_weight, "k": per_node})

    edge_set: dict[tuple[int, int], dict] = {}
    for r in ranked_result.fetchall():
        key = (r[0], r[1])
        if key not in edge_set and len(edge_set) < max_edges:
            edge_set[key] = {
                "source": r[0], "target": r[1], "weight": float(r[2]),
                "co_fire_count": r[3], "edge_type": r[4] or "pyramidal",
            }

    remaining = max_edges - len(edge_set)
    if remaining > 0:
        fill_result = await db.execute(
            select(
                NeuronEdge.source_id, NeuronEdge.target_id, NeuronEdge.weight,
                NeuronEdge.co_fire_count, NeuronEdge.edge_type,
            )
            .where(NeuronEdge.weight >= min_weight)
            .order_by(NeuronEdge.weight.desc())
            .limit(max_edges)
        )
        for r in fill_result.fetchall():
            if len(edge_set) >= max_edges:
                break
            key = (r.source_id, r.target_id)
            if key not in edge_set:
                edge_set[key] = {
                    "source": r.source_id, "target": r.target_id,
                    "weight": float(r.weight), "co_fire_count": r.co_fire_count,
                    "edge_type": r.edge_type or "pyramidal",
                }
    return list(edge_set.values())


@router.get("/graph-3d")
async def graph_3d(
    min_weight: float = 0.25,
    max_edges: int = 12000,
    per_node: int = 3,
    db: AsyncSession = Depends(get_db),
):
    """Active neurons + coverage-first co-firing edges for the 3D universe.

    per_node bounds the top-K edges kept per neuron before global fill;
    payload includes the abstraction axis, region tag, centrality, and
    edge_type so the client can style stellate/pyramidal/instantiates
    connections differently.
    """
    assert 0.0 <= min_weight <= 1.0, "min_weight must be in [0, 1]"
    assert 1 <= per_node <= 12, "per_node must be in [1, 12]"
    neurons = await _graph3d_nodes(db)
    edges = await _graph3d_edges(db, min_weight, max_edges, per_node)
    return {"neurons": neurons, "edges": edges}


@router.get("/{neuron_id}", response_model=NeuronDetail)
async def neuron_detail(neuron_id: int, db: AsyncSession = Depends(get_db)):
    neuron = await get_neuron(db, neuron_id)
    if not neuron:
        raise HTTPException(status_code=404, detail="Neuron not found")
    cross_ref = None
    if neuron.cross_ref_departments:
        try:
            cross_ref = json.loads(neuron.cross_ref_departments)
        except (json.JSONDecodeError, TypeError):
            pass
    return NeuronDetail(
        id=neuron.id,
        parent_id=neuron.parent_id,
        layer=neuron.layer,
        node_type=neuron.node_type,
        label=neuron.label,
        content=neuron.content,
        summary=neuron.summary,
        department=neuron.department,
        role_key=neuron.role_key,
        invocations=neuron.invocations,
        avg_utility=neuron.avg_utility,
        is_active=neuron.is_active,
        cross_ref_departments=cross_ref,
        standard_date=neuron.standard_date,
    )


@router.get("/{neuron_id}/scores", response_model=NeuronScoreDetail)
async def neuron_scores(neuron_id: int, db: AsyncSession = Depends(get_db)):
    neuron = await get_neuron(db, neuron_id)
    if not neuron:
        raise HTTPException(status_code=404, detail="Neuron not found")

    state = await get_system_state(db)
    scored = await score_candidates(db, [neuron], state.total_queries, [])

    if not scored:
        raise HTTPException(status_code=500, detail="Scoring failed")

    s = scored[0]
    return NeuronScoreDetail(
        neuron_id=s.neuron_id,
        burst=s.burst,
        impact=s.impact,
        precision=s.precision,
        novelty=s.novelty,
        recency=s.recency,
        relevance=s.relevance,
        combined=s.combined,
    )
