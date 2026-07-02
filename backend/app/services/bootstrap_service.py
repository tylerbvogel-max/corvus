"""Bootstrap firing service: pre-seed co-firing edges and invocation stats.

Seeds the neuron graph with informed priors based on:
  Pass 1 — Structural proximity (same parent, same department)
  Pass 2 — Cross-department collaboration affinity matrix
  Pass 3 — Semantic similarity (keyword/content overlap via shared concept linkage)
  Pass 5 — Intra-department semantic bridging (cross-role links within same dept)

All bootstrapped edges are tagged source='bootstrap' for traceability.
Bootstrapped weights are conservative (30-50% of organic equivalents) so real
usage quickly overtakes the priors.
"""

import json
import math
from collections import defaultdict

import numpy as np

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


# ── Cross-department affinity matrix ──
# Values represent collaboration frequency (0-1 scale).
# Symmetric — only define upper triangle; code mirrors both directions.
DEPT_AFFINITY: dict[tuple[str, str], float] = {
    # Engineering works closely with Manufacturing, Quality (Regulatory), and Program Management
    ("Engineering", "Manufacturing & Operations"): 0.70,
    ("Engineering", "Regulatory"): 0.55,
    ("Engineering", "Program Management"): 0.50,
    ("Engineering", "Contracts & Compliance"): 0.35,
    ("Engineering", "Finance"): 0.20,
    ("Engineering", "Business Development"): 0.30,
    ("Engineering", "Executive Leadership"): 0.20,
    ("Engineering", "Administrative & Support"): 0.10,
    # Manufacturing works closely with Quality, Engineering, Program Management
    ("Manufacturing & Operations", "Regulatory"): 0.65,
    ("Manufacturing & Operations", "Program Management"): 0.45,
    ("Manufacturing & Operations", "Contracts & Compliance"): 0.30,
    ("Manufacturing & Operations", "Finance"): 0.25,
    ("Manufacturing & Operations", "Business Development"): 0.15,
    ("Manufacturing & Operations", "Executive Leadership"): 0.20,
    ("Manufacturing & Operations", "Administrative & Support"): 0.15,
    # Contracts works closely with Finance, Program Management, Business Development
    ("Contracts & Compliance", "Finance"): 0.60,
    ("Contracts & Compliance", "Program Management"): 0.55,
    ("Contracts & Compliance", "Business Development"): 0.50,
    ("Contracts & Compliance", "Regulatory"): 0.45,
    ("Contracts & Compliance", "Executive Leadership"): 0.35,
    ("Contracts & Compliance", "Administrative & Support"): 0.20,
    # Finance works with Contracts, Program Management, Executive Leadership
    ("Finance", "Program Management"): 0.50,
    ("Finance", "Executive Leadership"): 0.45,
    ("Finance", "Business Development"): 0.35,
    ("Finance", "Regulatory"): 0.25,
    ("Finance", "Administrative & Support"): 0.20,
    # Program Management coordinates across most departments
    ("Program Management", "Regulatory"): 0.40,
    ("Program Management", "Business Development"): 0.45,
    ("Program Management", "Executive Leadership"): 0.50,
    ("Program Management", "Administrative & Support"): 0.15,
    # Business Development
    ("Business Development", "Executive Leadership"): 0.50,
    ("Business Development", "Regulatory"): 0.20,
    ("Business Development", "Administrative & Support"): 0.10,
    # Regulatory (Quality)
    ("Regulatory", "Executive Leadership"): 0.30,
    ("Regulatory", "Administrative & Support"): 0.15,
    # Executive Leadership
    ("Executive Leadership", "Administrative & Support"): 0.25,
}


def _get_affinity(dept_a: str, dept_b: str) -> float:
    """Look up affinity for a department pair (order-independent)."""
    if dept_a == dept_b:
        return 1.0
    key = (dept_a, dept_b) if (dept_a, dept_b) in DEPT_AFFINITY else (dept_b, dept_a)
    return DEPT_AFFINITY.get(key, 0.05)


