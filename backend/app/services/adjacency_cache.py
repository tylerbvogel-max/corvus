"""Graph-neighbor access for spread activation.

Biological analogue: pre-synaptic vesicle pools. Instead of synthesizing
neurotransmitters on demand (DB query per hop), the cell maintains a ready
pool of vesicles (in-memory adjacency dict) that can release instantly.

Two explicit operating modes:
- process-local: full adjacency + CSR replica for single-worker latency
- database: bounded frontier reads from canonical PostgreSQL for horizontal
  correctness; no worker owns a graph-sized replica

Feature-flagged via settings.spread_enabled (if spread is off, cache is never loaded).
"""

import math
import threading

import numpy as np

from app.config import settings

# Edge-type codes for the vectorized CSR view (must match _compute_edge_activation).
# Code 3 = memory-semantics edges (supersedes / scoped-by / evidence-link):
# provenance and temporal links, NEVER activation conduits — a supersedes
# edge boosting the node it demotes would be exactly backwards.
_ETYPE_CODE = {
    "stellate": 1, "instantiates": 2,
    "supersedes": 3, "scoped-by": 3, "evidence-link": 3,
}  # everything else (pyramidal/regulatory) -> 0


class _AdjacencyCache:
    """Thread-safe in-memory cache of the co-firing edge graph as adjacency lists."""

    def __init__(self):
        self._lock = threading.Lock()
        # neuron_id -> [(neighbor_id, weight, edge_type)]
        self._adjacency: dict[int, list[tuple[int, float, str]]] = {}
        self._loaded = False
        # Lazily-built CSR view for vectorized spread; invalidated on any mutation.
        self._csr: dict | None = None

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def load(
        self,
        edges: list[tuple[int, int, float, str]],
    ) -> None:
        """Build bidirectional adjacency dict from all edges.

        Each edge tuple: (source_id, target_id, weight, edge_type).
        """
        assert isinstance(edges, list), "edges must be a list"
        with self._lock:
            adj: dict[int, list[tuple[int, float, str]]] = {}
            for src, tgt, weight, etype in edges:
                adj.setdefault(src, []).append((tgt, weight, etype))
                adj.setdefault(tgt, []).append((src, weight, etype))
            self._adjacency = adj
            self._loaded = True
            self._csr = None

    def invalidate(self) -> None:
        """Force reload on next access (e.g. after edge pruning)."""
        with self._lock:
            self._loaded = False
            self._adjacency = {}
            self._csr = None

    def update_edges(
        self,
        pairs: list[tuple[int, int]],
        weights: list[float],
        edge_types: list[str],
    ) -> None:
        """Incremental update after co-firing edge writes.

        Updates existing edges or adds new ones without full cache rebuild.
        """
        assert len(pairs) == len(weights) == len(edge_types), \
            "pairs, weights, and edge_types must have equal length"
        with self._lock:
            if not self._loaded:
                return
            for (src, tgt), weight, etype in zip(pairs, weights, edge_types):
                # Update or add src -> tgt
                self._update_single_direction(src, tgt, weight, etype)
                # Update or add tgt -> src
                self._update_single_direction(tgt, src, weight, etype)
            self._csr = None

    def _update_single_direction(
        self, from_id: int, to_id: int, weight: float, etype: str,
    ) -> None:
        """Update a single direction of an edge in the adjacency dict.

        Must be called while holding self._lock.
        """
        neighbors = self._adjacency.get(from_id)
        if neighbors is None:
            self._adjacency[from_id] = [(to_id, weight, etype)]
            return
        for i, (nid, _w, _e) in enumerate(neighbors):
            if nid == to_id:
                neighbors[i] = (to_id, weight, etype)
                return
        neighbors.append((to_id, weight, etype))

    def remove_neuron(self, neuron_id: int) -> None:
        """Remove all edges for a neuron from the cache."""
        with self._lock:
            if not self._loaded:
                return
            # Remove the neuron's own adjacency list
            neighbors = self._adjacency.pop(neuron_id, [])
            # Remove reverse references from all neighbors
            for nid, _w, _e in neighbors:
                peer_list = self._adjacency.get(nid)
                if peer_list is not None:
                    self._adjacency[nid] = [
                        (n, w, e) for n, w, e in peer_list if n != neuron_id
                    ]
            self._csr = None

    def remove_edges(
        self,
        pairs: list[tuple[int, int]],
    ) -> None:
        """Remove specific edges from the cache (e.g. after demotion to JSONB)."""
        assert isinstance(pairs, list), "pairs must be a list"
        with self._lock:
            if not self._loaded:
                return
            for src, tgt in pairs:
                self._remove_single_direction(src, tgt)
                self._remove_single_direction(tgt, src)
            self._csr = None

    def _remove_single_direction(self, from_id: int, to_id: int) -> None:
        """Remove a single direction of an edge. Must hold self._lock."""
        neighbors = self._adjacency.get(from_id)
        if neighbors is not None:
            self._adjacency[from_id] = [
                (n, w, e) for n, w, e in neighbors if n != to_id
            ]

    def get_neighbors(
        self,
        neuron_ids: set[int],
        min_weight: float,
    ) -> dict[int, list[tuple[int, float, str]]]:
        """Return filtered neighbor lists for a set of neuron IDs.

        Returns {neuron_id: [(neighbor_id, weight, edge_type), ...]}
        with only edges at or above min_weight.
        """
        assert isinstance(neuron_ids, set), "neuron_ids must be a set"
        with self._lock:
            if not self._loaded:
                return {}
            result: dict[int, list[tuple[int, float, str]]] = {}
            for nid in neuron_ids:
                neighbors = self._adjacency.get(nid)
                if neighbors is not None:
                    filtered = [
                        (n, w, e) for n, w, e in neighbors if w >= min_weight
                    ]
                    if filtered:
                        result[nid] = filtered
            return result

    def get_csr(self) -> dict | None:
        """Lazily build + cache a CSR-by-source view for vectorized spread.

        Keys: id_list (int64 [N], index->node id), id2idx (dict), indptr
        (int64 [N+1], CSR row pointers by source index), indices (int64 [E],
        destination index per out-edge), weight (float32 [E]), etype (int8 [E],
        0 pyramidal/other · 1 stellate · 2 instantiates). Built from the same
        symmetric adjacency dict, so it carries both edge directions.
        """
        with self._lock:
            if self._csr is not None:
                return self._csr
            if not self._loaded:
                return None
            id_list = list(self._adjacency.keys())
            id2idx = {nid: i for i, nid in enumerate(id_list)}
            n = len(id_list)
            indptr = np.zeros(n + 1, dtype=np.int64)
            dst: list[int] = []
            wts: list[float] = []
            ets: list[int] = []
            for i in range(n):
                cnt = 0
                for tgt, weight, etype in self._adjacency[id_list[i]]:
                    j = id2idx.get(tgt)
                    if j is None:
                        continue
                    dst.append(j)
                    wts.append(weight)
                    ets.append(_ETYPE_CODE.get(etype, 0))
                    cnt += 1
                indptr[i + 1] = indptr[i] + cnt
            self._csr = {
                "id_list": np.array(id_list, dtype=np.int64),
                "id2idx": id2idx,
                "indptr": indptr,
                "indices": np.array(dst, dtype=np.int64),
                "weight": np.array(wts, dtype=np.float64),  # float64 to match the reference path exactly
                "etype": np.array(ets, dtype=np.int8),
            }
            return self._csr


