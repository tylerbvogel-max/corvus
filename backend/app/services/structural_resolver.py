"""Structural resolver — deterministic fast path for structural queries.

Detects queries that can be answered directly from the database without
calling the Haiku classifier. Returns a PreparedContext with zero classify cost,
or None to fall through to the full pipeline.

Pattern matching uses regex + keyword detection to identify:
- Department listings
- Role listings within a department
- Neuron searches by topic
- Graph statistics
- Neuron connection queries
"""

import re
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Neuron, NeuronEdge, SystemState


# Patterns: (regex, handler_name)
_PATTERNS = [
    (re.compile(r"\b(?:list|show|what(?:\s+are)?)\s+(?:the\s+)?departments?\b", re.I), "departments"),
    (re.compile(r"\b(?:roles?|teams?)\s+(?:in|under|for|within)\s+(\w[\w\s]*?)(?:\s+department)?\s*\??$", re.I), "roles_in_dept"),
    (re.compile(r"\b(?:how\s+many\s+neurons?|graph\s+stats?|neuron\s+count|total\s+neurons?)\b", re.I), "graph_stats"),
    (re.compile(r"\b(?:connections?|edges?|neighbors?|co-?fir(?:e|ing))\s+(?:to|for|of)\s+neuron\s+#?(\d+)\b", re.I), "connections"),
    (re.compile(r"\b(?:neurons?\s+(?:about|for|covering|related\s+to)|find\s+neurons?\s+(?:about|for|on))\s+(.+)", re.I), "topic_search"),
]


async def try_structural_resolve(db: AsyncSession, user_message: str):
    """Try to resolve the query structurally. Returns PreparedContext or None."""
    msg = user_message.strip()

    for pattern, handler_name in _PATTERNS:
        match = pattern.search(msg)
        if match:
            handler = _HANDLERS.get(handler_name)
            if handler:
                result = await handler(db, match)
                if result is not None:
                    return result

    return None


async def _handle_departments(db: AsyncSession, match: re.Match):
    """List all departments with neuron counts."""
    from app.services.prepared_context import PreparedContext

    result = await db.execute(
        select(Neuron.department, func.count(Neuron.id))
        .where(Neuron.is_active == True, Neuron.department.isnot(None))
        .group_by(Neuron.department)
        .order_by(func.count(Neuron.id).desc())
    )
    rows = result.all()

    lines = ["## Departments in the Neuron Graph\n"]
    for dept, count in rows:
        lines.append(f"- **{dept}**: {count} neurons")

    return PreparedContext(
        system_prompt="\n".join(lines),
        intent="structural_query",
        departments=[r[0] for r in rows],
        role_keys=[],
        keywords=[],
        neuron_scores=[],
        neurons_activated=0,
    )


async def _handle_roles_in_dept(db: AsyncSession, match: re.Match):
    """List roles and neuron counts within a department."""
    from app.services.prepared_context import PreparedContext

    dept_query = match.group(1).strip()

    # Fuzzy match department name
    result = await db.execute(
        select(Neuron.department).where(Neuron.is_active == True, Neuron.department.isnot(None))
        .group_by(Neuron.department)
    )
    all_depts = [r[0] for r in result.all()]
    matched_dept = None
    for d in all_depts:
        if d.lower() == dept_query.lower() or dept_query.lower() in d.lower():
            matched_dept = d
            break
    if not matched_dept:
        return None  # Fall through to full pipeline

    result = await db.execute(
        select(Neuron.role_key, func.count(Neuron.id))
        .where(Neuron.is_active == True, Neuron.department == matched_dept, Neuron.role_key.isnot(None))
        .group_by(Neuron.role_key)
        .order_by(func.count(Neuron.id).desc())
    )
    rows = result.all()

    lines = [f"## Roles in {matched_dept} Department\n"]
    for role_key, count in rows:
        lines.append(f"- **{role_key}**: {count} neurons")

    return PreparedContext(
        system_prompt="\n".join(lines),
        intent="structural_query",
        departments=[matched_dept],
        role_keys=[r[0] for r in rows],
        keywords=[],
        neuron_scores=[],
        neurons_activated=0,
    )