async def bootstrap_firings(
    db: AsyncSession,
    dry_run: bool = False,
) -> dict:
    """Pre-seed co-firing edges and invocation estimates.

    Args:
        dry_run: If True, compute stats but don't write anything.

    Returns summary dict with counts and details per pass.
    """
    summary = {
        "pass_1_structural": {"edges_created": 0, "edges_updated": 0},
        "pass_2_cross_dept": {"edges_created": 0, "edges_updated": 0},
        "pass_3_concept": {"edges_created": 0, "edges_updated": 0},
        "pass_4_gap_fill": {"edges_created": 0},
        "neurons_updated": 0,
        "dry_run": dry_run,
    }

    # ── Load all active non-concept neurons with their hierarchy info ──
    result = await db.execute(text("""
        SELECT id, parent_id, layer, department, role_key, label,
               invocations, avg_utility, content
        FROM neurons
        WHERE is_active = true AND layer >= 0
        ORDER BY id
    """))
    rows = result.all()

    neurons = {}
    for r in rows:
        neurons[r[0]] = {
            "id": r[0], "parent_id": r[1], "layer": r[2], "department": r[3],
            "role_key": r[4], "label": r[5], "invocations": r[6],
            "avg_utility": r[7], "content": r[8] or "",
        }

    # Group neurons by parent and department
    by_parent: dict[int, list[int]] = defaultdict(list)
    by_dept: dict[str, list[int]] = defaultdict(list)
    by_dept_role: dict[tuple[str, str | None], list[int]] = defaultdict(list)

    for nid, n in neurons.items():
        if n["parent_id"]:
            by_parent[n["parent_id"]].append(nid)
        if n["department"]:
            by_dept[n["department"]].append(nid)
            by_dept_role[(n["department"], n["role_key"])].append(nid)

    edges_to_create: dict[tuple[int, int], dict] = {}

    def _add_edge(a: int, b: int, weight: float, pass_name: str):
        if a == b:
            return
        key = (min(a, b), max(a, b))
        existing = edges_to_create.get(key)
        if existing is None or weight > existing["weight"]:
            edges_to_create[key] = {"weight": weight, "pass": pass_name}

    _pass1_structural(neurons, by_parent, _add_edge)
    summary["pass_1_structural"]["edges_created"] = len(edges_to_create)
    p1_count = len(edges_to_create)

    _pass2_cross_dept(neurons, by_dept, _add_edge)
    summary["pass_2_cross_dept"]["edges_created"] = len(edges_to_create) - p1_count

    await _pass3_concept(db, _add_edge)
    p3_start = p1_count + summary["pass_2_cross_dept"]["edges_created"]
    summary["pass_3_concept"]["edges_created"] = len(edges_to_create) - p3_start

    gap_count = await _pass4_gap_fill(db, neurons, by_dept, edges_to_create, _add_edge)
    summary["pass_4_gap_fill"]["edges_created"] = gap_count

    if not dry_run:
        await _write_edges(db, edges_to_create)
        summary["neurons_updated"] = await _estimate_invocations(db, neurons, by_parent)
        await db.commit()

    summary["total_edges"] = len(edges_to_create)
    return summary


def _pass1_structural(neurons, by_parent, _add_edge):
    """Pass 1: Structural proximity — siblings sharing a parent get co-firing weight."""
    for parent_id, children in by_parent.items():
        if len(children) < 2:
            continue
        parent = neurons.get(parent_id)
        base_w = 0.30
        layer_bonus = (parent["layer"] / 5.0) * 0.15 if parent else 0.0
        sibling_weight = min(0.50, base_w + layer_bonus)
        for i, a in enumerate(children):
            for b in children[i + 1:]:
                _add_edge(a, b, sibling_weight, "structural")


def _pass2_cross_dept(neurons, by_dept, _add_edge):
    """Pass 2: Cross-department affinity — keyword overlap between L1/L2 neurons."""
    stop_words = {"and", "or", "the", "of", "in", "to", "for", "a", "an", "&"}
    dept_list = list(by_dept.keys())
    for i, dept_a in enumerate(dept_list):
        for dept_b in dept_list[i + 1:]:
            affinity = _get_affinity(dept_a, dept_b)
            if affinity < 0.20:
                continue
            neurons_a = [nid for nid in by_dept[dept_a] if neurons[nid]["layer"] in (1, 2)]
            neurons_b = [nid for nid in by_dept[dept_b] if neurons[nid]["layer"] in (1, 2)]
            if not neurons_a or not neurons_b:
                continue
            for na in neurons_a:
                words_a = set(neurons[na]["label"].lower().split())
                for nb in neurons_b:
                    words_b = set(neurons[nb]["label"].lower().split())
                    if (words_a & words_b) - stop_words:
                        _add_edge(na, nb, min(0.50, affinity * 0.6), "cross_dept")