# Module-level singleton
_cache = _AdjacencyCache()


async def ensure_adjacency_loaded(db) -> None:
    """Load the adjacency cache from DB if not already loaded (neurons + engrams).

    Engram IDs are stored as negative values to avoid collision with neuron IDs
    in the shared adjacency dict.  Callers use engram_id_to_key() / key_to_engram_id()
    to convert.
    """
    if settings.cache_coherence_mode == "database":
        # High-churn co-fire edges change on nearly every query. A whole-graph
        # replica cannot be made horizontally coherent without reloading nearly
        # every query, so database mode deliberately keeps this cache empty.
        if _cache.is_loaded:
            _cache.invalidate()
        return
    if _cache.is_loaded:
        return

    from sqlalchemy import text

    # Load neuron-neuron edges
    result = await db.execute(
        text("SELECT source_id, target_id, weight, edge_type FROM neuron_edges")
    )
    edges = [
        (int(src), int(tgt), float(w), etype or "pyramidal")
        for src, tgt, w, etype in result.all()
    ]
    neuron_edge_count = len(edges)

    # Load engram-neuron edges (engram IDs negated to avoid collision)
    try:
        engram_result = await db.execute(
            text("SELECT engram_id, neuron_id, weight, edge_type FROM engram_edges")
        )
        for eid, nid, w, etype in engram_result.all():
            edges.append((-int(eid), int(nid), float(w), etype or "regulatory"))
    except Exception:
        pass  # engram_edges table may not exist yet

    engram_edge_count = len(edges) - neuron_edge_count

    if edges:
        _cache.load(edges)
        total_nodes = len(_cache._adjacency)
        print(f"Adjacency cache loaded: {neuron_edge_count} neuron + {engram_edge_count} engram edges, {total_nodes} nodes in graph")
    else:
        _cache.load([])
        print("Adjacency cache: no edges found")


