"""Community detection via Leiden algorithm on co-firing edges.

Discovers cross-department neuron clusters that the manual hierarchy
doesn't capture. Uses igraph + leidenalg for deterministic, high-quality
community detection with configurable resolution.

Replaces the earlier label propagation approach for better cluster quality
and determinism (same input = same output).
"""

import numpy as np
from collections import Counter
import re

import igraph as ig
import leidenalg

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Neuron, NeuronEdge


_ZONE_STOP_WORDS = {
    "about", "after", "again", "also", "always", "and", "because", "before",
    "available", "between", "corvus", "could", "exists", "for", "from",
    "graph", "has", "have", "into", "lives", "memory", "more", "must",
    "neuron", "neurons", "node", "only", "project", "required", "requires",
    "run", "runs", "service", "services", "should", "system", "that", "the",
    "their", "there", "these", "this", "through", "use", "user", "users",
    "uses", "using", "when", "where", "which", "while", "with", "work",
    "would",
}
_ZONE_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+#.-]{2,}")
_TECHNICAL_TOKENS = {"api", "cli", "db", "llm", "mcp", "nvm", "sql", "ui", "ux"}


def _zone_tokens(label: str) -> set[str]:
    """Return informative, de-duplicated label tokens for deterministic naming."""
    tokens: set[str] = set()
    for raw in _ZONE_TOKEN_RE.findall(label):
        token = raw.lower().strip(".-")
        if token in _ZONE_STOP_WORDS:
            continue
        if token.startswith(("mind-", "project-", "corvus-")):
            continue
        tokens.add(token)
    return tokens


def _display_token(token: str) -> str:
    """Preserve useful technical casing while making ordinary words readable."""
    if token in _TECHNICAL_TOKENS:
        return token.upper()
    return token.capitalize()


def _build_igraph(
    edges: list, node_set: set[int]
) -> tuple[ig.Graph, list[int]]:
    """Build an igraph Graph from edge tuples and node set."""
    nodes = sorted(node_set)
    node_to_idx = {nid: i for i, nid in enumerate(nodes)}
    n = len(nodes)

    g = ig.Graph(n=n, directed=False)
    edge_list: list[tuple[int, int]] = []
    weights: list[float] = []
    for src, tgt, w in edges:
        i, j = node_to_idx[src], node_to_idx[tgt]
        edge_list.append((i, j))
        weights.append(w)

    g.add_edges(edge_list)
    g.es["weight"] = weights
    assert g.vcount() == n, f"Graph vertex count mismatch: {g.vcount()} != {n}"
    return g, nodes


def _leiden_clustering(
    g: ig.Graph,
    resolution: float = 1.0,
    seed: int = 42,
) -> list[int]:
    """Run Leiden community detection, return membership list.

    Uses RBConfigurationVertexPartition (modularity-like with resolution parameter).
    Higher resolution = more, smaller clusters. Lower = fewer, larger clusters.
    """
    assert g.vcount() > 0, "Graph must have at least one vertex"
    partition = leidenalg.find_partition(
        g,
        leidenalg.RBConfigurationVertexPartition,
        weights="weight",
        resolution_parameter=resolution,
        seed=seed,
    )
    assert len(partition.membership) == g.vcount(), "Membership length mismatch"
    return partition.membership