async def _pass3_concept(db, _add_edge):
    """Pass 3: Concept-linked co-firing — neurons sharing a concept get edges."""
    concept_result = await db.execute(text("""
        SELECT n.id FROM neurons n
        WHERE n.node_type = 'concept' AND n.layer = -1 AND n.is_active = true
    """))
    concept_ids = [r[0] for r in concept_result.all()]

    for cid in concept_ids:
        linked_result = await db.execute(text("""
            SELECT CASE WHEN e.source_id = :cid THEN e.target_id ELSE e.source_id END AS linked_id,
                   n.department
            FROM neuron_edges e
            JOIN neurons n ON n.id = CASE WHEN e.source_id = :cid THEN e.target_id ELSE e.source_id END
            WHERE e.edge_type = 'instantiates'
              AND (e.source_id = :cid OR e.target_id = :cid)
              AND n.layer IN (1, 2)
              AND n.is_active = true
        """), {"cid": cid})
        linked = linked_result.all()
        if len(linked) < 2:
            continue
        concept_weight = min(0.40, 0.20 + len(linked) * 0.008)
        for i, (a, dept_a) in enumerate(linked):
            for b, dept_b in linked[i + 1:]:
                if a != b and dept_a != dept_b:
                    _add_edge(a, b, concept_weight, "concept")


async def _pass4_gap_fill(db, neurons, by_dept, edges_to_create, _add_edge) -> int:
    """Pass 4: Gap fill — ensure every neuron has at least one visible edge."""
    visibility_threshold = 0.30
    covered = set()
    for (a, b), info in edges_to_create.items():
        if info["weight"] >= visibility_threshold:
            covered.add(a)
            covered.add(b)
    existing_result = await db.execute(text("""
        SELECT DISTINCT unnest(ARRAY[source_id, target_id])
        FROM neuron_edges WHERE weight >= :thresh
    """), {"thresh": visibility_threshold})
    for row in existing_result.all():
        covered.add(row[0])

    gap_count = 0
    for nid, n in neurons.items():
        if nid in covered:
            continue
        pid = n["parent_id"]
        if pid and pid in neurons:
            _add_edge(nid, pid, visibility_threshold, "gap_fill")
            gap_count += 1
            continue
        for peer in by_dept.get(n["department"], []):
            if peer != nid and peer in covered:
                _add_edge(nid, peer, visibility_threshold, "gap_fill")
                gap_count += 1
                break
    return gap_count


async def write_planned_edges(db, edges_to_create):
    """Public entrypoint for planned-edge writes, shared with seeding_service.

    Edge type derives from region (department) equality in SQL; unregioned
    endpoints resolve to pyramidal until retype_edges_by_region runs.
    """
    await _write_edges(db, edges_to_create)


async def _write_edges(db, edges_to_create):
    """Write planned edges to database with upsert (always promoted: w >= 0.30)."""
    from app.services.edge_tier import delete_weak_edge
    for (src, tgt), info in edges_to_create.items():
        w = round(info["weight"], 4)
        co_fire = max(1, round(w * 10))
        await db.execute(text(
            "INSERT INTO neuron_edges (source_id, target_id, co_fire_count, weight, "
            "  last_updated_query, edge_type, source, last_adjusted) "
            "VALUES (:src, :tgt, :cfc, :w, 0, "
            "  CASE WHEN (SELECT department FROM neurons WHERE id = :src) = "
            "       (SELECT department FROM neurons WHERE id = :tgt) "
            "  THEN 'stellate' ELSE 'pyramidal' END, "
            "  'bootstrap', now()) "
            "ON CONFLICT (source_id, target_id) DO UPDATE SET "
            "  weight = GREATEST(neuron_edges.weight, :w), "
            "  source = CASE WHEN neuron_edges.source = 'organic' THEN neuron_edges.source ELSE 'bootstrap' END, "
            "  last_adjusted = now() "
            "WHERE neuron_edges.source != 'organic'"
        ), {"src": src, "tgt": tgt, "cfc": co_fire, "w": w})
        # Remove from JSONB if previously stored as weak edge
        await delete_weak_edge(db, src, tgt)


