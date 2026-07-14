"""Ingest-first emergent seeding — structure discovery for blank-canvas orgs.

A new tenant does NOT hand-author an org taxonomy. Instead:
  1. Ingest their corpus (document ingest mints flat artifact neurons +
     embeddings — existing two-phase pipeline).
  2. bootstrap_knn_edges / bootstrap_cooccurrence_edges give spread
     activation a prior wiring so the graph is not dead on day one.
  3. discover_regions runs Leiden over the bootstrapped edges, labels each
     cluster with one bounded LLM call, and stages HUMAN-APPROVED proposals
     that create a structural region root + assign the region tag to members.
  4. retype_edges_by_region re-derives stellate/pyramidal after region
     assignment so intra-region edges get local spread decay.

All structure mutations go through the proposal queue + Action Bus — the
discovered taxonomy is a proposal, never an auto-commit.
"""

import json
import logging

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutopilotProposal, ProposalItem

logger = logging.getLogger(__name__)

# Bootstrap edge weights: strong enough to carry spread activation
# (>= spread_pyramidal_min_weight 0.20), capped below organic ceiling.
_KNN_WEIGHT_BASE = 0.20
_KNN_WEIGHT_SCALE = 0.30
_KNN_WEIGHT_CAP = 0.45
_COOCCURRENCE_WEIGHT = 0.30
_MAX_CLUSTERS_PER_DISCOVERY = 20
_MAX_LABEL_SAMPLE = 10


async def _load_embedded_neurons(db: AsyncSession) -> tuple[list[int], np.ndarray]:
    """Load (ids, embedding matrix) for active neurons with embeddings."""
    rows = (await db.execute(text(
        "SELECT id, embedding FROM neurons "
        "WHERE is_active = true AND embedding IS NOT NULL AND embedding != ''"
    ))).all()
    ids: list[int] = []
    vecs: list[list[float]] = []
    for nid, emb_json in rows:
        try:
            vecs.append(json.loads(emb_json))
            ids.append(nid)
        except (json.JSONDecodeError, TypeError):
            continue
    matrix = np.array(vecs, dtype=np.float32) if vecs else np.zeros((0, 384), dtype=np.float32)
    return ids, matrix


def _knn_pairs(
    ids: list[int], matrix: np.ndarray, k: int, min_similarity: float,
) -> dict[tuple[int, int], float]:
    """Top-k embedding neighbors per neuron above min_similarity.

    Returns {(min_id, max_id): max_similarity} — bidirectional dedup.
    """
    n = len(ids)
    if n < 2:
        return {}
    sims = matrix @ matrix.T
    np.fill_diagonal(sims, -1.0)
    pairs: dict[tuple[int, int], float] = {}
    top_k = min(k, n - 1)
    for i in range(n):
        neighbor_idx = np.argpartition(-sims[i], top_k - 1)[:top_k]
        for j in neighbor_idx:
            sim = float(sims[i][j])
            if sim < min_similarity:
                continue
            key = (min(ids[i], ids[int(j)]), max(ids[i], ids[int(j)]))
            if sim > pairs.get(key, 0.0):
                pairs[key] = sim
    return pairs


async def bootstrap_knn_edges(
    db: AsyncSession,
    k: int = 6,
    min_similarity: float = 0.35,
    dry_run: bool = False,
) -> dict:
    """Embedding kNN -> bootstrap edges so day-one spread activation works."""
    assert k >= 1, f"k must be >= 1, got {k}"
    assert 0.0 < min_similarity < 1.0, f"min_similarity out of range: {min_similarity}"

    ids, matrix = await _load_embedded_neurons(db)
    pairs = _knn_pairs(ids, matrix, k, min_similarity)

    edges_to_create = {
        pair: {
            "weight": min(_KNN_WEIGHT_CAP, _KNN_WEIGHT_BASE + _KNN_WEIGHT_SCALE * sim),
            "context": "knn_bootstrap",
        }
        for pair, sim in pairs.items()
    }
    if not dry_run and edges_to_create:
        from app.services.bootstrap_service import write_planned_edges
        await write_planned_edges(db, edges_to_create)
        from app.services.adjacency_cache import invalidate_adjacency_cache
        from app.services.neuron_index import invalidate_index
        invalidate_adjacency_cache()
        invalidate_index()

    return {
        "neurons_embedded": len(ids),
        "edges_planned": len(edges_to_create),
        "dry_run": dry_run,
    }


