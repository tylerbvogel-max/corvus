"""Maintenance substrate for the memory tenant — the primitives every
curation pass computes over, extracted out from under the scheduler.

Record 04b (durability-maintenance-cluster) measured the last of the five
import cycles: twelve modules braided by seven module-scope and seventeen
function-scope edges. The read-only mapping pass found that NO back-edge in
that cycle wanted a janitor pass. Every one of them wanted substrate:

    delivery_mode  -> LESSON_TYPES, _log_action
    skill_compiler -> LESSON_TYPES, _add_memory_edge, _load_lessons, _log_action
    mind_lint      -> EPISODE_DIR, BORDERLINE_SIM, _load_lessons, _similar_pairs
    reconsolidation.plan -> content_hash (via mind_lint)

``mind_janitors`` was doing two jobs — holding the corpus primitives AND
running the schedule that consumes them — so anything that needed a
primitive had to import the scheduler, and the scheduler imported it back.
The same pressure shows up outside the cycle: eight further modules
(routers/auditor, routers/compile, routers/janitor, distiller,
injection_channel, lesson_store, memory_quality_auditor, mind_metrics)
already reach into the janitor module for exactly these names. This file is
the layer they were all actually asking for.

WHAT LIVES HERE: the episode log location and its append, the lesson-corpus
loader and its similarity census, the calibrated pair thresholds, the stable
content hash, and the one governed helper for asserting a memory-semantics
edge. All of it is read, compute, or an Action Bus call.

WHAT DELIBERATELY DOES NOT: the janitor passes, the schedule, and — the
point of the whole record — the direct ORM writes to protected columns
(``is_active``, ``superseded_by``, ``avg_utility``, ``authority_level``).
Those stay in ``mind_janitors`` where the classification put them. A
primitive shared this widely must never be a place a write can hide.

``mind_janitors`` re-exports every name below so its existing importers, and
the test seams that monkeypatch them there, keep resolving unchanged.
"""

import hashlib
import json
import os
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Neuron

EPISODE_DIR = os.path.expanduser(
    os.environ.get("CORVUS_MIND_EPISODE_DIR", "~/.corvus-mind/episodes")
)
ACTIONS_LOG = os.path.join(EPISODE_DIR, "janitor-actions.jsonl")
LESSON_TYPES = ("lesson", "tool-profile", "context-scope")
FUSE_SIM = 0.88          # >= : auto-fuse (same scope only)
BORDERLINE_SIM = 0.75    # >= : report for review, never auto-fuse
                         # (calibrated on real pair 22/28 @ 0.778: complementary
                         # facts, related-not-duplicate — must surface, not fuse)


# ── episode log ─────────────────────────────────────────────────────────

def _log_action(action: str, detail: dict) -> None:
    """Janitor actions are episodes: append to the janitor actions log."""
    os.makedirs(EPISODE_DIR, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "event": "JanitorAction",
        "action": action,
        **detail,
    }
    with open(ACTIONS_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


# ── corpus census ───────────────────────────────────────────────────────

async def _load_lessons(db: AsyncSession) -> list[Neuron]:
    # IDENTITY WALL (mind-reference-class): reference-class neurons never
    # enter lesson maintenance or the skill compiler's cluster feed — a
    # PDF can become "what I can look up", never "who I am".
    from app.services.reference_class import reference_exclusion_filters
    rows = (await db.execute(
        select(Neuron).where(
            Neuron.is_active.is_(True),
            Neuron.node_type.in_(LESSON_TYPES),
            Neuron.embedding.isnot(None),
            *reference_exclusion_filters(),
        ).order_by(Neuron.id)
    )).scalars().all()
    return list(rows)


def _similar_pairs(
    lessons: list[Neuron], floor: float = BORDERLINE_SIM,
) -> list[tuple[int, int, float]]:
    """Index pairs (i, j, cosine) at or above `floor`. The default floor is
    BORDERLINE_SIM; the lint lane lowers it to NEAR_MISS_SIM so lexically
    near-verbatim pairs below the cosine radar can still be judged."""
    if len(lessons) < 2:
        return []
    matrix = np.array([json.loads(n.embedding) for n in lessons], dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = matrix / norms
    sims = unit @ unit.T
    pairs: list[tuple[int, int, float]] = []
    for i in range(len(lessons)):
        for j in range(i + 1, len(lessons)):
            if sims[i, j] >= floor:
                pairs.append((i, j, float(sims[i, j])))
    return pairs


def content_hash(neuron: Neuron) -> str:
    """Stable hash of the judged text — mismatch means the verdict is stale.

    ONE definition on purpose: the lint's verdict store and
    ``reconsolidation.plan.snapshot_of`` must agree bit-for-bit, or a
    FusionPlan's drift detection would disagree with the verdict that
    nominated it. Keeping it below both is what makes that parity structural
    rather than a comment.
    """
    text = f"{neuron.label}\n{neuron.content or ''}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ── governed memory edges ───────────────────────────────────────────────

async def _add_memory_edge(
    db: AsyncSession, source_id: int, target_id: int,
    edge_type: str, context: str,
) -> None:
    """Create a memory-semantics edge through the Action Bus (audited).

    Idempotent: asserting a relationship that already exists is a no-op,
    not an error. Janitor passes re-derive the same resolution when both
    parties survive (e.g. a contradiction pair re-detected next run) —
    found 2026-07-12 crashing every staleness pass on neuron_edges_pkey,
    which killed the whole janitor run before decay/promotion could run."""
    assert edge_type in ("supersedes", "scoped-by", "evidence-link"), \
        f"not a memory edge type: {edge_type}"
    from sqlalchemy import select as sa_select
    from app.middleware.rbac import UserIdentity
    from app.models import NeuronEdge
    from app.services import action_bus

    existing = (await db.execute(
        sa_select(NeuronEdge).where(
            NeuronEdge.source_id == source_id,
            NeuronEdge.target_id == target_id,
        ).limit(1)
    )).scalar_one_or_none()
    if existing is not None:
        return  # relationship already asserted — re-assertion is a no-op

    identity = UserIdentity(user_id="mind_janitor", role="admin", source="system")
    result = await action_bus.submit(
        db=db, kind="edge.link", actor=identity, actor_type="system",
        input_data={
            "source_id": source_id, "target_id": target_id,
            # Memory edges are semantic assertions, not co-fire statistics:
            # meet the promotion threshold by construction so they land in
            # neuron_edges (durable), never the reapable weak tier.
            "weight": 1.0,
            "co_fire_count": settings.edge_promote_min_cofires,
            "edge_type": edge_type, "source": "mind_janitor",
            "context": context[:300],
        },
        reason=context[:200],
    )
    assert result.state == "applied", f"edge.link failed: {result.error}"
