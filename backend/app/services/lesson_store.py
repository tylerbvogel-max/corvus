"""Persist evidence-gated lessons through the tiered write gate.

Shared by the /remember endpoint and the episode distiller so every
lesson — explicit save or distilled candidate — takes the identical
path: staged proposal → write-gate routing → Action Bus apply → inline
embed + semantic-cache update. Informational-tier saves auto-commit and
are reclaimed by consolidation decay if never reinforced.
"""

import asyncio
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutopilotProposal, Neuron, ProposalItem
from app.services.write_gate import authority_rank, route_proposal

logger = logging.getLogger(__name__)


async def resolve_scope_anchor(
    db: AsyncSession, scope: str | None,
) -> tuple[int | None, str | None, int | None]:
    """Anchor point (parent_id, role_key, layer) for a save within its scope.

    Role-less, parentless neurons systematically lose the role-match
    scoring bonus to scaffold nodes and get displaced from the final
    slice by structural completion — anchoring under the scope's
    primary role node makes saves first-class graph citizens. The
    anchor's layer is returned because layer must equal tree depth
    (projection-depth metadata): the /neurons/tree max_depth filter cuts
    by layer, so a lesson whose layer overstates its depth vanishes from
    the Explorer's initial view.
    """
    if not scope:
        return None, None, None
    role_node = (await db.execute(
        select(Neuron)
        .where(Neuron.department == scope, Neuron.node_type == "role",
               Neuron.is_active.is_(True))
        .order_by(Neuron.id).limit(1)
    )).scalar_one_or_none()
    if role_node is None:
        return None, None, None
    assert role_node.department == scope, "anchor must live in the scope"
    return role_node.id, role_node.role_key, role_node.layer


async def get_or_create_project_node(
    db: AsyncSession, scope: str, project: str,
    role_id: int, role_key: str | None, role_layer: int | None,
) -> Neuron:
    """Per-project sub-anchor within a scope: lessons about corvus nest
    under a `project` node named corvus, not directly under the role —
    contextual truths live inside their context."""
    assert project.strip(), "project must be non-empty"
    existing = (await db.execute(
        select(Neuron).where(Neuron.department == scope,
                             Neuron.node_type == "project",
                             Neuron.label == project.strip(),
                             Neuron.is_active.is_(True)).limit(1)
    )).scalar_one_or_none()
    if existing is not None:
        return existing
    from app.middleware.rbac import UserIdentity
    from app.services import action_bus
    identity = UserIdentity(user_id="lesson_store", role="admin", source="system")
    result = await action_bus.submit(
        db=db, kind="neuron.create", actor=identity, actor_type="system",
        input_data={"spec": {
            "parent_id": role_id, "layer": (role_layer or 1) + 1,
            "node_type": "project", "abstraction_type": "structural",
            "label": project.strip(),
            "content": f"Project scope container for {project.strip()} lessons.",
            "summary": f"Project: {project.strip()}",
            "department": scope, "role_key": role_key,
            "source_origin": "lesson_store", "source_type": "operational",
            "authority_level": "informational",
        }, "reason": f"project sub-anchor: {project.strip()}"},
    )
    assert result.state == "applied", f"project node create failed: {result.error}"
    node = await db.get(Neuron, (result.payload or {})["neuron_id"])
    assert node is not None, "created project node must exist"
    return node


def _lesson_content(
    *, lesson: str, evidence: str, node_type: str,
    abstraction_type: str | None, entities: list[str] | None,
    frame_fields: dict[str, str | None],
) -> str:
    """The neuron body: an evidence frame for durable memory classes.

    mind-neuron-evidence-frame — a lesson used to be
    "<prose>\\n\\nEvidence: <prose>", which is exactly the shape Step 06
    found could be topically right and still unable to reconstruct an
    answer. Durable classes now build the canonical nine-slot frame and
    fail closed here, at the composer, rather than at the Action Bus:
    a caller that cannot say why a fact will be needed or how it would be
    asked for has not finished writing the memory.

    `future_use` and `likely_queries` have no honest default and are
    therefore genuinely required. `time_scope`, `context`, `confidence`
    and `volatility` fall back to their conservative markers (unknown /
    uncertain), which are truthful admissions rather than claims — an
    unset volatility must never read as "stable", because the staleness
    gate gives stable facts protection they would not have earned.
    """
    from app.services.evidence_frame import (
        build_frame, enforcement_enabled, is_framed, requires_frame,
    )
    if not (requires_frame(node_type, abstraction_type)
            and enforcement_enabled()):
        return f"{lesson.strip()}\n\nEvidence: {evidence.strip()}"
    # Already a frame (capsule import from a framed graph): keep the
    # author's slots verbatim. Re-deriving them here would either nest a
    # frame inside its own Claim slot or invent slots the source never
    # asserted — both worse than transporting what was actually written.
    if is_framed(lesson):
        return lesson.strip()
    return build_frame(
        claim=lesson.strip(),
        evidence=evidence.strip(),
        entities=", ".join(entities) if entities else "none",
        time_scope=frame_fields.get("time_scope") or "unknown",
        context=frame_fields.get("context") or "unknown",
        future_use=(frame_fields.get("future_use") or "").strip(),
        likely_queries=(frame_fields.get("likely_queries") or "").strip(),
        confidence=frame_fields.get("confidence") or "medium",
        volatility=frame_fields.get("volatility") or "uncertain",
    )


