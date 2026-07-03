"""MCP server exposing Corvus's neuron graph as tools for Claude Code.

Runs as stdio transport — Claude Code spawns this as a child process.
Shares the same PostgreSQL connection pool as the FastAPI app.
"""

import json
from types import MappingProxyType

from mcp.server.fastmcp import FastMCP

from app.config import settings
from app.database import async_session
from app.models import Neuron, NeuronEdge, SystemState, Query, CitationHopSession
from app.services.citation_hopping import serialize_hop_map, deserialize_hop_map
from sqlalchemy import select, func, or_

# Next-step hints for AI agent tool chaining (JPL-6: immutable mapping)
TOOL_HINTS = MappingProxyType({
    "query_graph": (
        "Use neuron_detail(neuron_id) to inspect a specific neuron's content and edges",
        "Use impact_analysis(topic) for zero-cost semantic search on a related topic",
        "Use discover_clusters() to find cross-department knowledge patterns",
        "If a hop_session_id is returned, call verify_citations(hop_session_id, your_answer) to confirm your citations are real before you rely on them",
    ),
    "impact_analysis": (
        "Use neuron_detail(neuron_id) to get full content for any neuron in the results",
        "Use query_graph(query) to run the full scoring pipeline with these neurons",
        "Use browse_departments(department) to explore related roles in a department",
    ),
    "neuron_detail": (
        "Use impact_analysis(topic) to find neurons semantically similar to this one",
        "Use browse_departments(department) to see sibling roles in the same department",
        "Use query_graph(query) to see how this neuron scores against a specific query",
    ),
    "browse_departments": (
        "Use neuron_detail(neuron_id) to inspect a specific neuron within a role",
        "Use impact_analysis(topic) to search across all departments by meaning",
        "Use graph_stats() to see overall graph health and coverage",
    ),
    "graph_stats": (
        "Use browse_departments() to drill into a specific department",
        "Use discover_clusters() to find emergent cross-department patterns",
        "Use cost_report() to check API cost and token usage",
    ),
    "cost_report": (
        "Use graph_stats() to see neuron graph health alongside cost data",
        "Use query_graph(query) to run a query and see per-query cost breakdown",
    ),
    "discover_clusters": (
        "Use neuron_detail(neuron_id) to inspect neurons within a cluster",
        "Use impact_analysis(topic) to find related neurons outside the cluster",
        "Use browse_departments() to compare cluster membership against org hierarchy",
    ),
    "verify_citations": (
        "A non-empty hallucinated[] means you cited neuron keys that do not exist — drop those claims or re-answer using only listed keys",
        "Use neuron_detail(neuron_id) on cited_neuron_ids to confirm the sources you grounded on",
    ),
})


async def _persist_hop_session(db, ctx) -> int | None:
    """Persist the secret per-query hop map so verify_citations can later grade
    an external agent's answer. Returns the session id, or None when hopping is
    off. The map is never returned to the agent — only the opaque session id."""
    if not settings.citation_hopping_enabled or ctx.hop_map is None:
        return None
    session = CitationHopSession(
        token_map_json=serialize_hop_map(ctx.hop_map),
        required_json=(
            sorted(ctx.hop_map.tokens())
            if settings.citation_hop_require_all else None
        ),
    )
    db.add(session)
    await db.flush()
    return session.id


def _with_hints(tool_name: str, result_dict: dict) -> str:
    """Inject next_steps hints into a tool result dict and return JSON string."""
    assert isinstance(result_dict, dict), f"result_dict must be a dict, got {type(result_dict)}"
    assert tool_name in TOOL_HINTS, f"Unknown tool: {tool_name}"
    result_dict["next_steps"] = list(TOOL_HINTS[tool_name])
    return json.dumps(result_dict, indent=2)


