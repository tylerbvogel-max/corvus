"""Bulk pairwise embedding similarity for integrity scans.

Loads neuron embeddings from the database, parses JSON vectors into a numpy
matrix, and computes full pairwise cosine similarity via matrix multiply.
L2-normalized embeddings → dot product = cosine similarity.
"""

import json
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Neuron


@dataclass
class NeuronEmbedding:
    """Neuron ID paired with its parsed embedding vector."""

    neuron_id: int
    department: str | None
    layer: int
    label: str


@dataclass
class SimilarPair:
    """A pair of neurons with their cosine similarity score."""

    neuron_a_id: int
    neuron_b_id: int
    similarity: float
    a_label: str = ""
    b_label: str = ""
    a_department: str | None = None
    b_department: str | None = None
    a_layer: int = 0
    b_layer: int = 0


async def load_neuron_embeddings(
    db: AsyncSession,
    scope: str = "global",
    max_neurons: int = 10_000,
) -> tuple[list[NeuronEmbedding], np.ndarray]:
    """Load active neurons with embeddings, return metadata + numpy matrix.

    Returns (metadata_list, embedding_matrix) where embedding_matrix is (N, 384).
    Only neurons with non-null embeddings are included.
    """
    stmt = (
        select(Neuron.id, Neuron.department, Neuron.layer, Neuron.label, Neuron.embedding)
        .where(Neuron.is_active.is_(True))
        .where(Neuron.embedding.isnot(None))
    )
    # Apply scope filter
    if scope.startswith("department:"):
        dept = scope.split(":", 1)[1]
        stmt = stmt.where(Neuron.department == dept)
    elif scope.startswith("layer:"):
        layer_num = int(scope.split(":", 1)[1])
        stmt = stmt.where(Neuron.layer == layer_num)
    elif scope.startswith("node_type:"):
        types = [t.strip() for t in scope.split(":", 1)[1].split(",") if t.strip()]
        assert len(types) > 0, "node_type scope requires at least one type"
        stmt = stmt.where(Neuron.node_type.in_(types))
    # else: global — no additional filter

    stmt = stmt.limit(max_neurons)
    result = await db.execute(stmt)
    rows = result.all()

    assert len(rows) >= 0, "Query must return non-negative rows"

    metadata: list[NeuronEmbedding] = []
    vectors: list[list[float]] = []

    for row in rows:
        nid, dept, layer, label, emb_json = row
        assert emb_json is not None, "Filtered query should exclude null embeddings"
        vec = json.loads(emb_json)
        metadata.append(NeuronEmbedding(neuron_id=nid, department=dept, layer=layer, label=label))
        vectors.append(vec)

    if not vectors:
        return metadata, np.empty((0, 384), dtype=np.float32)

    matrix = np.array(vectors, dtype=np.float32)
    assert matrix.shape[1] == 384, f"Expected 384-dim embeddings, got {matrix.shape[1]}"
    return metadata, matrix


def compute_pairwise_similarity(matrix: np.ndarray) -> np.ndarray:
    """Compute full pairwise cosine similarity matrix.

    Input: (N, 384) L2-normalized embeddings.
    Output: (N, N) symmetric similarity matrix with 1.0 on diagonal.
    """
    assert matrix.ndim == 2, "Input must be 2D matrix"
    if matrix.shape[0] == 0:
        return np.empty((0, 0), dtype=np.float32)
    # L2-normalized → dot product = cosine similarity
    sim_matrix = matrix @ matrix.T
    return sim_matrix


def extract_pairs_above_threshold(
    sim_matrix: np.ndarray,
    metadata: list[NeuronEmbedding],
    threshold: float,
    max_pairs: int = 100,
) -> list[SimilarPair]:
    """Extract neuron pairs with similarity above threshold.

    Only returns upper-triangle pairs (no duplicates, no self-pairs).
    Sorted by similarity descending.
    """
    assert threshold > 0.0, "Threshold must be positive"
    assert max_pairs > 0, "max_pairs must be positive"

    n = sim_matrix.shape[0]
    if n < 2:
        return []

    # Get upper triangle indices (excluding diagonal)
    row_idx, col_idx = np.triu_indices(n, k=1)
    sims = sim_matrix[row_idx, col_idx]

    # Filter above threshold
    mask = sims >= threshold
    filtered_rows = row_idx[mask]
    filtered_cols = col_idx[mask]
    filtered_sims = sims[mask]

    # Sort descending
    sort_idx = np.argsort(-filtered_sims)[:max_pairs]

    pairs: list[SimilarPair] = []
    for idx in sort_idx:
        i, j = int(filtered_rows[idx]), int(filtered_cols[idx])
        a, b = metadata[i], metadata[j]
        pairs.append(SimilarPair(
            neuron_a_id=a.neuron_id, neuron_b_id=b.neuron_id,
            similarity=float(filtered_sims[idx]),
            a_label=a.label, b_label=b.label,
            a_department=a.department, b_department=b.department,
            a_layer=a.layer, b_layer=b.layer,
        ))

    return pairs


def extract_pairs_in_range(
    sim_matrix: np.ndarray,
    metadata: list[NeuronEmbedding],
    min_sim: float,
    max_sim: float,
    max_pairs: int = 200,
) -> list[SimilarPair]:
    """Extract neuron pairs with similarity within [min_sim, max_sim].

    Used by conflict monitoring to find candidates that are similar enough
    to potentially contradict but not so similar as to be duplicates.
    """
    assert 0.0 <= min_sim < max_sim <= 1.0, "Invalid similarity range"
    assert max_pairs > 0, "max_pairs must be positive"

    n = sim_matrix.shape[0]
    if n < 2:
        return []

    row_idx, col_idx = np.triu_indices(n, k=1)
    sims = sim_matrix[row_idx, col_idx]

    mask = (sims >= min_sim) & (sims <= max_sim)
    filtered_rows = row_idx[mask]
    filtered_cols = col_idx[mask]
    filtered_sims = sims[mask]

    # Sort by similarity descending (most similar first)
    sort_idx = np.argsort(-filtered_sims)[:max_pairs]

    pairs: list[SimilarPair] = []
    for idx in sort_idx:
        i, j = int(filtered_rows[idx]), int(filtered_cols[idx])
        a, b = metadata[i], metadata[j]
        pairs.append(SimilarPair(
            neuron_a_id=a.neuron_id, neuron_b_id=b.neuron_id,
            similarity=float(filtered_sims[idx]),
            a_label=a.label, b_label=b.label,
            a_department=a.department, b_department=b.department,
            a_layer=a.layer, b_layer=b.layer,
        ))

    return pairs