async def wire_neuron_knn_edges(
    db: AsyncSession, neuron_id: int, k: int = 8, min_similarity: float = 0.30,
) -> int:
    """Genesis wiring: connect ONE new neuron to its top-k embedding
    neighbors at creation so it joins spread activation at birth instead
    of waiting for a manual bootstrap pass. Liberal by design (synaptic
    exuberance); decay janitors prune what never conducts. Returns the
    number of edges written."""
    assert k >= 1, f"k must be >= 1, got {k}"
    assert 0.0 < min_similarity < 1.0, f"min_similarity out of range: {min_similarity}"
    ids, matrix = await _load_embedded_neurons(db)
    if neuron_id not in ids or len(ids) < 2:
        return 0
    i = ids.index(neuron_id)
    sims = matrix @ matrix[i]
    sims[i] = -1.0
    top = np.argsort(-sims)[: min(k, len(ids) - 1)]
    edges = {}
    for j in top:
        sim = float(sims[int(j)])
        if sim < min_similarity:
            break  # sims sorted descending — nothing further qualifies
        key = (min(neuron_id, ids[int(j)]), max(neuron_id, ids[int(j)]))
        edges[key] = {
            "weight": min(_KNN_WEIGHT_CAP, _KNN_WEIGHT_BASE + _KNN_WEIGHT_SCALE * sim),
            "context": "genesis_wire",
        }
    if edges:
        from app.services.bootstrap_service import write_planned_edges
        await write_planned_edges(db, edges)
        from app.services.adjacency_cache import invalidate_adjacency_cache
        invalidate_adjacency_cache()
    return len(edges)


async def bootstrap_cooccurrence_edges(
    db: AsyncSession,
    window: int = 3,
    max_pairs_per_doc: int = 200,
    dry_run: bool = False,
) -> dict:
    """Same-source-document co-occurrence -> bootstrap edges.

    Neurons linked to one SourceDocument get chained with a sliding window
    (bounded, deterministic) instead of a full clique.
    """
    assert window >= 1, f"window must be >= 1, got {window}"

    rows = (await db.execute(text(
        "SELECT nsl.source_document_id, nsl.neuron_id FROM neuron_source_links nsl "
        "JOIN neurons n ON n.id = nsl.neuron_id AND n.is_active = true "
        "ORDER BY nsl.source_document_id, nsl.neuron_id"
    ))).all()

    by_doc: dict[int, list[int]] = {}
    for doc_id, neuron_id in rows:
        by_doc.setdefault(doc_id, []).append(neuron_id)

    edges_to_create: dict[tuple[int, int], dict] = {}
    for doc_id, members in by_doc.items():
        doc_pairs = 0
        for i, a in enumerate(members):
            for b in members[i + 1:i + 1 + window]:
                if doc_pairs >= max_pairs_per_doc:
                    break
                key = (min(a, b), max(a, b))
                edges_to_create.setdefault(
                    key,
                    {"weight": _COOCCURRENCE_WEIGHT, "context": f"co_source_doc:{doc_id}"},
                )
                doc_pairs += 1
            if doc_pairs >= max_pairs_per_doc:
                break

    if not dry_run and edges_to_create:
        from app.services.bootstrap_service import write_planned_edges
        await write_planned_edges(db, edges_to_create)
        from app.services.adjacency_cache import invalidate_adjacency_cache
        from app.services.neuron_index import invalidate_index
        invalidate_adjacency_cache()
        invalidate_index()

    return {
        "source_documents": len(by_doc),
        "edges_planned": len(edges_to_create),
        "dry_run": dry_run,
    }


def _build_cluster_label_prompt(clusters: list[dict]) -> tuple[str, str]:
    """System + user prompt for the one bounded cluster-labeling LLM call."""
    system = (
        "You name knowledge clusters for an organizational memory system. "
        "Each cluster is a group of related knowledge items discovered by "
        "community detection. Give each cluster a short (1-4 word) region "
        "name a practitioner would recognize, like a department or practice "
        "area name. Respond with ONLY a JSON array: "
        '[{"cluster_id": <int>, "region_label": "<name>", "rationale": "<one sentence>"}]'
    )
    digest = [
        {
            "cluster_id": c["cluster_id"],
            "size": len(c["neuron_ids"]),
            "keywords": c["suggested_label"],
            "sample_items": c["sample_labels"][:_MAX_LABEL_SAMPLE],
        }
        for c in clusters
    ]
    return system, json.dumps(digest, indent=1)


def _parse_label_response(raw_text: str, valid_ids: set[int]) -> dict[int, dict]:
    """Parse the labeling response defensively (LLM output is untrusted input)."""
    stripped = raw_text.strip()
    if "```" in stripped:
        parts = stripped.split("```")
        stripped = parts[1] if len(parts) > 1 else stripped
        if stripped.startswith("json"):
            stripped = stripped[4:]
    try:
        parsed = json.loads(stripped.strip())
    except (json.JSONDecodeError, ValueError):
        logger.warning("Cluster label response unparseable: %s", raw_text[:200])
        return {}
    labels: dict[int, dict] = {}
    if not isinstance(parsed, list):
        return {}
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("cluster_id")
        label = str(entry.get("region_label", "")).strip()[:100]
        if cid in valid_ids and label:
            labels[cid] = {
                "region_label": label,
                "rationale": str(entry.get("rationale", ""))[:500],
            }
    return labels


async def _sample_member_labels(db: AsyncSession, neuron_ids: list[int]) -> list[str]:
    """Fetch a bounded sample of member labels for the labeling prompt."""
    sample_ids = neuron_ids[:_MAX_LABEL_SAMPLE]
    rows = (await db.execute(
        text("SELECT label FROM neurons WHERE id = ANY(:ids)"),
        {"ids": sample_ids},
    )).all()
    return [r[0] for r in rows]