mcp = FastMCP(
    "corvus",
    instructions=(
        "Corvus is a biomimetic neuron graph for prompt preparation. "
        "Use query_graph to get enriched context for any question. "
        "Use browse_departments, neuron_detail, and impact_analysis to explore the graph. "
        "Use graph_stats and cost_report for system health."
    ),
)


@mcp.tool()
async def query_graph(
    query: str, top_k: int = 30, token_budget: int = 4000,
    project_path: str | None = None, mode: str = "adaptive",
    requester_regions: list[str] | None = None,
) -> str:
    """Run the neuron graph pipeline (classify → score → spread → inhibit → assemble) and return enriched context.

    This is the primary tool — returns a system prompt built from the most relevant neurons
    in the graph. Use the returned system_prompt as enriched context for answering questions.

    Args:
        query: The user's question or topic
        top_k: Maximum neurons to activate (default 30)
        token_budget: Token budget for the assembled prompt (default 4000)
        project_path: Optional project directory path for per-project neuron boosting
        mode: Recall mode — "adaptive" (default: embed-only recall, LLM classify
            only when the query is ambiguous), "cheap" (never call an LLM), or
            "full" (LLM classify on every read)
        requester_regions: Optional region scope for the requester — recall is
            bounded to knowledge visible to these regions (restricted regions
            outside this list are excluded). Omit for unrestricted recall.
    """
    from app.services.executor import prepare_context
    from app.services.region_policy import RequesterContext

    if mode not in ("adaptive", "cheap", "full"):
        return json.dumps({"error": f"mode must be adaptive|cheap|full, got {mode!r}"})

    requester = None
    if requester_regions is not None:
        requester = RequesterContext(
            principal="mcp", regions=tuple(requester_regions), privileged=False,
        )

    async with async_session() as db:
        ctx = await prepare_context(
            db, query,
            token_budget=token_budget,
            top_k=top_k,
            project_path=project_path,
            recall_mode=mode,
            requester=requester,
        )
        hop_session_id = await _persist_hop_session(db, ctx)
        await db.commit()

        result = {
            "system_prompt": ctx.system_prompt,
            "neurons_activated": ctx.neurons_activated,
            "departments": ctx.departments,
            "intent": ctx.intent,
            "recall_mode": mode,
            "classify_cost_usd": ctx.classify_cost_usd,
            "neuron_scores": ctx.neuron_scores[:10],  # Top 10 for brevity
        }
        if hop_session_id is not None:
            # External agent (analysis layer) verifies via verify_citations.
            result["hop_session_id"] = hop_session_id
        return _with_hints("query_graph", result)


@mcp.tool()
async def verify_citations(hop_session_id: int, answer: str) -> str:
    """Frequency-hop exit layer: verify an answer's citation keys are real.

    After answering a query_graph result that returned a hop_session_id, pass
    that id and your answer text here. Corvus checks every citation key against
    the secret per-query map and reports any fabricated (hallucinated) neuron
    references. Keys are unique per query, so a guessed or reused key fails.

    Args:
        hop_session_id: the id returned by query_graph for this answer
        answer: your full answer text (citation keys are extracted from it)
    """
    from app.services.citation_hopping import (
        extract_citation_tokens, verify_citations as _verify,
    )

    async with async_session() as db:
        session = await db.get(CitationHopSession, hop_session_id)
        if session is None:
            return json.dumps({"error": f"unknown hop_session_id {hop_session_id}"})
        hop_map = deserialize_hop_map(session.token_map_json)
        result = _verify(
            extract_citation_tokens(answer), hop_map,
            require_all=bool(session.required_json),
        )
        session.audit_json = result.to_dict()
        await db.commit()
        return _with_hints("verify_citations", {
            "ok": result.ok,
            "hallucinated": result.hallucinated,
            "missing": result.missing,
            "cited_neuron_ids": result.cited_neuron_ids,
            "cited_engram_ids": result.cited_engram_ids,
            "allowed_count": len(result.allowed),
            "used_count": len(result.used),
        })


