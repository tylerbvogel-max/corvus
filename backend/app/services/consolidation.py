"""Periodic decay/pruning of neuron activation history.

Run periodically to:
1. Decay avg_utility on neurons that haven't fired recently
2. Prune old firing records beyond retention window
3. Deactivate evidence-tier (artifact) neurons with consistently low utility
4. Refresh denormalized degree centrality (cold-start prior input)

Forgetting is the soft write gate: auto-committed observational writes that
never get reinforced are reclaimed here instead of being human-gated upfront.
Tunables live in settings (consolidation_*) so per-region loop config can
override them.
"""

from sqlalchemy import select, delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Neuron, NeuronFiring, SystemState, ABSTRACTION_ARTIFACT

# Refreshes Neuron.centrality = degree / max_degree over promoted
# CONDUCTING edges. Memory-semantics rows (supersedes / scoped-by /
# evidence-link) are provenance, not activation topology — counting them
# kept absorbed corpses outranking living memories (kernel receipt:
# inactive #51 at 0.3898 vs active #57 at 0.2373; post-rewiring replay
# showed the same inversion against the synthesis until this filter).
# UPDATE only touches rows whose value actually changed.
_CENTRALITY_REFRESH_SQL = """
WITH deg AS (
    SELECT nid, COUNT(*) AS d FROM (
        SELECT source_id AS nid FROM neuron_edges
        WHERE edge_type IN ('pyramidal', 'stellate')
        UNION ALL
        SELECT target_id AS nid FROM neuron_edges
        WHERE edge_type IN ('pyramidal', 'stellate')
    ) endpoints
    GROUP BY nid
), mx AS (
    SELECT GREATEST(MAX(d), 1)::float AS m FROM deg
)
UPDATE neurons n
SET centrality = sub.c
FROM (
    SELECT n2.id AS nid,
           COALESCE(ROUND((deg.d / mx.m)::numeric, 4), 0)::float AS c
    FROM neurons n2
    CROSS JOIN mx
    LEFT JOIN deg ON deg.nid = n2.id
) sub
WHERE sub.nid = n.id AND n.centrality IS DISTINCT FROM sub.c
"""


def _is_reclaimable(neuron: Neuron) -> bool:
    """Evidence-tier nodes are reclaimable; higher abstractions are kept.

    Keyed on the abstraction axis (artifact = records/outputs); falls back
    to the legacy layer==5 heuristic for unclassified rows.
    """
    if neuron.abstraction_type is not None:
        return neuron.abstraction_type == ABSTRACTION_ARTIFACT
    return neuron.layer == 5


async def refresh_centrality(db: AsyncSession) -> int:
    """Recompute normalized degree centrality for all neurons. Returns rowcount."""
    result = await db.execute(text(_CENTRALITY_REFRESH_SQL))
    return result.rowcount or 0


async def _decay_stale_utility(db: AsyncSession, total_queries: int) -> tuple[int, int]:
    """Decay utility on neurons that haven't fired recently; deactivate reclaimable
    low-utility ones. Returns (decayed_count, deactivated_count)."""
    recent_window = max(0, total_queries - 200)
    recent_fired = await db.execute(
        select(NeuronFiring.neuron_id).where(
            NeuronFiring.global_query_offset >= recent_window
        ).distinct()
    )
    recently_active_ids = {r[0] for r in recent_fired.all()}

    all_neurons = await db.execute(select(Neuron).where(Neuron.is_active == True))
    decayed = 0
    deactivated = 0
    for neuron in all_neurons.scalars():
        if neuron.id in recently_active_ids:
            continue
        neuron.avg_utility *= settings.consolidation_decay_rate
        decayed += 1
        low_utility = neuron.avg_utility < settings.consolidation_deactivation_threshold
        if low_utility and neuron.invocations > 0 and _is_reclaimable(neuron):
            neuron.is_active = False
            deactivated += 1
    return decayed, deactivated


async def run_consolidation(db: AsyncSession) -> dict:
    """Run periodic consolidation: decay, prune, deactivate, refresh centrality."""
    state_result = await db.execute(select(SystemState).where(SystemState.id == 1))
    state = state_result.scalar_one_or_none()
    if not state:
        return {"status": "no_state"}

    total_queries = state.total_queries

    # 1. Prune old firing records
    cutoff = total_queries - settings.consolidation_retention_queries
    if cutoff > 0:
        prune_result = await db.execute(
            delete(NeuronFiring).where(NeuronFiring.global_query_offset < cutoff)
        )
        pruned = prune_result.rowcount
    else:
        pruned = 0

    # 2. Decay utility on neurons that haven't fired recently
    decayed, deactivated = await _decay_stale_utility(db, total_queries)

    # 3. Refresh degree centrality (cold-start prior input)
    centrality_updates = await refresh_centrality(db)

    # Naive UTC: the column is TIMESTAMP WITHOUT TIME ZONE (asyncpg rejects
    # aware datetimes on naive columns).
    from datetime import datetime
    state.last_consolidation_at = datetime.utcnow()
    await db.commit()

    # Consolidation pruned firings + changed utility/centrality/activation — the
    # incremental firing hook only appends, so force a fresh index rebuild.
    from app.services.neuron_index import invalidate_index
    invalidate_index()

    # Warm the regulatory (engram) cache from eCFR — FIRE-AND-FORGET on its own
    # session so a slow/large fetch never blocks this heartbeat. resolve_engrams
    # reads cache only; this refresh rides the ~daily heartbeat + manual button.
    engram_warm_scheduled = False
    if settings.engram_resolve_enabled:
        from app.services.regulatory_resolve import schedule_engram_cache_warm
        engram_warm_scheduled = schedule_engram_cache_warm()

    return {
        "status": "consolidated",
        "firings_pruned": pruned,
        "neurons_decayed": decayed,
        "neurons_deactivated": deactivated,
        "centrality_updates": centrality_updates,
        "engram_cache": {"scheduled": engram_warm_scheduled},
        "total_queries": total_queries,
    }