def engram_id_to_key(engram_id: int) -> int:
    """Convert an engram ID to its adjacency cache key (negative)."""
    return -engram_id


def key_to_engram_id(key: int) -> int:
    """Convert a negative adjacency key back to an engram ID."""
    return -key


def is_engram_key(key: int) -> bool:
    """Check if an adjacency cache key represents an engram (negative)."""
    return key < 0


def invalidate_adjacency_cache() -> None:
    """Call after edge pruning or bulk edge operations to force reload."""
    _cache.invalidate()


def update_adjacency_incremental(
    pairs: list[tuple[int, int]],
    weights: list[float],
    edge_types: list[str],
) -> None:
    """Incrementally update the cache after co-firing edge writes."""
    assert len(pairs) > 0, "pairs must not be empty"
    _cache.update_edges(pairs, weights, edge_types)


def remove_adjacency_edges(
    pairs: list[tuple[int, int]],
) -> None:
    """Remove specific edges from the cache after demotion to JSONB."""
    if pairs:
        _cache.remove_edges(pairs)


def get_cached_neighbors(
    neuron_ids: set[int],
    min_weight: float,
) -> dict[int, list[tuple[int, float, str]]]:
    """Return cached neighbor lists for spread activation."""
    return _cache.get_neighbors(neuron_ids, min_weight)