@mcp.tool()
async def impact_analysis(topic: str, top_n: int = 20, graph_trace: bool = True) -> str:
    """Find neurons related to a topic via semantic search and graph traversal blast radius.

    With graph_trace=True (default): seeds top-N semantic matches, then BFS through
    co-firing edges to discover direct (1-hop), indirect (2-hop), and transitive (3-hop)
    impact. Returns tiered results with confidence scores.

    With graph_trace=False: returns only semantic similarity results (original behavior).

    Pure CPU + DB read, zero API cost.

    Args:
        topic: The topic to search for
        top_n: Number of top seed neurons for semantic search (default 20)
        graph_trace: Enable graph traversal blast radius (default True)
    """
    import concurrent.futures
    import asyncio
    from app.config import settings

    try:
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            from app.services.embedding_service import embed_text
            query_embedding = await loop.run_in_executor(pool, embed_text, topic)
    except Exception as e:
        return json.dumps({"error": f"Embedding failed: {e}"})

    async with async_session() as db:
        from app.services.semantic_prefilter import semantic_prefilter
        candidates = await semantic_prefilter(db, query_embedding, top_n_override=top_n)

        if not candidates:
            return json.dumps({"neurons": [], "message": "No similar neurons found"})

        # semantic_prefilter returns (entity_id, entity_type, similarity)
        neuron_hits = [(eid, sim) for eid, etype, sim in candidates if etype == "neuron"]
        cand_ids = [eid for eid, _sim in neuron_hits]
        sim_map = dict(neuron_hits)

        if graph_trace:
            # Use top seeds for blast radius BFS
            from app.services.impact_service import compute_blast_radius
            seed_count = min(settings.impact_seed_count, len(cand_ids))
            seed_ids = cand_ids[:seed_count]

            blast = await compute_blast_radius(
                db, seed_ids, sim_map,
                max_hops=settings.impact_max_hops,
                min_edge_weight=settings.impact_min_edge_weight,
            )
            return _with_hints("impact_analysis", {
                "mode": "graph_trace",
                "topic": topic,
                **blast,
            })

        # Flat semantic-only results (original behavior)
        result = await db.execute(select(Neuron).where(Neuron.id.in_(cand_ids)))
        neurons = {n.id: n for n in result.scalars().all()}

        output = []
        for nid in cand_ids:
            n = neurons.get(nid)
            if not n:
                continue
            output.append({
                "neuron_id": nid,
                "label": n.label,
                "department": n.department,
                "layer": n.layer,
                "role_key": n.role_key,
                "similarity": round(sim_map.get(nid, 0), 4),
            })

        return _with_hints("impact_analysis", {"neurons": output})


@mcp.tool()
async def neuron_detail(neuron_id: int) -> str:
    """Get full details for a specific neuron including content, scores, and top co-firing edges.

    Args:
        neuron_id: The neuron ID to inspect
    """
    async with async_session() as db:
        neuron = await db.get(Neuron, neuron_id)
        if not neuron:
            return json.dumps({"error": f"Neuron {neuron_id} not found"})

        # Get top co-firing edges
        result = await db.execute(
            select(NeuronEdge)
            .where(or_(NeuronEdge.source_id == neuron_id, NeuronEdge.target_id == neuron_id))
            .order_by(NeuronEdge.weight.desc())
            .limit(10)
        )
        edges = result.scalars().all()

        # Load connected neuron labels
        connected_ids = set()
        for e in edges:
            connected_ids.add(e.source_id if e.source_id != neuron_id else e.target_id)
        n_map = {}
        if connected_ids:
            n_result = await db.execute(select(Neuron).where(Neuron.id.in_(connected_ids)))
            n_map = {n.id: n for n in n_result.scalars().all()}

        edge_list = []
        for e in edges:
            other_id = e.source_id if e.source_id != neuron_id else e.target_id
            other = n_map.get(other_id)
            edge_list.append({
                "target_id": other_id,
                "target_label": other.label if other else f"#{other_id}",
                "weight": round(e.weight, 4),
                "co_fire_count": e.co_fire_count,
            })

        return _with_hints("neuron_detail", {
            "id": neuron.id,
            "label": neuron.label,
            "layer": neuron.layer,
            "department": neuron.department,
            "role_key": neuron.role_key,
            "node_type": neuron.node_type,
            "content": neuron.content,
            "summary": neuron.summary,
            "invocations": neuron.invocations,
            "avg_utility": round(neuron.avg_utility, 4) if neuron.avg_utility else 0,
            "is_active": neuron.is_active,
            "top_edges": edge_list,
        })