def _lesson_spec(
    *, lesson: str, evidence: str, label: str, scope: str | None,
    node_type: str, abstraction_type: str | None, summary: str | None,
    authority_level: str, source_origin: str,
    parent_id: int | None, role_key: str | None, anchor_layer: int | None,
    entities: list[str] | None = None,
    frame_fields: dict[str, str | None] | None = None,
) -> dict:
    """Neuron spec for a lesson; evidence rides content + citation."""
    from app.services.recall_lanes import normalize_entities
    assert lesson.strip(), "lesson must be non-empty"
    assert evidence.strip(), "evidence must be non-empty"
    norm_entities = normalize_entities(entities)
    content = _lesson_content(
        lesson=lesson, evidence=evidence, node_type=node_type,
        abstraction_type=abstraction_type, entities=norm_entities,
        frame_fields=frame_fields or {},
    )
    return {
        **({"entities": norm_entities} if norm_entities else {}),
        # layer = tree depth (anchor + 1); unanchored saves sit at 3
        "layer": (anchor_layer + 1) if anchor_layer is not None else 3,
        "parent_id": parent_id,
        "role_key": role_key,
        "node_type": node_type,
        "abstraction_type": abstraction_type,
        "label": label.strip(),
        "content": content,
        "summary": summary or lesson.strip()[:280],
        "department": scope,
        "source_origin": source_origin,
        "source_type": "operational",
        "citation": evidence.strip()[:500],
        "authority_level": authority_level,
    }


async def _embed_created(db: AsyncSession, neuron_id: int) -> None:
    """Embed a newly created neuron and refresh its semantic-cache entry.

    Creates via the Action Bus don't embed (embeddings are normally a
    batch job); without this, a saved lesson would be invisible to
    recall until the next full re-embed.
    """
    from app.services.embedding_service import embed_text
    from app.services.reconsolidation.inheritance import embedding_input
    from app.services.semantic_prefilter import update_cache_incremental

    neuron = await db.get(Neuron, neuron_id)
    assert neuron is not None, f"created neuron {neuron_id} must exist"
    text = embedding_input(neuron.label, neuron.summary, neuron.content)
    loop = asyncio.get_running_loop()
    vec = await loop.run_in_executor(None, embed_text, text)
    assert len(vec) > 0, "embedding must be non-empty"
    neuron.embedding = json.dumps(vec)
    await db.flush()
    await update_cache_incremental(db, [neuron_id], "neuron")


async def label_exists(db: AsyncSession, label: str) -> bool:
    """Case-insensitive exact-label duplicate check against active neurons."""
    assert label.strip(), "label must be non-empty"
    from sqlalchemy import func
    row = (await db.execute(
        select(Neuron.id).where(
            func.lower(Neuron.label) == label.strip().lower(),
            Neuron.is_active.is_(True),
        ).limit(1)
    )).scalar_one_or_none()
    return row is not None