async def get_graph_neighbors(
    db,
    node_ids: set[int],
    min_weight: float,
    traversal_metrics: dict | None = None,
) -> dict[int, list[tuple[int, float, str]]]:
    """Return neighbors from the configured graph-read backend.

    Database mode is response-bounded and reads both directions of neuron and
    engram edges from canonical state. Engram keys remain negative so callers
    observe the same representation as the local adjacency cache.
    """
    assert isinstance(node_ids, set), "node_ids must be a set"
    if not node_ids:
        return {}
    if settings.cache_coherence_mode == "process-local":
        await ensure_adjacency_loaded(db)
        return get_cached_neighbors(node_ids, min_weight)

    from sqlalchemy import text

    result = await db.execute(
        text("""
            SELECT root_id, neighbor_id, weight, edge_type
            FROM (
                SELECT source_id AS root_id, target_id AS neighbor_id,
                       weight, COALESCE(edge_type, 'pyramidal') AS edge_type
                FROM neuron_edges
                WHERE source_id = ANY(:node_ids)
                UNION ALL
                SELECT target_id AS root_id, source_id AS neighbor_id,
                       weight, COALESCE(edge_type, 'pyramidal') AS edge_type
                FROM neuron_edges
                WHERE target_id = ANY(:node_ids)
                UNION ALL
                SELECT neuron_id AS root_id, -engram_id AS neighbor_id,
                       weight, COALESCE(edge_type, 'regulatory') AS edge_type
                FROM engram_edges
                WHERE neuron_id = ANY(:node_ids)
                UNION ALL
                SELECT -engram_id AS root_id, neuron_id AS neighbor_id,
                       weight, COALESCE(edge_type, 'regulatory') AS edge_type
                FROM engram_edges
                WHERE -engram_id = ANY(:node_ids)
            ) AS graph_edges
            WHERE weight >= :min_weight
            ORDER BY weight DESC, root_id, neighbor_id
            LIMIT :edge_limit
        """),
        {
            "node_ids": list(node_ids),
            "min_weight": float(min_weight),
            "edge_limit": settings.spread_edges_max_per_hop,
        },
    )
    rows = result.all()
    if traversal_metrics is not None:
        edge_limit = settings.spread_edges_max_per_hop
        traversal_metrics["edge_rows"] = (
            traversal_metrics.get("edge_rows", 0) + len(rows)
        )
        traversal_metrics["max_edge_rows_per_hop"] = max(
            traversal_metrics.get("max_edge_rows_per_hop", 0), len(rows),
        )
        if len(rows) >= edge_limit:
            traversal_metrics["edge_limit_saturated_hops"] = (
                traversal_metrics.get("edge_limit_saturated_hops", 0) + 1
            )
    adjacency: dict[int, list[tuple[int, float, str]]] = {}
    for root_id, neighbor_id, weight, edge_type in rows:
        adjacency.setdefault(int(root_id), []).append(
            (int(neighbor_id), float(weight), edge_type)
        )
    return adjacency


def get_adjacency_csr() -> dict | None:
    """Return the cached CSR-by-source view for vectorized spread (None if unloaded)."""
    return _cache.get_csr()


def is_adjacency_loaded() -> bool:
    """Check if the adjacency cache has been loaded."""
    return _cache.is_loaded


# Derived hop caps above this are clamped — past ~8 hops, multiplicative
# decay has extinguished any signal regardless of graph shape.
_HOP_CAP_MAX = 8


def hop_cap_formula(n_nodes: int, n_edges: int) -> int:
    """Characteristic-path-length hop cap: ceil(log N / log avg-degree).

    Small-world result: expected hops to reach any node from any node scales
    with log(N)/log(k) for average degree k. Dense graphs (high k) need few
    hops however large N grows; sparse graphs need more. Degenerate
    near-chain graphs (avg degree < 2, log <= ~0.7 would explode the ratio)
    clamp to the max.
    """
    assert n_nodes >= 2, f"hop_cap_formula needs >= 2 nodes, got {n_nodes}"
    assert n_edges >= 1, f"hop_cap_formula needs >= 1 edge, got {n_edges}"
    avg_degree = n_edges / n_nodes
    if avg_degree < 2.0:
        return _HOP_CAP_MAX
    cap = math.ceil(math.log(n_nodes) / math.log(avg_degree))
    return max(1, min(cap, _HOP_CAP_MAX))


def derived_hop_cap() -> int | None:
    """Graph-derived spread hop cap, or None when the cache isn't loaded.

    Reads node/edge counts from the cached CSR view (O(1); the CSR build is
    itself cached and invalidated on edge mutations), so the cap re-derives
    automatically as the graph grows or densifies.
    """
    csr = get_adjacency_csr()
    if not csr or int(csr["id_list"].size) < 2 or int(csr["indices"].size) < 1:
        return None
    return hop_cap_formula(int(csr["id_list"].size), int(csr["indices"].size))