@mcp.tool()
async def browse_departments(department: str | None = None) -> str:
    """Browse the neuron graph hierarchy.

    Without arguments: lists all departments with neuron counts.
    With department: lists roles and neuron counts within that department.

    Args:
        department: Optional department name to drill into
    """
    async with async_session() as db:
        if department is None:
            result = await db.execute(
                select(Neuron.department, func.count(Neuron.id))
                .where(Neuron.is_active == True, Neuron.department.isnot(None))
                .group_by(Neuron.department)
                .order_by(func.count(Neuron.id).desc())
            )
            return _with_hints("browse_departments", {
                "departments": [
                    {"name": dept, "neuron_count": count}
                    for dept, count in result.all()
                ]
            })
        else:
            result = await db.execute(
                select(Neuron.role_key, func.count(Neuron.id))
                .where(
                    Neuron.is_active == True,
                    Neuron.department == department,
                    Neuron.role_key.isnot(None),
                )
                .group_by(Neuron.role_key)
                .order_by(func.count(Neuron.id).desc())
            )
            return _with_hints("browse_departments", {
                "department": department,
                "roles": [
                    {"role_key": rk, "neuron_count": count}
                    for rk, count in result.all()
                ]
            })


@mcp.tool()
async def graph_stats() -> str:
    """Get overall neuron graph statistics: totals, layer/department breakdowns, edge counts."""
    async with async_session() as db:
        total = (await db.execute(
            select(func.count(Neuron.id)).where(Neuron.is_active == True)
        )).scalar() or 0

        by_layer = await db.execute(
            select(Neuron.layer, func.count(Neuron.id))
            .where(Neuron.is_active == True)
            .group_by(Neuron.layer)
            .order_by(Neuron.layer)
        )

        by_dept = await db.execute(
            select(Neuron.department, func.count(Neuron.id))
            .where(Neuron.is_active == True, Neuron.department.isnot(None))
            .group_by(Neuron.department)
            .order_by(func.count(Neuron.id).desc())
        )

        promoted_count = (await db.execute(select(func.count()).select_from(NeuronEdge))).scalar() or 0
        from sqlalchemy import text as sa_text
        weak_count = (await db.execute(sa_text(
            "SELECT COALESCE(SUM(jsonb_array_length("
            "  COALESCE((SELECT jsonb_agg(k) FROM jsonb_object_keys(weak_edges) k), '[]'::jsonb)"
            ")), 0) FROM neurons WHERE weak_edges IS NOT NULL"
        ))).scalar() or 0
        edge_count = promoted_count + weak_count

        state = (await db.execute(select(SystemState).where(SystemState.id == 1))).scalar_one_or_none()

        total_firings = 0
        if state:
            from app.models import NeuronFiring
            total_firings = (await db.execute(
                select(func.count(NeuronFiring.id))
            )).scalar() or 0

        return _with_hints("graph_stats", {
            "total_neurons": total,
            "total_edges": edge_count,
            "total_queries": state.total_queries if state else 0,
            "total_firings": total_firings,
            "by_layer": {f"L{layer}": count for layer, count in by_layer.all()},
            "by_department": {dept: count for dept, count in by_dept.all()},
        })