async def _handle_graph_stats(db: AsyncSession, match: re.Match):
    """Return graph statistics."""
    from app.services.prepared_context import PreparedContext

    total = (await db.execute(
        select(func.count(Neuron.id)).where(Neuron.is_active == True)
    )).scalar() or 0

    by_layer = await db.execute(
        select(Neuron.layer, func.count(Neuron.id))
        .where(Neuron.is_active == True)
        .group_by(Neuron.layer)
        .order_by(Neuron.layer)
    )
    layer_rows = by_layer.all()

    by_dept = await db.execute(
        select(Neuron.department, func.count(Neuron.id))
        .where(Neuron.is_active == True, Neuron.department.isnot(None))
        .group_by(Neuron.department)
        .order_by(func.count(Neuron.id).desc())
    )
    dept_rows = by_dept.all()

    promoted_count = (await db.execute(select(func.count()).select_from(NeuronEdge))).scalar() or 0
    from sqlalchemy import text
    weak_count_result = await db.execute(text(
        "SELECT COALESCE(SUM(jsonb_array_length("
        "  COALESCE((SELECT jsonb_agg(k) FROM jsonb_object_keys(weak_edges) k), '[]'::jsonb)"
        ")), 0) FROM neurons WHERE weak_edges IS NOT NULL"
    ))
    weak_count = weak_count_result.scalar() or 0
    edge_count = promoted_count + weak_count

    state = (await db.execute(select(SystemState).where(SystemState.id == 1))).scalar_one_or_none()
    total_queries = state.total_queries if state else 0

    lines = ["## Neuron Graph Statistics\n"]
    lines.append(f"- **Total active neurons**: {total}")
    lines.append(f"- **Total co-firing edges**: {edge_count} ({promoted_count} promoted, {weak_count} weak)")
    lines.append(f"- **Total queries executed**: {total_queries}")
    lines.append(f"\n### By Layer")
    for layer, count in layer_rows:
        lines.append(f"- L{layer}: {count} neurons")
    lines.append(f"\n### By Department")
    for dept, count in dept_rows:
        lines.append(f"- {dept}: {count} neurons")

    return PreparedContext(
        system_prompt="\n".join(lines),
        intent="structural_query",
        departments=[],
        role_keys=[],
        keywords=[],
        neuron_scores=[],
        neurons_activated=0,
    )


async def _handle_connections(db: AsyncSession, match: re.Match):
    """Load co-firing edges for a specific neuron."""
    from app.services.prepared_context import PreparedContext

    neuron_id = int(match.group(1))
    neuron = await db.get(Neuron, neuron_id)
    if not neuron:
        return None

    # Get top edges
    from sqlalchemy import or_
    result = await db.execute(
        select(NeuronEdge)
        .where(or_(NeuronEdge.source_id == neuron_id, NeuronEdge.target_id == neuron_id))
        .order_by(NeuronEdge.weight.desc())
        .limit(20)
    )
    edges = result.scalars().all()

    # Load connected neuron labels
    connected_ids = set()
    for e in edges:
        connected_ids.add(e.source_id if e.source_id != neuron_id else e.target_id)
    if connected_ids:
        n_result = await db.execute(select(Neuron).where(Neuron.id.in_(connected_ids)))
        n_map = {n.id: n for n in n_result.scalars().all()}
    else:
        n_map = {}

    lines = [f"## Connections for Neuron #{neuron_id}: {neuron.label}\n"]
    lines.append(f"Department: {neuron.department}, Layer: L{neuron.layer}\n")
    for e in edges:
        other_id = e.source_id if e.source_id != neuron_id else e.target_id
        other = n_map.get(other_id)
        label = other.label if other else f"#{other_id}"
        dept = other.department if other else "?"
        lines.append(f"- **{label}** ({dept}) — weight: {e.weight:.3f}, co-fires: {e.co_fire_count}")

    return PreparedContext(
        system_prompt="\n".join(lines),
        intent="structural_query",
        departments=[],
        role_keys=[],
        keywords=[],
        neuron_scores=[],
        neurons_activated=0,
    )


async def _handle_topic_search(db: AsyncSession, match: re.Match):
    """Embed topic and find top neurons by semantic similarity."""
    from app.services.prepared_context import PreparedContext

    topic = match.group(1).strip().rstrip("?.")
    if len(topic) < 3:
        return None

    # Try embedding-based search
    try:
        import concurrent.futures
        import asyncio
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            from app.services.embedding_service import embed_text
            query_embedding = await loop.run_in_executor(pool, embed_text, topic)
    except Exception:
        return None  # Fall through to full pipeline

    from app.services.semantic_prefilter import semantic_prefilter
    candidates = await semantic_prefilter(db, query_embedding, top_n_override=20)
    if not candidates:
        return None

    # Load neuron details (filter to neurons only — engrams handled in full pipeline)
    cand_ids = [eid for eid, etype, _ in candidates if etype == "neuron"]
    sim_map = {eid: sim for eid, etype, sim in candidates if etype == "neuron"}
    result = await db.execute(select(Neuron).where(Neuron.id.in_(cand_ids)))
    neurons = {n.id: n for n in result.scalars().all()}

    lines = [f"## Neurons Related to \"{topic}\"\n"]
    for nid in cand_ids:
        n = neurons.get(nid)
        if not n:
            continue
        sim = sim_map.get(nid, 0)
        lines.append(f"- **{n.label}** (#{n.id}, {n.department}, L{n.layer}) — similarity: {sim:.3f}")

    return PreparedContext(
        system_prompt="\n".join(lines),
        intent="structural_query",
        departments=[],
        role_keys=[],
        keywords=[topic],
        neuron_scores=[],
        neurons_activated=len(candidates),
    )


_HANDLERS = {
    "departments": _handle_departments,
    "roles_in_dept": _handle_roles_in_dept,
    "graph_stats": _handle_graph_stats,
    "connections": _handle_connections,
    "topic_search": _handle_topic_search,
}