async def _nearest_active_lesson(db: AsyncSession, spec: dict) -> dict | None:
    """Nearest active lesson the candidate should queue against, if any.

    Two lanes (kernel Phase 1A), same eligibility as the janitor's dedup
    census; one local embed per save:
    - cosine: near-verbatim at FUSE_SIM — the original write gate.
    - lexical: NVM receipt — the judged duplicates entered at 0.757-0.822
      cosine, under the 0.88 radar, and lint was the only backstop. A
      candidate in the near-miss band (>= NEAR_MISS_SIM) whose token/
      entity signals or fact fingerprint (shared paths/commands/versions)
      match an active lesson queues for review instead of silently
      inserting. The global cosine bar is NOT lowered.
    A cosine hit outranks a lexical hit; ties break by similarity."""
    import numpy as np
    from app.services.embedding_service import embed_text
    from app.services.mind_janitors import FUSE_SIM, _load_lessons
    from app.services.mind_lint import NEAR_MISS_SIM, lexical_high
    from app.services.reconsolidation.fingerprints import nominates

    # Must use the canonical recipe: _load_lessons returns vectors stored
    # by embedding_input, and since that recipe became frame-aware a
    # hand-rolled string here would embed the candidate's frame headings
    # while comparing against boilerplate-free neighbours — a cosine
    # comparison across two different spaces, silently mis-scoring the
    # near-dup gate.
    from app.services.reconsolidation.inheritance import embedding_input
    text = embedding_input(
        spec["label"], spec.get("summary"), spec.get("content"))
    loop = asyncio.get_running_loop()
    vec = np.array(await loop.run_in_executor(None, embed_text, text[:2000]),
                   dtype=np.float64)
    norm = np.linalg.norm(vec)
    if norm == 0:
        return None
    vec /= norm
    # Transient row (never added to the session) so the lexical helpers
    # see the candidate exactly as it would land.
    candidate = Neuron(
        label=spec["label"], content=spec.get("content"),
        summary=spec.get("summary"), layer=spec.get("layer", 3),
        node_type=spec.get("node_type", "lesson"),
        entities=spec.get("entities"),
    )
    best: dict | None = None
    for n in await _load_lessons(db):
        emb = np.array(json.loads(n.embedding), dtype=np.float64)
        denom = np.linalg.norm(emb)
        if denom == 0:
            continue
        sim = float(vec @ (emb / denom))
        if sim >= FUSE_SIM:
            lane = "cosine"
        elif sim >= NEAR_MISS_SIM and (lexical_high(candidate, n)
                                       or nominates(candidate, n)):
            lane = "lexical"
        else:
            continue
        if best is None or (lane == "cosine", sim) > (best["lane"] == "cosine",
                                                      best["sim"]):
            best = {"id": n.id, "label": n.label, "sim": sim, "lane": lane}
    return best


def _near_dup_disposition(near: dict | None, requires_approval: bool) -> str:
    """'insert' | 'queue' | 'skip' for a near-dup gate hit. Pure.

    With approval required (every real tenant), any hit queues for human
    review — unchanged. With approval disabled (throwaway eval tenants),
    the gate models the approved outcome: near-verbatim cosine hits are
    true duplicates (skip); lexical review-band nominations insert, because
    that lane flags suspicion for a reviewer, not a verdict."""
    if near is None:
        return "insert"
    if requires_approval:
        return "queue"
    return "skip" if near["lane"] == "cosine" else "insert"


async def _embed_and_wire(db: AsyncSession, neuron_id: int) -> None:
    """Make a freshly-created lesson recallable: embed it, then wire it to
    its nearest neighbors so it joins spread activation at birth rather than
    waiting for a manual bootstrap pass. Wiring is best-effort — a graph
    failure must never fail the save (the lesson is still findable by
    embedding search without edges)."""
    await _embed_created(db, neuron_id)
    try:
        from app.services.seeding_service import wire_neuron_knn_edges
        await wire_neuron_knn_edges(db, neuron_id)
    except Exception:
        logger.exception("genesis wiring failed for neuron %s", neuron_id)