async def _estimate_invocations(db, neurons, by_parent) -> int:
    """Estimate invocations/utility for neurons with zero organic activity."""
    neuron_updates = 0
    for nid, n in neurons.items():
        if n["invocations"] > 0:
            continue
        layer_base = max(2, 20 - n["layer"] * 4)
        siblings = by_parent.get(n["parent_id"], []) if n["parent_id"] else []
        sibling_invocations = [neurons[s]["invocations"] for s in siblings if neurons[s]["invocations"] > 0]
        if sibling_invocations:
            estimated = round(sum(sibling_invocations) / len(sibling_invocations) * 0.5)
        else:
            estimated = layer_base
        await db.execute(text(
            "UPDATE neurons SET invocations = :inv, avg_utility = :util "
            "WHERE id = :nid AND invocations = 0"
        ), {"nid": nid, "inv": estimated, "util": 0.45})
        neuron_updates += 1
    return neuron_updates


async def get_bootstrap_stats(db: AsyncSession) -> dict:
    """Return statistics about bootstrap vs organic edges."""
    result = await db.execute(text("""
        SELECT
            source,
            COUNT(*) as edge_count,
            ROUND(AVG(weight)::numeric, 4) as avg_weight,
            ROUND(AVG(co_fire_count)::numeric, 1) as avg_cofires
        FROM neuron_edges
        WHERE source IS NOT NULL
        GROUP BY source
        ORDER BY source
    """))
    rows = result.all()

    stats = {}
    for r in rows:
        stats[r[0]] = {
            "edge_count": r[1],
            "avg_weight": float(r[2]) if r[2] else 0,
            "avg_cofires": float(r[3]) if r[3] else 0,
        }

    # Count neurons with bootstrap-only invocations (no organic firings)
    organic_fired = await db.execute(text("""
        SELECT COUNT(DISTINCT n.id)
        FROM neurons n
        JOIN neuron_firings nf ON nf.neuron_id = n.id
        WHERE n.is_active = true AND n.layer >= 0
    """))
    organic_count = organic_fired.scalar() or 0

    total_active = await db.execute(text("""
        SELECT COUNT(*) FROM neurons WHERE is_active = true AND layer >= 0
    """))
    total = total_active.scalar() or 0

    return {
        "edge_sources": stats,
        "neurons_with_organic_firings": organic_count,
        "neurons_bootstrap_only": total - organic_count,
        "total_active_neurons": total,
    }


async def purge_bootstrap(db: AsyncSession) -> dict:
    """Remove all bootstrap-sourced edges and reset bootstrap invocations.

    Use this if bootstrap priors are causing unwanted bias.
    """
    # Delete bootstrap edges from promoted table
    result = await db.execute(text(
        "DELETE FROM neuron_edges WHERE source = 'bootstrap'"
    ))
    edges_deleted = result.rowcount

    # Remove bootstrap entries from JSONB weak_edges
    await db.execute(text("""
        UPDATE neurons
        SET weak_edges = (
            SELECT jsonb_object_agg(key, value)
            FROM jsonb_each(weak_edges)
            WHERE value->>'s' != 'bootstrap'
        )
        WHERE weak_edges IS NOT NULL
    """))

    # Reset invocations on neurons that have no organic firings
    inv_result = await db.execute(text("""
        UPDATE neurons SET invocations = 0, avg_utility = 0.5
        WHERE is_active = true AND layer >= 0
          AND id NOT IN (SELECT DISTINCT neuron_id FROM neuron_firings)
          AND invocations > 0
    """))
    neurons_reset = inv_result.rowcount

    await db.commit()
    return {
        "edges_deleted": edges_deleted,
        "neurons_reset": neurons_reset,
    }


async def _load_dept_neurons_with_embeddings(
    db: AsyncSession,
) -> dict[str, list[dict]]:
    result = await db.execute(text("""
        SELECT id, department, role_key, layer, label, embedding
        FROM neurons
        WHERE is_active = true
          AND layer BETWEEN 1 AND 4
          AND department IS NOT NULL
          AND role_key IS NOT NULL
          AND embedding IS NOT NULL
          AND embedding != ''
        ORDER BY department, role_key, id
    """))
    rows = result.all()

    by_dept: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        try:
            emb = json.loads(r[5])
        except (json.JSONDecodeError, TypeError):
            continue
        by_dept[r[1]].append({
            "id": r[0], "department": r[1], "role_key": r[2],
            "layer": r[3], "label": r[4], "embedding": np.array(emb, dtype=np.float32),
        })
    return by_dept


