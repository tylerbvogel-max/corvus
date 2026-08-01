"""Semantic pre-filter: in-memory embedding cache for fast cosine candidate selection.

Biological analogue: cortical topographic maps. Instead of routing queries through
the org-chart hierarchy (department → role → task), we compute semantic proximity
in embedding space. Neurons close in meaning to the query get selected regardless
of their department assignment.

Cache architecture:
- On first query, loads all ~2K neuron embeddings (~3MB as float32 matrix)
- Subsequent queries do a single matrix multiply (~1ms) to rank all neurons
- In database coherence mode, every worker checks a shared O(1) revision before
  use and reloads only after an embedding/active-set write.

Feature-flagged via settings.semantic_prefilter_enabled.
"""

import asyncio
import json
import threading
import numpy as np

from app.config import settings


class _EmbeddingCache:
    """Thread-safe in-memory cache of neuron + engram embeddings as a numpy matrix."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entity_ids: list[int] = []
        self._entity_types: list[str] = []  # "neuron" or "engram" per entry
        self._matrix: np.ndarray | None = None  # shape (N, 384), float32
        self._loaded = False
        self._revision: int | None = None

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def revision(self) -> int | None:
        return self._revision

    # Legacy alias for backward compatibility
    @property
    def _neuron_ids(self) -> list[int]:
        return self._entity_ids

    def load(
        self,
        entity_ids: list[int],
        entity_types: list[str],
        embeddings: list[list[float]],
        revision: int | None = None,
    ):
        """Load embedding matrix from DB results. Called once at startup or on invalidate."""
        assert len(entity_ids) == len(entity_types) == len(embeddings)
        with self._lock:
            self._entity_ids = entity_ids
            self._entity_types = entity_types
            self._matrix = np.array(embeddings, dtype=np.float32) if embeddings else None
            self._revision = revision
            self._loaded = True

    def invalidate(self):
        """Force reload on next query (e.g. after embedding new neurons/engrams)."""
        with self._lock:
            self._loaded = False
            self._matrix = None
            self._entity_ids = []
            self._entity_types = []
            self._revision = None

    def update_neurons(
        self, neuron_ids: list[int], embeddings: list[list[float]],
        entity_type: str = "neuron",
    ) -> None:
        """Incrementally update or append entities without full cache rebuild."""
        assert len(neuron_ids) == len(embeddings), "ids and embeddings must match"
        with self._lock:
            if not self._loaded or self._matrix is None:
                return
            # Build lookup keyed on (type, id) to handle neuron/engram ID collision
            key_to_idx = {
                (self._entity_types[i], self._entity_ids[i]): i
                for i in range(len(self._entity_ids))
            }
            new_ids: list[int] = []
            new_types: list[str] = []
            new_vecs: list[list[float]] = []
            for nid, emb in zip(neuron_ids, embeddings):
                key = (entity_type, nid)
                if key in key_to_idx:
                    self._matrix[key_to_idx[key]] = np.array(emb, dtype=np.float32)
                else:
                    new_ids.append(nid)
                    new_types.append(entity_type)
                    new_vecs.append(emb)
            if new_ids:
                new_matrix = np.array(new_vecs, dtype=np.float32)
                self._matrix = np.vstack([self._matrix, new_matrix])
                self._entity_ids.extend(new_ids)
                self._entity_types.extend(new_types)

    def remove_neurons(self, neuron_ids: list[int]) -> None:
        """Remove entities from cache via mask rebuild."""
        with self._lock:
            if not self._loaded or self._matrix is None:
                return
            remove_set = set(neuron_ids)
            keep_mask = [i for i, nid in enumerate(self._entity_ids) if nid not in remove_set]
            if len(keep_mask) == len(self._entity_ids):
                return
            self._entity_ids = [self._entity_ids[i] for i in keep_mask]
            self._entity_types = [self._entity_types[i] for i in keep_mask]
            self._matrix = self._matrix[keep_mask] if keep_mask else None
            if self._matrix is None:
                self._loaded = False

    def query(
        self, query_vec: list[float], top_n: int, min_similarity: float,
    ) -> list[tuple[int, str, float]]:
        """Return top-N (entity_id, entity_type, similarity) tuples above min_similarity.

        Uses matrix dot product for speed (~1ms for 2K+ entities x 384 dims).
        """
        with self._lock:
            if not self._loaded or self._matrix is None or len(self._entity_ids) == 0:
                return []

            q = np.array(query_vec, dtype=np.float32)
            scores = self._matrix @ q  # (N,) cosine similarities

            mask = scores >= min_similarity
            valid_indices = np.where(mask)[0]

            if len(valid_indices) == 0:
                return []

            valid_scores = scores[valid_indices]
            if len(valid_indices) <= top_n:
                top_local = np.argsort(-valid_scores)
            else:
                top_local = np.argpartition(-valid_scores, top_n)[:top_n]
                top_local = top_local[np.argsort(-valid_scores[top_local])]

            results = []
            for idx in top_local:
                global_idx = valid_indices[idx]
                results.append((
                    self._entity_ids[global_idx],
                    self._entity_types[global_idx],
                    float(scores[global_idx]),
                ))

            return results


# Module-level singleton
_cache = _EmbeddingCache()
_load_lock = asyncio.Lock()


async def _shared_semantic_revision(db) -> int:
    """Return the PostgreSQL-owned embedding-set revision.

    The row is maintained by statement-level triggers on neuron/engram
    embedding and active-set writes. Missing schema is a hard failure in
    database coherence mode: silently serving a stale matrix would violate the
    mode's contract.
    """
    from sqlalchemy import text

    result = await db.execute(text(
        "SELECT revision FROM cache_versions WHERE key = 'semantic_embeddings'"
    ))
    revision = result.scalar_one_or_none()
    if revision is None:
        raise RuntimeError("semantic_embeddings cache revision is not initialized")
    return int(revision)


async def _ensure_cache_loaded_unlocked(db):
    """Load the embedding cache from DB if not already loaded (neurons + engrams)."""
    shared_revision: int | None = None
    if settings.cache_coherence_mode == "database":
        shared_revision = await _shared_semantic_revision(db)
        if _cache.is_loaded and _cache.revision == shared_revision:
            return
    elif _cache.is_loaded:
        return

    from sqlalchemy import text

    entity_ids: list[int] = []
    entity_types: list[str] = []
    embeddings: list[list[float]] = []

    # Load neuron embeddings
    neuron_rows = (await db.execute(
        text("SELECT id, embedding FROM neurons WHERE is_active = true AND embedding IS NOT NULL AND embedding != ''")
    )).all()
    for nid, emb_json in neuron_rows:
        try:
            vec = json.loads(emb_json)
            entity_ids.append(nid)
            entity_types.append("neuron")
            embeddings.append(vec)
        except (json.JSONDecodeError, TypeError):
            continue

    neuron_count = len(entity_ids)

    # Load engram embeddings
    try:
        engram_rows = (await db.execute(
            text("SELECT id, embedding FROM engrams WHERE is_active = true AND embedding IS NOT NULL AND embedding != ''")
        )).all()
        for eid, emb_json in engram_rows:
            try:
                vec = json.loads(emb_json)
                entity_ids.append(eid)
                entity_types.append("engram")
                embeddings.append(vec)
            except (json.JSONDecodeError, TypeError):
                continue
    except Exception:
        pass  # engrams table may not exist yet in older databases

    engram_count = len(entity_ids) - neuron_count

    if entity_ids:
        _cache.load(
            entity_ids, entity_types, embeddings, revision=shared_revision,
        )
        size_kb = _cache._matrix.nbytes / 1024 if _cache._matrix is not None else 0
        revision_detail = (
            f", revision {shared_revision}" if shared_revision is not None else ""
        )
        print(
            f"Semantic cache loaded: {neuron_count} neurons + "
            f"{engram_count} engrams ({size_kb:.0f} KB{revision_detail})"
        )
    else:
        # An empty active set is still a valid, revision-bearing replica. Mark
        # it loaded so every query does not repeat the full table reads.
        _cache.load([], [], [], revision=shared_revision)
        print("Semantic cache: no embeddings found")


async def ensure_cache_loaded(db):
    """Ensure one coherent matrix, collapsing concurrent stale-cache reloads."""
    if settings.cache_coherence_mode == "database":
        shared_revision = await _shared_semantic_revision(db)
        if _cache.is_loaded and _cache.revision == shared_revision:
            return
    elif _cache.is_loaded:
        return

    # Only stale/missing contenders serialize. The winner reloads; waiters
    # recheck inside the helper and return without duplicating table reads.
    async with _load_lock:
        await _ensure_cache_loaded_unlocked(db)


def invalidate_cache():
    """Call after embedding new neurons to force reload."""
    _cache.invalidate()


async def update_cache_incremental(
    db, entity_ids: list[int], entity_type: str = "neuron",
) -> None:
    """Incrementally update the cache for specific entities instead of full reload."""
    assert len(entity_ids) > 0, "entity_ids must not be empty"
    if not _cache.is_loaded:
        return

    from sqlalchemy import text
    table = "neurons" if entity_type == "neuron" else "engrams"
    placeholders = ", ".join(str(int(eid)) for eid in entity_ids)
    result = await db.execute(
        text(f"SELECT id, embedding FROM {table} WHERE id IN ({placeholders}) AND embedding IS NOT NULL")
    )
    rows = result.all()
    if not rows:
        return

    loaded_ids: list[int] = []
    loaded_embeddings: list[list[float]] = []
    for eid, emb_json in rows:
        try:
            vec = json.loads(emb_json)
            loaded_ids.append(eid)
            loaded_embeddings.append(vec)
        except (json.JSONDecodeError, TypeError):
            continue

    if loaded_ids:
        _cache.update_neurons(loaded_ids, loaded_embeddings, entity_type=entity_type)
        print(f"Semantic cache incremental update: {len(loaded_ids)} {entity_type}s")


async def semantic_prefilter(
    db,
    query_embedding: list[float],
    top_n_override: int | None = None,
) -> list[tuple[int, str, float]]:
    """Return top-N candidates ranked by semantic similarity.

    Returns list of (entity_id, entity_type, similarity_score) tuples, sorted descending.
    entity_type is "neuron" or "engram".
    """
    await ensure_cache_loaded(db)

    top_n = top_n_override if top_n_override is not None else settings.semantic_prefilter_top_n

    return _cache.query(
        query_embedding,
        top_n=top_n,
        min_similarity=settings.semantic_prefilter_min_similarity,
    )
