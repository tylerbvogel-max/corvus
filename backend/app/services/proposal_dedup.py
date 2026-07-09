"""Semantic near-duplicate clustering of proposals (gov-polish-cluster).

Gap detectors re-surface the same gap across runs, so the proposal queue
accumulates near-copies that reviewers must triage one by one. This service
embeds each proposal's gap_description (local MiniLM, $0) and greedily
clusters by cosine similarity so the UI can present one cluster instead of
N copies. Advisory only: clustering never mutates proposals — bulk actions
still go through the reviewed per-proposal action-bus flow.

Failure behavior: proposals without a gap_description cannot be compared
and are simply left unclustered (never guessed into a cluster); an empty
candidate set returns zero clusters rather than erroring.
"""

import asyncio

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutopilotProposal

# Bounded work per request (JPL-2): newest proposals win when over the cap.
MAX_PROPOSALS = 500


def greedy_clusters(ids: list[int], matrix: np.ndarray, threshold: float) -> list[list[int]]:
    """Greedy single-pass clustering over L2-normalized embeddings.

    Each unassigned proposal seeds a cluster and absorbs every unassigned
    proposal whose cosine similarity meets the threshold. Deliberately simple
    (no transitive chaining beyond the seed) so a cluster is always "everything
    similar to THIS proposal" — easy for a reviewer to sanity-check.
    """
    assert len(ids) == matrix.shape[0], "ids and embedding rows must align"
    assert 0.0 < threshold <= 1.0, "threshold must be a cosine in (0, 1]"
    sims = matrix @ matrix.T
    assigned: set[int] = set()
    clusters: list[list[int]] = []
    for i in range(len(ids)):
        if i in assigned:
            continue
        members = [i] + [
            j for j in range(i + 1, len(ids))
            if j not in assigned and sims[i, j] >= threshold
        ]
        if len(members) > 1:
            assigned.update(members)
            clusters.append([ids[m] for m in members])
    return clusters


async def compute_dedup_clusters(
    db: AsyncSession, state: str = "proposed", threshold: float = 0.88,
) -> dict:
    """Cluster proposals in `state` by gap_description similarity.

    Returns {"clusters": [{proposal_ids, size, representative}], "scanned",
    "threshold"} with clusters ordered largest first. Embedding runs in a
    thread (CPU-bound BERT encode) so the event loop stays responsive.
    """
    assert state, "state must be non-empty"
    rows = (await db.execute(
        select(AutopilotProposal.id, AutopilotProposal.gap_description)
        .where(
            AutopilotProposal.state == state,
            AutopilotProposal.gap_description.isnot(None),
            AutopilotProposal.gap_description != "",
        )
        .order_by(AutopilotProposal.id.desc())
        .limit(MAX_PROPOSALS)
    )).all()
    if len(rows) < 2:
        return {"clusters": [], "scanned": len(rows), "threshold": threshold}

    ids = [r[0] for r in rows]
    texts = [r[1] for r in rows]
    from app.services.embedding_service import embed_batch
    vectors = await asyncio.to_thread(embed_batch, texts)
    matrix = np.asarray(vectors, dtype=np.float32)

    desc_by_id = dict(rows)
    clusters = [
        {
            "proposal_ids": member_ids,
            "size": len(member_ids),
            # Seed proposal's text names the cluster in the UI
            "representative": (desc_by_id[member_ids[0]] or "")[:160],
        }
        for member_ids in greedy_clusters(ids, matrix, threshold)
    ]
    clusters.sort(key=lambda c: c["size"], reverse=True)
    return {"clusters": clusters, "scanned": len(rows), "threshold": threshold}