@mcp.tool()
async def cost_report() -> str:
    """Get cost and token usage summary across all queries."""
    async with async_session() as db:
        result = await db.execute(
            select(
                func.count(Query.id),
                func.coalesce(func.sum(Query.cost_usd), 0),
                func.coalesce(func.sum(Query.classify_input_tokens + Query.classify_output_tokens), 0),
                func.coalesce(func.sum(Query.execute_input_tokens + Query.execute_output_tokens), 0),
            )
        )
        row = result.one()
        total_queries = row[0]
        total_cost = float(row[1])
        classify_tokens = int(row[2])
        execute_tokens = int(row[3])
        total_tokens = classify_tokens + execute_tokens

        return _with_hints("cost_report", {
            "total_queries": total_queries,
            "total_cost_usd": round(total_cost, 6),
            "avg_cost_per_query": round(total_cost / total_queries, 6) if total_queries else 0,
            "total_tokens": total_tokens,
            "classify_tokens": classify_tokens,
            "execute_tokens": execute_tokens,
        })


@mcp.tool()
async def reconciliation_report() -> str:
    """Open cross-region discrepancies (contradictions, staleness divergence,
    homonym/synonym collisions, seam gaps) grouped by type and owning region.

    The horizontal reconciler detects these across silo seams and routes them
    to the owning region's controller; this report is the read-only console
    view. Detection only — no raw restricted content is included.
    """
    from app.models import IntegrityFinding
    from sqlalchemy import select as sa_select

    async with async_session() as db:
        stmt = (
            sa_select(IntegrityFinding)
            .where(
                IntegrityFinding.status == "open",
                IntegrityFinding.finding_type.in_((
                    "contradiction", "staleness_divergence",
                    "homonym_synonym", "seam_gap",
                )),
                IntegrityFinding.region.isnot(None),
            )
            .order_by(IntegrityFinding.priority_score.desc())
            .limit(100)
        )
        findings = (await db.execute(stmt)).scalars().all()

        by_type: dict[str, int] = {}
        by_region: dict[str, int] = {}
        for f in findings:
            by_type[f.finding_type] = by_type.get(f.finding_type, 0) + 1
            by_region[f.region or "?"] = by_region.get(f.region or "?", 0) + 1

        return json.dumps({
            "open_cross_region_findings": len(findings),
            "by_type": by_type,
            "by_owning_region": by_region,
            "top_findings": [
                {
                    "id": f.id, "type": f.finding_type, "region": f.region,
                    "severity": f.severity, "priority": f.priority_score,
                    "description": (f.description or "")[:300],
                }
                for f in findings[:15]
            ],
        }, indent=2)


@mcp.tool()
async def discover_clusters(min_weight: float = 0.3, min_size: int = 3, resolution: float = 1.0) -> str:
    """Discover emergent cross-department neuron clusters via Leiden community detection on co-firing edges.

    Finds groups of neurons that frequently co-fire across department boundaries,
    revealing hidden knowledge patterns the manual hierarchy doesn't capture.

    Args:
        min_weight: Minimum edge weight to include (default 0.3)
        min_size: Minimum cluster size (default 3)
        resolution: Leiden resolution parameter (default 1.0). Higher = more, smaller clusters.
    """
    from app.services.clustering import find_clusters

    async with async_session() as db:
        clusters = await find_clusters(db, min_weight=min_weight, min_size=min_size, resolution=resolution)

        return _with_hints("discover_clusters", {
            "cluster_count": len(clusters),
            "clusters": [
                {
                    "cluster_id": c["cluster_id"],
                    "neuron_ids": c["neuron_ids"],
                    "departments": c["departments"],
                    "neuron_count": len(c["neuron_ids"]),
                    "dept_count": len(c["departments"]),
                    "avg_internal_weight": round(c["avg_internal_weight"], 4),
                    "suggested_label": c["suggested_label"],
                }
                for c in clusters
            ]
        })


if __name__ == "__main__":
    mcp.run(transport="stdio")