async def save_lesson(
    db: AsyncSession, *, lesson: str, evidence: str, label: str,
    scope: str | None = None, node_type: str = "lesson",
    abstraction_type: str | None = "principle", summary: str | None = None,
    authority_level: str = "informational", source_origin: str = "remember_api",
    gap_source: str = "remember_api", project: str | None = None,
    entities: list[str] | None = None,
    time_scope: str | None = None, context: str | None = None,
    future_use: str | None = None, likely_queries: str | None = None,
    confidence: str | None = None, volatility: str | None = None,
) -> dict:
    """Stage a lesson save and route it through the write gate. Commits.

    The trailing six arguments are evidence-frame slots
    (mind-neuron-evidence-frame). For durable memory classes they are not
    decoration: `future_use` and `likely_queries` are required, and the
    save raises EvidenceFrameError without them.
    """
    # Reject malformed authority before staging, anchors, embedding or commits.
    rank = authority_rank(authority_level)
    if authority_level is None:
        authority_level = "informational"
    # Scopes are region tags; casefold to the canonical spelling so
    # "assistant" and "Assistant" never split the corpus-by-scope counts.
    if scope:
        canonical = {s.casefold(): s for s in
                     ("Projects", "User", "Harness", "Environment", "Assistant")}
        scope = canonical.get(scope.strip().casefold(), scope.strip())
    if scope == "Assistant" and rank < authority_rank("organizational"):
        authority_level = "organizational"
    proposal = AutopilotProposal(
        state="proposed",
        gap_source=gap_source,
        gap_description=f"lesson save: {label.strip()[:120]}",
    )
    db.add(proposal)
    await db.flush()
    assert proposal.id is not None, "proposal must have id after flush"

    parent_id, role_key, anchor_layer = await resolve_scope_anchor(db, scope)
    if project and scope and parent_id is not None:
        project_node = await get_or_create_project_node(
            db, scope, project, parent_id, role_key, anchor_layer)
        parent_id, anchor_layer = project_node.id, project_node.layer
    spec = _lesson_spec(
        lesson=lesson, evidence=evidence, label=label, scope=scope,
        node_type=node_type, abstraction_type=abstraction_type,
        summary=summary, authority_level=authority_level,
        source_origin=source_origin, parent_id=parent_id, role_key=role_key,
        anchor_layer=anchor_layer, entities=entities,
        frame_fields={
            "time_scope": time_scope, "context": context,
            "future_use": future_use, "likely_queries": likely_queries,
            "confidence": confidence, "volatility": volatility,
        },
    )
    item = ProposalItem(
        proposal_id=proposal.id, action="create",
        neuron_spec_json=json.dumps(spec),
        reason=f"{gap_source}: {evidence.strip()[:400]}",
    )
    db.add(item)
    await db.flush()
    # apply_approved_proposal reads proposal.items; a freshly-flushed ORM
    # object hasn't loaded the relationship, and async lazy-load raises
    # MissingGreenlet — load it explicitly before routing.
    await db.refresh(proposal, ["items"])

    # WRITE-TIME NEAR-DUP GATE (graph lint): label_exists above only stops
    # exact-label restatements — paraphrases walked straight in and the
    # janitor was the only backstop. Embed the candidate BEFORE routing;
    # a near-verbatim cosine hit against an active lesson forces the
    # human-review queue instead of a silent insert.
    #
    # When mind_dedup_requires_approval is FALSE (throwaway eval tenants
    # only — documented never-on-a-real-tenant), the queue has no human to
    # drain it, so the gate models the approved steady state instead:
    # a cosine hit (>= FUSE_SIM, near-verbatim) skips as a true duplicate;
    # a lexical-band nomination inserts with its annotation kept — the
    # 2026-07-18 LoCoMo Phase A showed that lane parking 180 DISTINCT
    # persona facts (65% of the corpus) into review limbo, cratering
    # recall ~20pp. Review-band suspicion gets benefit of the doubt in
    # auto mode; near-verbatim does not.
    from app.config import settings
    near = await _nearest_active_lesson(db, spec)
    disposition = _near_dup_disposition(
        near, settings.mind_dedup_requires_approval)
    if near is not None:
        proposal.gap_description = (
            f"NEAR-DUPLICATE ({near['lane']} lane) @ sim {near['sim']:.3f} "
            f"of #{near['id']} '{near['label'][:60]}' — "
            f"{proposal.gap_description}"
        )[:2000]
    if disposition == "queue":
        await db.commit()
        logger.info("near-dup gate queued lesson %r (%s lane, sim %.3f vs #%s)",
                    label[:60], near["lane"], near["sim"], near["id"])
        return {
            "route": "queue",
            "reason": (f"near-duplicate of #{near['id']} "
                       f"'{near['label'][:60]}' ({near['lane']} lane, "
                       f"sim {near['sim']:.3f}) — "
                       "queued for human review instead of silent insert"),
            "proposal_id": proposal.id,
            "neuron_id": None,
        }
    if disposition == "skip":
        proposal.state = "rejected"
        await db.commit()
        logger.info("near-dup gate auto-skipped duplicate %r (cosine sim "
                    "%.3f vs #%s)", label[:60], near["sim"], near["id"])
        return {
            "route": "skip",
            "reason": (f"auto-skip: near-verbatim duplicate of #{near['id']} "
                       f"'{near['label'][:60]}' (sim {near['sim']:.3f}); "
                       "dedup approval disabled on this tenant"),
            "proposal_id": proposal.id,
            "neuron_id": None,
        }

    decision = await route_proposal(
        db, proposal, guardrails_passed=None, confidence=None, region=scope,
    )
    assert decision.route in ("auto", "queue"), "unexpected gate route"
    neuron_id = None
    if decision.route == "auto":
        await db.refresh(item)
        neuron_id = item.created_neuron_id
        if neuron_id is not None:
            await _embed_and_wire(db, neuron_id)
    await db.commit()
    return {
        "route": decision.route,
        "reason": decision.reason,
        "proposal_id": proposal.id,
        "neuron_id": neuron_id,
    }
