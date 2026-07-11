"""Persist evidence-gated lessons through the tiered write gate.

Shared by the /remember endpoint and the episode distiller so every
lesson — explicit save or distilled candidate — takes the identical
path: staged proposal → write-gate routing → Action Bus apply → inline
embed + semantic-cache update. Informational-tier saves auto-commit and
are reclaimed by consolidation decay if never reinforced.
"""

import asyncio
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AutopilotProposal, Neuron, ProposalItem
from app.services.write_gate import route_proposal


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


def _lesson_spec(
    *, lesson: str, evidence: str, label: str, scope: str | None,
    node_type: str, abstraction_type: str | None, summary: str | None,
    authority_level: str, source_origin: str,
    parent_id: int | None, role_key: str | None, anchor_layer: int | None,
) -> dict:
    """Neuron spec for a lesson; evidence rides content + citation."""
    assert lesson.strip(), "lesson must be non-empty"
    assert evidence.strip(), "evidence must be non-empty"
    content = f"{lesson.strip()}\n\nEvidence: {evidence.strip()}"
    return {
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
    from app.services.semantic_prefilter import update_cache_incremental

    neuron = await db.get(Neuron, neuron_id)
    assert neuron is not None, f"created neuron {neuron_id} must exist"
    text = f"{neuron.label}. {neuron.summary or ''} {neuron.content or ''}"
    loop = asyncio.get_running_loop()
    vec = await loop.run_in_executor(None, embed_text, text[:2000])
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


async def save_lesson(
    db: AsyncSession, *, lesson: str, evidence: str, label: str,
    scope: str | None = None, node_type: str = "lesson",
    abstraction_type: str | None = "principle", summary: str | None = None,
    authority_level: str = "informational", source_origin: str = "remember_api",
    gap_source: str = "remember_api", project: str | None = None,
) -> dict:
    """Stage a lesson save and route it through the write gate. Commits."""
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
        anchor_layer=anchor_layer,
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

    decision = await route_proposal(
        db, proposal, guardrails_passed=None, confidence=None, region=scope,
    )
    assert decision.route in ("auto", "queue"), "unexpected gate route"
    neuron_id = None
    if decision.route == "auto":
        await db.refresh(item)
        neuron_id = item.created_neuron_id
        if neuron_id is not None:
            await _embed_created(db, neuron_id)
    await db.commit()
    return {
        "route": decision.route,
        "reason": decision.reason,
        "proposal_id": proposal.id,
        "neuron_id": neuron_id,
    }