def _find_cross_role_edges(
    dept_neurons: list[dict],
    similarity_threshold: float,
    min_weight: float,
    max_weight: float,
) -> list[dict]:
    roles = list({n["role_key"] for n in dept_neurons})
    if len(roles) < 2:
        return []

    by_role: dict[str, list[dict]] = defaultdict(list)
    for n in dept_neurons:
        by_role[n["role_key"]].append(n)

    edges = []
    for i, role_a in enumerate(roles):
        for role_b in roles[i + 1:]:
            neurons_a = by_role[role_a]
            neurons_b = by_role[role_b]

            embs_a = np.array([n["embedding"] for n in neurons_a])
            embs_b = np.array([n["embedding"] for n in neurons_b])
            sim_matrix = embs_a @ embs_b.T

            pairs = np.argwhere(sim_matrix >= similarity_threshold)
            for idx_a, idx_b in pairs:
                sim = float(sim_matrix[idx_a, idx_b])
                weight = min_weight + (sim - similarity_threshold) / (1.0 - similarity_threshold) * (max_weight - min_weight)
                weight = round(min(max_weight, max(min_weight, weight)), 4)

                na = neurons_a[idx_a]
                nb = neurons_b[idx_b]
                edges.append({
                    "src": min(na["id"], nb["id"]),
                    "tgt": max(na["id"], nb["id"]),
                    "weight": weight,
                    "sim": round(sim, 3),
                })
    return edges


async def _write_bridge_edges(db: AsyncSession, edges: list[dict]) -> int:
    """Write bridge edges (always promoted: w >= 0.20)."""
    from app.services.edge_tier import delete_weak_edge
    created = 0
    for edge in edges:
        co_fire = max(1, round(edge["weight"] * 10))
        r = await db.execute(text(
            "INSERT INTO neuron_edges (source_id, target_id, co_fire_count, weight, "
            "  last_updated_query, edge_type, source, last_adjusted) "
            "VALUES (:src, :tgt, :cfc, :w, 0, 'stellate', 'bootstrap', now()) "
            "ON CONFLICT (source_id, target_id) DO UPDATE SET "
            "  weight = GREATEST(neuron_edges.weight, :w), "
            "  source = CASE WHEN neuron_edges.source = 'organic' THEN neuron_edges.source ELSE 'bootstrap' END, "
            "  last_adjusted = now() "
            "WHERE neuron_edges.source != 'organic'"
        ), {"src": edge["src"], "tgt": edge["tgt"], "cfc": co_fire, "w": edge["weight"]})
        if r.rowcount > 0:
            created += 1
        # Remove from JSONB if previously stored as weak edge
        await delete_weak_edge(db, edge["src"], edge["tgt"])
    return created


async def intra_department_bridge(
    db: AsyncSession,
    similarity_threshold: float = 0.45,
    min_weight: float = 0.20,
    max_weight: float = 0.45,
    dry_run: bool = False,
) -> dict:
    """Bridge role clusters within the same department using embedding similarity.

    Computes pairwise cosine similarity between L1-L4 neurons in different roles
    within each department. Creates edges where similarity exceeds threshold, with
    weight proportional to similarity. Prevents intra-department role fragmentation.

    Returns summary dict with per-department bridge counts.
    """
    by_dept = await _load_dept_neurons_with_embeddings(db)

    dept_results = {}
    total_edges = 0
    total_created = 0

    for dept, dept_neurons in by_dept.items():
        edges_for_dept = _find_cross_role_edges(
            dept_neurons, similarity_threshold, min_weight, max_weight,
        )
        if not edges_for_dept:
            continue

        created = 0
        if not dry_run:
            created = await _write_bridge_edges(db, edges_for_dept)

        roles = list({n["role_key"] for n in dept_neurons})
        dept_results[dept] = {
            "role_pairs": len(roles) * (len(roles) - 1) // 2,
            "candidate_edges": len(edges_for_dept),
            "edges_written": created,
            "avg_similarity": round(sum(e["sim"] for e in edges_for_dept) / len(edges_for_dept), 3),
        }
        total_edges += len(edges_for_dept)
        total_created += created

    if not dry_run and total_created > 0:
        await db.commit()

    return {
        "departments": dept_results,
        "total_candidate_edges": total_edges,
        "total_edges_written": total_created,
        "similarity_threshold": similarity_threshold,
        "dry_run": dry_run,
    }