def _build_cluster_record(
    cluster_id: int,
    nids: list[int],
    edges: list,
    neuron_info: dict[int, dict],
    min_departments: int,
) -> dict | None:
    """Build a cluster record dict, or None if it doesn't meet dept threshold."""
    depts: set[str] = set()
    for nid in nids:
        info = neuron_info.get(nid, {})
        dept = info.get("department")
        if dept:
            depts.add(dept)
    if len(depts) < min_departments:
        return None

    nid_set = set(nids)
    internal_weights = [w for src, tgt, w in edges if src in nid_set and tgt in nid_set]
    avg_weight = float(np.mean(internal_weights)) if internal_weights else 0.0

    # Rank representative neurons by weighted internal degree. These examples
    # make a zone inspectable even when its compact name cannot carry every
    # concept in a mixed community.
    weighted_degree: Counter[int] = Counter()
    for src, tgt, weight in edges:
        if src in nid_set and tgt in nid_set:
            weighted_degree[src] += float(weight)
            weighted_degree[tgt] += float(weight)
    representative_ids = sorted(
        nids,
        key=lambda nid: (
            -weighted_degree[nid],
            neuron_info.get(nid, {}).get("label", "").lower(),
            nid,
        ),
    )[:3]
    representative_labels = [
        neuron_info[nid]["label"]
        for nid in representative_ids
        if neuron_info.get(nid, {}).get("label")
    ]

    # Count a term once per neuron label so repetitive wording in one long
    # label cannot dominate the name. The result is deterministic and incurs
    # no LLM call—the graph names its own territories from member vocabulary.
    token_docs: Counter[str] = Counter()
    for nid in nids:
        info = neuron_info.get(nid, {})
        token_docs.update(_zone_tokens(info.get("label", "")))
    common = sorted(
        token_docs,
        key=lambda token: (
            -token_docs[token],
            token not in _TECHNICAL_TOKENS,
            -len(token),
            token,
        ),
    )[:3]
    if common:
        suggested = " · ".join(_display_token(token) for token in common)
    elif representative_labels:
        suggested = representative_labels[0][:72]
    else:
        suggested = f"Zone {cluster_id + 1}"

    return {
        "cluster_id": cluster_id,
        "neuron_ids": sorted(nids),
        "member_count": len(nids),
        "departments": sorted(depts),
        "avg_internal_weight": avg_weight,
        "suggested_label": suggested,
        "representative_labels": representative_labels,
    }


async def find_clusters(
    db: AsyncSession,
    min_weight: float = 0.3,
    min_size: int = 3,
    min_departments: int = 2,
    resolution: float = 1.0,
) -> list[dict]:
    """Run Leiden community detection and return cross-department clusters.

    Args:
        min_weight: Minimum edge weight to include (default 0.3)
        min_size: Minimum cluster size (default 3)
        min_departments: Minimum departments spanned (default 2)
        resolution: Leiden resolution parameter (default 1.0). Higher = more clusters.

    Returns list of dicts with: cluster_id, neuron_ids, departments,
    avg_internal_weight, suggested_label.
    """
    result = await db.execute(
        select(NeuronEdge.source_id, NeuronEdge.target_id, NeuronEdge.weight)
        .where(NeuronEdge.weight >= min_weight)
    )
    edges = result.all()

    if not edges:
        return []

    node_set: set[int] = set()
    for src, tgt, _ in edges:
        node_set.add(src)
        node_set.add(tgt)

    g, nodes = _build_igraph(edges, node_set)
    membership = _leiden_clustering(g, resolution=resolution)

    # Group nodes by community label
    clusters_raw: dict[int, list[int]] = {}
    for i, lbl in enumerate(membership):
        clusters_raw.setdefault(lbl, []).append(nodes[i])

    # Load neuron info for labeling
    neuron_result = await db.execute(
        select(Neuron.id, Neuron.label, Neuron.department, Neuron.layer)
        .where(Neuron.id.in_(list(node_set)))
    )
    neuron_info = {r[0]: {"label": r[1], "department": r[2], "layer": r[3]} for r in neuron_result.all()}

    clusters: list[dict] = []
    for cluster_id, (lbl, nids) in enumerate(clusters_raw.items()):
        if len(nids) < min_size:
            continue
        record = _build_cluster_record(cluster_id, nids, edges, neuron_info, min_departments)
        if record is not None:
            clusters.append(record)

    clusters.sort(key=lambda c: len(c["neuron_ids"]), reverse=True)

    return clusters