def _region_proposal_items(cluster: dict, region_label: str) -> list[ProposalItem]:
    """Build proposal items: create region root + tag each member."""
    root_spec = {
        "label": region_label,
        "layer": 0,
        "node_type": "department",
        "abstraction_type": "structural",
        "department": region_label,
        "summary": f"Emergent region discovered from corpus clustering "
                   f"({len(cluster['neuron_ids'])} members)",
        "source_origin": "seed_discovery",
    }
    items = [ProposalItem(
        action="create",
        neuron_spec_json=json.dumps(root_spec),
        reason=f"Region root for emergent cluster {cluster['cluster_id']}",
    )]
    for nid in cluster["neuron_ids"]:
        items.append(ProposalItem(
            action="update",
            target_neuron_id=nid,
            field="department",
            old_value="",
            new_value=region_label,
            reason=f"Assign region '{region_label}' (emergent cluster member)",
        ))
    return items


async def _stage_region_proposal(
    db: AsyncSession, cluster: dict, label_info: dict, model: str,
) -> AutopilotProposal:
    """Stage one human-approval proposal for a discovered region."""
    region_label = label_info["region_label"]
    proposal = AutopilotProposal(
        state="proposed",
        gap_source="seed_structure",
        gap_description=(
            f"Emergent region '{region_label}': {len(cluster['neuron_ids'])} neurons, "
            f"keywords: {cluster['suggested_label']}"
        ),
        gap_evidence_json=json.dumps({
            "cluster_id": cluster["cluster_id"],
            "neuron_ids": cluster["neuron_ids"],
            "avg_internal_weight": cluster["avg_internal_weight"],
        }),
        priority_score=0.5,
        llm_reasoning=label_info.get("rationale", ""),
        llm_model=model,
    )
    db.add(proposal)
    await db.flush()
    for item in _region_proposal_items(cluster, region_label):
        item.proposal_id = proposal.id
        db.add(item)
    await db.flush()
    return proposal


async def discover_regions(
    db: AsyncSession,
    resolution: float = 1.0,
    min_size: int = 3,
    only_unregioned: bool = True,
    model: str = "opus",
) -> dict:
    """Leiden over bootstrapped edges -> LLM labels -> human-approval proposals.

    The discovered taxonomy IS the seed: each cluster becomes a proposal that
    (a) creates a structural region root and (b) tags members with the region.
    Nothing mutates until a human approves in the standard proposal queue.
    """
    from app.services.clustering import find_clusters

    clusters = await find_clusters(
        db, min_weight=0.2, min_size=min_size, min_departments=0,
        resolution=resolution,
    )
    if only_unregioned:
        clusters = [c for c in clusters if not c["departments"]]
    clusters = clusters[:_MAX_CLUSTERS_PER_DISCOVERY]
    if not clusters:
        return {"clusters_found": 0, "proposals_created": 0}

    for cluster in clusters:
        cluster["sample_labels"] = await _sample_member_labels(db, cluster["neuron_ids"])

    from app.services.llm_provider import llm_chat
    system, user = _build_cluster_label_prompt(clusters)
    response = await llm_chat(system, user, max_tokens=2000, model=model)
    labels = _parse_label_response(
        response["text"], {c["cluster_id"] for c in clusters},
    )

    proposals_created = 0
    for cluster in clusters:
        label_info = labels.get(cluster["cluster_id"])
        if not label_info:
            continue
        await _stage_region_proposal(db, cluster, label_info, model)
        proposals_created += 1

    return {
        "clusters_found": len(clusters),
        "proposals_created": proposals_created,
        "labels": {cid: info["region_label"] for cid, info in labels.items()},
    }


async def retype_edges_by_region(db: AsyncSession) -> int:
    """Re-derive stellate/pyramidal from region membership after assignment.

    Touches only stellate/pyramidal rows whose type actually changes;
    typed edges like 'instantiates'/'regulatory' are left alone.
    """
    result = await db.execute(text(
        "UPDATE neuron_edges e SET edge_type = derived.new_type, last_adjusted = now() "
        "FROM ("
        "  SELECT e2.source_id, e2.target_id, "
        "         CASE WHEN a.department IS NOT NULL AND a.department = b.department "
        "              THEN 'stellate' ELSE 'pyramidal' END AS new_type "
        "  FROM neuron_edges e2 "
        "  JOIN neurons a ON a.id = e2.source_id "
        "  JOIN neurons b ON b.id = e2.target_id "
        "  WHERE e2.edge_type IN ('stellate', 'pyramidal') "
        ") derived "
        "WHERE e.source_id = derived.source_id AND e.target_id = derived.target_id "
        "  AND e.edge_type IS DISTINCT FROM derived.new_type"
    ))
    retyped = result.rowcount or 0
    if retyped:
        from app.services.adjacency_cache import invalidate_adjacency_cache
        from app.services.neuron_index import invalidate_index
        invalidate_adjacency_cache()
        invalidate_index()
    return retyped
