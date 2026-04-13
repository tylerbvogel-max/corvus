"""POST /ingest/observation — Corvus observation ingestion pipeline."""

import json
from datetime import datetime
from types import MappingProxyType

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Neuron, NeuronEdge, NeuronRefinement, ObservationQueue
from app.services.llm_provider import llm_chat
from app.schemas import ObservationEvalRequest, ObservationBatchEvalRequest, ObservationApplyRequest
from app.services.reference_hooks import populate_external_references
from app.services import action_bus
from app.middleware.rbac import UserIdentity, resolve_identity

_SYSTEM_ACTOR = UserIdentity(user_id="corvus-system", role="admin", source="system")

router = APIRouter(prefix="/ingest", tags=["ingest"])


class ObservationRequest(BaseModel):
    source: str = "corvus"
    user_id: str = "anonymous"
    observation_type: str = Field(..., pattern="^(decision|process|entity|pattern|digest)$")
    text: str = Field(..., min_length=10, max_length=10000)
    entities: list[dict] = []
    app_context: str | None = None
    project_path: str | None = None


class ObservationResponse(BaseModel):
    observation_id: int
    status: str  # "queued", "auto_created", "duplicate", "edge_strengthened"
    proposed_department: str | None = None
    proposed_layer: int | None = None
    similar_neuron_id: int | None = None
    similar_neuron_label: str | None = None
    similarity_score: float | None = None
    edges_strengthened: int = 0


# Map observation types to neuron layers
OBSERVATION_LAYER_MAP = MappingProxyType({
    "decision": 4,    # L4 = Decision
    "process": 3,     # L3 = System/Process
    "entity": 5,      # L5 = Output/Communication
    "pattern": 3,     # L3 = System/Process
    "digest": 5,      # L5 = Output/Communication
})


def _create_observation_record(
    req: ObservationRequest,
    department: str | None,
    role_key: str | None,
    proposed_layer: int,
    similar_neuron: Neuron | None,
    similarity: float | None,
    status: str,
) -> ObservationQueue:
    return ObservationQueue(
        source=req.source,
        user_id=req.user_id,
        observation_type=req.observation_type,
        text=req.text[:2000],
        entities_json=json.dumps(req.entities[:20]),
        app_context=req.app_context,
        project_path=req.project_path,
        proposed_department=department,
        proposed_role_key=role_key,
        proposed_layer=proposed_layer,
        similar_neuron_id=similar_neuron.id if similar_neuron else None,
        similarity_score=similarity,
        status=status,
    )


async def _handle_duplicate(
    db: AsyncSession,
    req: ObservationRequest,
    similar_neuron: Neuron,
    similarity: float,
    department: str | None,
    role_key: str | None,
    proposed_layer: int,
) -> ObservationResponse:
    edges_strengthened = await _strengthen_entity_edges(db, req.entities, similar_neuron.id)
    similar_neuron.invocations = (similar_neuron.invocations or 0) + 1

    obs = _create_observation_record(
        req, department, role_key, proposed_layer, similar_neuron, similarity, "duplicate",
    )
    db.add(obs)
    await db.commit()

    return ObservationResponse(
        observation_id=obs.id,
        status="edge_strengthened" if edges_strengthened > 0 else "duplicate",
        proposed_department=department,
        proposed_layer=proposed_layer,
        similar_neuron_id=similar_neuron.id,
        similar_neuron_label=similar_neuron.label,
        similarity_score=similarity,
        edges_strengthened=edges_strengthened,
    )


async def _handle_novel(
    db: AsyncSession,
    req: ObservationRequest,
    similar_neuron: Neuron | None,
    similarity: float | None,
    department: str | None,
    role_key: str | None,
    proposed_layer: int,
) -> ObservationResponse:
    obs = _create_observation_record(
        req, department, role_key, proposed_layer, similar_neuron, similarity, "queued",
    )
    db.add(obs)
    await db.flush()

    edges_strengthened = 0
    if req.entities:
        edges_strengthened = await _strengthen_entity_edges(db, req.entities)

    await db.commit()

    return ObservationResponse(
        observation_id=obs.id,
        status="queued",
        proposed_department=department,
        proposed_layer=proposed_layer,
        similar_neuron_id=similar_neuron.id if similar_neuron else None,
        similar_neuron_label=similar_neuron.label if similar_neuron else None,
        similarity_score=similarity,
        edges_strengthened=edges_strengthened,
    )


@router.post("/observation", response_model=ObservationResponse)
async def ingest_observation(req: ObservationRequest, db: AsyncSession = Depends(get_db)):
    """Ingest an observation from Corvus into the neuron graph.

    Pipeline:
    1. Classify the observation (department, role_key, layer)
    2. Check semantic similarity against existing neurons
    3. If novel: queue for review (or auto-create if high confidence)
    4. If similar: strengthen co-firing edges and update recency
    5. Extract entity references and strengthen entity-linked edges
    """
    proposed_layer = OBSERVATION_LAYER_MAP.get(req.observation_type, 3)

    department, role_key = await _classify_observation(req.text, req.observation_type, req.app_context)
    similar_neuron, similarity = await _find_similar_neuron(db, req.text, department)

    if similar_neuron and similarity and similarity >= 0.85:
        return await _handle_duplicate(
            db, req, similar_neuron, similarity, department, role_key, proposed_layer,
        )

    return await _handle_novel(
        db, req, similar_neuron, similarity, department, role_key, proposed_layer,
    )


@router.get("/observations")
async def list_observations(
    status: str = "",
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List observation queue entries."""
    query = select(ObservationQueue).order_by(ObservationQueue.id.desc()).limit(limit)
    if status:
        query = query.where(ObservationQueue.status == status)
    result = await db.execute(query)
    rows = result.scalars().all()
    return [
        {
            "id": o.id,
            "source": o.source,
            "user_id": o.user_id,
            "observation_type": o.observation_type,
            "text": o.text[:200] + ("..." if len(o.text) > 200 else ""),
            "proposed_department": o.proposed_department,
            "proposed_layer": o.proposed_layer,
            "similar_neuron_id": o.similar_neuron_id,
            "similarity_score": o.similarity_score,
            "status": o.status,
            "created_neuron_id": o.created_neuron_id,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
        for o in rows
    ]


@router.post("/observations/{obs_id}/approve")
async def approve_observation(obs_id: int, db: AsyncSession = Depends(get_db)):
    """Approve a queued observation and create a neuron from it."""
    obs = await db.get(ObservationQueue, obs_id)
    if not obs:
        raise HTTPException(status_code=404, detail="Observation not found")
    if obs.status != "queued":
        raise HTTPException(status_code=400, detail=f"Observation is {obs.status}, not queued")

    # Find a suitable parent neuron
    parent_id = None
    if obs.proposed_department and obs.proposed_role_key:
        result = await db.execute(
            select(Neuron).where(
                Neuron.department == obs.proposed_department,
                Neuron.role_key == obs.proposed_role_key,
                Neuron.layer < obs.proposed_layer,
                Neuron.is_active == True,
            ).order_by(Neuron.layer.desc()).limit(1)
        )
        parent = result.scalar_one_or_none()
        if parent:
            parent_id = parent.id

    # Determine node_type from layer
    node_type_map = {3: "system", 4: "decision", 5: "output"}
    node_type = node_type_map.get(obs.proposed_layer, "system")

    # Create the neuron
    label = obs.text[:150].strip()
    if len(obs.text) > 150:
        # Try to find a natural break point
        for sep in [". ", ":", " - ", "; "]:
            idx = obs.text[:150].rfind(sep)
            if idx > 30:
                label = obs.text[:idx].strip()
                break

    result = await action_bus.submit(
        db=db, kind="neuron.create", actor=_SYSTEM_ACTOR,
        actor_type="system",
        input_data={
            "spec": {
                "parent_id": parent_id,
                "layer": obs.proposed_layer,
                "node_type": node_type,
                "label": label,
                "content": obs.text,
                "summary": obs.text[:300] if len(obs.text) > 300 else obs.text,
                "department": obs.proposed_department,
                "role_key": obs.proposed_role_key,
                "source_type": "operational",
                "source_origin": "corvus",
            },
            "reason": f"Corvus observation #{obs.id} approved",
        },
    )
    assert result.state == "applied", f"neuron.create failed: {result.error}"

    neuron_id = result.payload["neuron_id"]
    obs.status = "approved"
    obs.created_neuron_id = neuron_id
    await db.commit()

    neuron = await db.get(Neuron, neuron_id)
    return {
        "observation_id": obs.id,
        "neuron_id": neuron_id,
        "label": neuron.label if neuron else label,
        "department": neuron.department if neuron else obs.proposed_department,
        "layer": neuron.layer if neuron else obs.proposed_layer,
    }


@router.post("/observations/{obs_id}/reject")
async def reject_observation(obs_id: int, db: AsyncSession = Depends(get_db)):
    """Reject a queued observation."""
    obs = await db.get(ObservationQueue, obs_id)
    if not obs:
        raise HTTPException(status_code=404, detail="Observation not found")
    if obs.status != "queued":
        raise HTTPException(status_code=400, detail=f"Observation is {obs.status}, not queued")

    obs.status = "rejected"
    await db.commit()
    return {"observation_id": obs.id, "status": "rejected"}


@router.get("/observations/{obs_id}")
async def get_observation_detail(obs_id: int, db: AsyncSession = Depends(get_db)):
    """Get full observation detail including eval proposals and nearby neurons."""
    obs = await db.get(ObservationQueue, obs_id)
    if not obs:
        raise HTTPException(status_code=404, detail="Observation not found")

    # Load similar neuron details
    similar_neuron_data = None
    if obs.similar_neuron_id:
        sn = await db.get(Neuron, obs.similar_neuron_id)
        if sn:
            similar_neuron_data = {
                "id": sn.id, "label": sn.label, "layer": sn.layer,
                "node_type": sn.node_type, "department": sn.department,
                "role_key": sn.role_key, "summary": sn.summary,
                "content": sn.content, "invocations": sn.invocations,
                "avg_utility": float(sn.avg_utility or 0),
            }

    # Load nearby neurons in same department/role
    nearby = []
    if obs.proposed_department:
        q = select(Neuron).where(
            Neuron.is_active == True,
            Neuron.department == obs.proposed_department,
        ).order_by(Neuron.layer, Neuron.invocations.desc()).limit(10)
        if obs.similar_neuron_id:
            q = q.where(Neuron.id != obs.similar_neuron_id)
        result = await db.execute(q)
        for n in result.scalars().all():
            nearby.append({
                "id": n.id, "label": n.label, "layer": n.layer,
                "node_type": n.node_type, "summary": (n.summary or "")[:200],
                "invocations": n.invocations,
                "avg_utility": float(n.avg_utility or 0),
            })

    eval_data = None
    if obs.eval_json:
        try:
            eval_data = json.loads(obs.eval_json)
        except Exception:
            pass

    return {
        "id": obs.id,
        "source": obs.source,
        "user_id": obs.user_id,
        "observation_type": obs.observation_type,
        "text": obs.text,
        "entities": json.loads(obs.entities_json) if obs.entities_json else [],
        "app_context": obs.app_context,
        "project_path": obs.project_path,
        "proposed_department": obs.proposed_department,
        "proposed_role_key": obs.proposed_role_key,
        "proposed_layer": obs.proposed_layer,
        "similar_neuron_id": obs.similar_neuron_id,
        "similarity_score": obs.similarity_score,
        "similar_neuron": similar_neuron_data,
        "nearby_neurons": nearby,
        "status": obs.status,
        "eval_json": eval_data,
        "eval_model": obs.eval_model,
        "eval_input_tokens": obs.eval_input_tokens,
        "eval_output_tokens": obs.eval_output_tokens,
        "created_neuron_id": obs.created_neuron_id,
        "created_at": obs.created_at.isoformat() if obs.created_at else None,
    }


async def _build_similar_neuron_context(
    db: AsyncSession, obs: ObservationQueue
) -> str:
    if not obs.similar_neuron_id:
        return ""
    sn = await db.get(Neuron, obs.similar_neuron_id)
    if not sn:
        return ""

    context = (
        f"\n## Most Similar Neuron (similarity: {obs.similarity_score:.2f})\n"
        f"ID: {sn.id} | Layer: L{sn.layer} | Type: {sn.node_type}\n"
        f"Label: {sn.label}\n"
        f"Department: {sn.department} | Role: {sn.role_key}\n"
        f"Invocations: {sn.invocations} | Utility: {sn.avg_utility:.2f}\n"
        f"Summary: {sn.summary or '(none)'}\n"
        f"Content: {(sn.content or '(none)')[:1000]}\n"
    )

    parent = await db.get(Neuron, sn.parent_id) if sn.parent_id else None
    if parent:
        context += f"\nParent: [{parent.id}] L{parent.layer} {parent.label}\n"
        grandparent = await db.get(Neuron, parent.parent_id) if parent.parent_id else None
        if grandparent:
            context += f"Grandparent: [{grandparent.id}] L{grandparent.layer} {grandparent.label}\n"

    sibling_result = await db.execute(
        select(Neuron.id, Neuron.label, Neuron.layer).where(
            Neuron.parent_id == sn.parent_id,
            Neuron.id != sn.id,
            Neuron.is_active == True,
        ).limit(5)
    )
    siblings = sibling_result.all()
    if siblings:
        context += "\nSiblings:\n"
        for sid, slabel, slayer in siblings:
            context += f"  [{sid}] L{slayer} {slabel}\n"

    return context


async def _build_nearby_neuron_context(
    db: AsyncSession, obs: ObservationQueue
) -> str:
    if not obs.proposed_department:
        return ""

    q = select(Neuron).where(
        Neuron.is_active == True,
        Neuron.department == obs.proposed_department,
    ).order_by(Neuron.layer, Neuron.invocations.desc()).limit(10)
    if obs.similar_neuron_id:
        q = q.where(Neuron.id != obs.similar_neuron_id)

    result = await db.execute(q)
    nearby_neurons = result.scalars().all()
    if not nearby_neurons:
        return ""

    context = "\n## Nearby Neurons in Department\n"
    for n in nearby_neurons:
        context += (
            f"  [{n.id}] L{n.layer} {n.label} "
            f"(inv:{n.invocations}, util:{n.avg_utility:.2f}) "
            f"— {(n.summary or '')[:100]}\n"
        )
    return context


def _build_entities_text(obs: ObservationQueue) -> str:
    try:
        entities = json.loads(obs.entities_json) if obs.entities_json else []
        if entities:
            return "\nEntities: " + ", ".join(
                f"{e.get('type','?')}:{e.get('value','?')}" for e in entities[:10]
            )
    except Exception:
        pass
    return ""


def _parse_eval_response(response_text: str) -> dict:
    import re

    try:
        eval_data = json.loads(response_text)
    except json.JSONDecodeError:
        match = re.search(r'\{[\s\S]*\}', response_text)
        if match:
            eval_data = json.loads(match.group())
        else:
            raise HTTPException(status_code=500, detail="LLM response was not valid JSON")

    assert isinstance(eval_data, dict), "LLM eval response must parse to a dict"
    assert eval_data.get("action") in ("create", "update", "merge", "dismiss"), \
        f"eval action must be create/update/merge/dismiss, got {eval_data.get('action')!r}"
    return eval_data


# LLM PROMPT INTENT: Evaluate a Corvus screen-capture observation and propose a single action
#   (create, update, merge, or dismiss) to integrate the observation into the neuron knowledge
#   graph. The LLM acts as a graph architect deciding how new operational knowledge should be
#   incorporated relative to existing neurons.
# INPUT: User message contains the observation text, type, app context, proposed department/role,
#   extracted entities, the most similar existing neuron with its parent chain and siblings, and
#   nearby neurons in the same department. All formatted as labeled markdown sections.
# OUTPUT FORMAT: Raw JSON object (no markdown fences):
#   {"reasoning": str, "action": "create|update|merge|dismiss",
#    "updates": [{"neuron_id": int, "field": str, "old_value": str, "new_value": str, "reason": str}],
#    "new_neurons": [{"parent_id": int, "layer": int, "node_type": str, "label": str,
#     "content": str, "summary": str, "department": str, "role_key": str, "reason": str}],
#    "merge_target_id": int|null, "merge_content_delta": str|null}
#   Only the fields relevant to the chosen action should be populated.
# FAILURE MODES: If the LLM returns non-JSON, a regex fallback attempts to extract the outermost
#   JSON object. If that also fails, a 500 HTTP error is raised. If the LLM returns an invalid
#   action value, the assertion will raise an AssertionError (caught as 500). If the
#   LLM proposes a nonexistent parent_id for new neurons, the neuron is created with a dangling
#   parent reference. Proposals are stored but NOT applied until explicit human approval via
#   the /apply endpoint — this is a safety gate per Corvus development rules.
_EVAL_SYSTEM_PROMPT = """You are a neuron graph architect reviewing a screen-capture observation for potential ingestion into a knowledge graph.

The graph has 6 layers:
- L0: Department (organizational unit)
- L1: Role (functional role within department)
- L2: Task (specific responsibility)
- L3: System/Process (procedures, tools, workflows)
- L4: Decision (judgment calls, criteria, thresholds)
- L5: Output/Communication (deliverables, reports, notifications)

You must decide ONE action for this observation:
- **create**: New knowledge not captured by any existing neuron. Specify parent_id, layer, label, content, summary.
- **update**: The observation corrects or enhances a specific field on an existing neuron. Specify neuron_id, field, old_value, new_value.
- **merge**: The observation adds operational detail to an existing neuron's content. Specify merge_target_id and the content to append.
- **dismiss**: The observation is noise, already captured, or too ephemeral for the graph.

Rules:
- New neurons MUST attach under an existing parent (use a nearby neuron's parent_id or a nearby neuron itself as parent).
- Keep content concise and factual — this is operational knowledge from screen observation, not authoritative.
- Prefer merge/update over create when the knowledge overlaps significantly with existing neurons.
- For merge, provide only the NEW content delta to append, not the full existing content.

Respond with ONLY a JSON object (no markdown fences):
{
  "reasoning": "Brief explanation of your decision",
  "action": "create|update|merge|dismiss",
  "updates": [{"neuron_id": N, "field": "content|summary|label", "old_value": "...", "new_value": "...", "reason": "..."}],
  "new_neurons": [{"parent_id": N, "layer": N, "node_type": "system|decision|output", "label": "...", "content": "...", "summary": "...", "department": "...", "role_key": "...", "reason": "..."}],
  "merge_target_id": null,
  "merge_content_delta": null
}

Only populate the relevant array/fields for your chosen action. Leave others empty/null."""


@router.post("/observations/{obs_id}/evaluate")
async def evaluate_observation(
    obs_id: int, req: ObservationEvalRequest, db: AsyncSession = Depends(get_db)
):
    """LLM-evaluate an observation and propose neuron actions."""
    obs = await db.get(ObservationQueue, obs_id)
    if not obs:
        raise HTTPException(status_code=404, detail="Observation not found")
    if obs.status not in ("queued", "evaluated"):
        raise HTTPException(status_code=400, detail=f"Observation is {obs.status}, must be queued or evaluated")
    assert obs.text and len(obs.text.strip()) > 0, "Observation text must be non-empty for evaluation"

    similar_context = await _build_similar_neuron_context(db, obs)
    nearby_context = await _build_nearby_neuron_context(db, obs)
    entities_text = _build_entities_text(obs)

    user_message = (
        f"## Observation\n"
        f"Type: {obs.observation_type}\n"
        f"App: {obs.app_context or 'unknown'}\n"
        f"Department: {obs.proposed_department or 'unclassified'}\n"
        f"Role: {obs.proposed_role_key or 'unclassified'}\n"
        f"{entities_text}\n\n"
        f"Text:\n{obs.text}\n"
        f"{similar_context}"
        f"{nearby_context}"
    )

    result = await llm_chat(
        system_prompt=_EVAL_SYSTEM_PROMPT,
        user_message=user_message,
        max_tokens=4096,
        model=req.model,
    )

    eval_data = _parse_eval_response(result["text"])

    obs.eval_json = json.dumps(eval_data)
    obs.eval_model = req.model
    obs.eval_input_tokens = result.get("input_tokens", 0)
    obs.eval_output_tokens = result.get("output_tokens", 0)
    obs.status = "evaluated"
    await db.commit()

    return {
        "observation_id": obs.id,
        "model": req.model,
        "input_tokens": result.get("input_tokens", 0),
        "output_tokens": result.get("output_tokens", 0),
        "cost_usd": result.get("cost_usd", 0),
        **eval_data,
    }


@router.post("/observations/evaluate-batch")
async def evaluate_observation_batch(
    req: ObservationBatchEvalRequest, db: AsyncSession = Depends(get_db)
):
    """Evaluate multiple observations sequentially."""
    if len(req.observation_ids) > 20:
        raise HTTPException(status_code=400, detail="Max 20 observations per batch")

    results = []
    for obs_id in req.observation_ids:
        try:
            eval_req = ObservationEvalRequest(model=req.model)
            result = await evaluate_observation(obs_id, eval_req, db)
            results.append(result)
        except HTTPException as e:
            results.append({"observation_id": obs_id, "error": e.detail})
        except Exception as e:
            results.append({"observation_id": obs_id, "error": str(e)})

    return results


def _validate_and_parse_eval(obs: ObservationQueue) -> dict:
    assert obs.eval_json, "No evaluation data found"
    eval_data = json.loads(obs.eval_json)
    assert isinstance(eval_data, dict), "Stored eval_json must deserialize to a dict"
    action = eval_data.get("action", "dismiss")
    assert action in ("create", "update", "merge", "dismiss"), \
        f"eval action must be create/update/merge/dismiss, got {action!r}"
    return eval_data


async def _apply_selected_updates(
    db: AsyncSession, updates_list: list[dict], indices: list[int], obs_id: int
) -> int:
    updated_count = 0
    for idx in indices:
        if idx < 0 or idx >= len(updates_list):
            continue
        u = updates_list[idx]
        result = await action_bus.submit(
            db=db, kind="neuron.refine", actor=_SYSTEM_ACTOR,
            actor_type="system",
            input_data={
                "target_neuron_id": u["neuron_id"],
                "field": u["field"],
                "old_value": str(u.get("old_value", "")),
                "new_value": str(u["new_value"]),
                "reason": u.get("reason", f"Corvus observation #{obs_id}"),
            },
        )
        if result.state == "applied":
            updated_count += 1
    return updated_count


async def _apply_selected_new_neurons(
    db: AsyncSession, new_neurons_list: list[dict], indices: list[int],
    obs: ObservationQueue,
) -> tuple[int, list[int]]:
    created_count = 0
    created_neuron_ids: list[int] = []
    for idx in indices:
        if idx < 0 or idx >= len(new_neurons_list):
            continue
        n = new_neurons_list[idx]
        result = await action_bus.submit(
            db=db, kind="neuron.create", actor=_SYSTEM_ACTOR,
            actor_type="system",
            input_data={
                "spec": {
                    "parent_id": n.get("parent_id"),
                    "layer": n.get("layer", 3),
                    "node_type": n.get("node_type", "system"),
                    "label": n.get("label", ""),
                    "content": n.get("content", ""),
                    "summary": n.get("summary", ""),
                    "department": n.get("department") or obs.proposed_department,
                    "role_key": n.get("role_key") or obs.proposed_role_key,
                    "source_origin": "corvus",
                },
                "reason": n.get("reason", f"Corvus observation #{obs.id}"),
            },
        )
        if result.state == "applied":
            created_neuron_ids.append(result.payload["neuron_id"])
            created_count += 1
    return created_count, created_neuron_ids


async def _apply_merge(
    db: AsyncSession, merge_target_id: int, merge_content_delta: str, obs_id: int
) -> int:
    target = await db.get(Neuron, merge_target_id)
    if not target:
        return 0
    old_content = target.content or ""
    new_content = old_content + "\n\n" + merge_content_delta
    result = await action_bus.submit(
        db=db, kind="neuron.refine", actor=_SYSTEM_ACTOR,
        actor_type="system",
        input_data={
            "target_neuron_id": merge_target_id,
            "field": "content",
            "old_value": old_content,
            "new_value": new_content,
            "reason": f"Merged from Corvus observation #{obs_id}",
        },
    )
    return 1 if result.state == "applied" else 0


async def _strengthen_observation_entity_edges(
    db: AsyncSession, obs: ObservationQueue,
    created_neuron_ids: list[int], merge_target_id: int | None,
) -> None:
    try:
        entities = json.loads(obs.entities_json) if obs.entities_json else []
        if entities:
            anchor_id = (
                created_neuron_ids[0] if created_neuron_ids
                else (merge_target_id or obs.similar_neuron_id)
            )
            await _strengthen_entity_edges(db, entities, anchor_id)
    except Exception:
        pass


@router.post("/observations/{obs_id}/apply")
async def apply_observation(
    obs_id: int, req: ObservationApplyRequest, db: AsyncSession = Depends(get_db)
):
    """Apply evaluated proposals: create neurons, update fields, merge content."""
    obs = await db.get(ObservationQueue, obs_id)
    if not obs:
        raise HTTPException(status_code=404, detail="Observation not found")
    if obs.status != "evaluated":
        raise HTTPException(status_code=400, detail=f"Observation is {obs.status}, must be evaluated first")
    if not obs.eval_json:
        raise HTTPException(status_code=400, detail="No evaluation data found")

    eval_data = _validate_and_parse_eval(obs)
    action = eval_data.get("action", "dismiss")

    updated_count = await _apply_selected_updates(
        db, eval_data.get("updates", []), req.update_indices, obs.id,
    )
    created_count, created_neuron_ids = await _apply_selected_new_neurons(
        db, eval_data.get("new_neurons", []), req.new_neuron_indices, obs,
    )

    merged_count = 0
    merge_target_id = eval_data.get("merge_target_id")
    merge_content_delta = eval_data.get("merge_content_delta")
    if action == "merge" and merge_target_id and merge_content_delta:
        if not req.update_indices and not req.new_neuron_indices:
            merged_count = await _apply_merge(db, merge_target_id, merge_content_delta, obs.id)

    await _strengthen_observation_entity_edges(db, obs, created_neuron_ids, merge_target_id)

    obs.status = "approved"
    if created_neuron_ids:
        obs.created_neuron_id = created_neuron_ids[0]
    await db.commit()

    return {
        "observation_id": obs.id,
        "updated": updated_count,
        "created": created_count,
        "merged": merged_count,
        "created_neuron_ids": created_neuron_ids,
    }


async def _classify_observation(
    text: str, observation_type: str, app_context: str | None
) -> tuple[str | None, str | None]:
    """Use Haiku to classify an observation into department + role_key.

    Returns (department, role_key) or (None, None) on failure.
    """
    try:
        # LLM PROMPT INTENT: Classify a Corvus observation into an organizational department and
        #   role_key to determine where in the neuron graph hierarchy the observation belongs.
        #   This is a lightweight classification step (Haiku) run before semantic similarity search.
        # INPUT: User message contains the observation type, optional app context, and the first
        #   500 characters of the observation text.
        # OUTPUT FORMAT: Raw JSON object: {"department": str, "role_key": str}. Department is one
        #   of the enumerated valid departments. role_key is snake_case.
        # FAILURE MODES: If the LLM returns non-JSON or the call fails entirely, the except block
        #   returns (None, None), causing the observation to be classified as "unclassified" for
        #   both department and role. This is safe — the observation still proceeds through
        #   similarity search and queuing, just without department-filtered candidate narrowing.
        #   If the LLM returns a department not in the valid set, the downstream neuron filter
        #   query will simply return no matches for that department.
        system = (
            "You classify observations into organizational departments and roles. "
            "Respond with ONLY a JSON object: {\"department\": \"...\", \"role_key\": \"...\"}\n\n"
            "Valid departments: Engineering, Manufacturing, Quality, Contracts, Finance, "
            "Program Management, Executive Leadership, Regulatory, Operations\n\n"
            "Role keys are snake_case like: structures_engineer, process_engineer, "
            "quality_inspector, contracts_analyst, program_manager, etc."
        )

        user_msg = f"Observation type: {observation_type}\n"
        if app_context:
            user_msg += f"App: {app_context}\n"
        user_msg += f"Text: {text[:500]}"

        response = await llm_chat(
            system_prompt=system,
            user_message=user_msg,
            max_tokens=100,
            model="haiku",
        )

        import json
        result = json.loads(response["text"])
        return result.get("department"), result.get("role_key")
    except Exception as e:
        print(f"[Corvus] Observation classification failed: {e}")
        return None, None


async def _find_similar_neuron(
    db: AsyncSession, text: str, department: str | None
) -> tuple[Neuron | None, float | None]:
    """Find the most semantically similar existing neuron.

    Uses embedding similarity if available, falls back to keyword overlap.
    """
    try:
        from app.services.embedding_service import embed_text
        import concurrent.futures
        import asyncio
        import json
        import numpy as np

        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            query_embedding = await loop.run_in_executor(pool, embed_text, text[:500])

        if query_embedding is None:
            return None, None

        # Load candidate neurons (filter by department if available)
        query = select(Neuron.id, Neuron.label, Neuron.embedding).where(
            Neuron.is_active == True,
            Neuron.embedding.isnot(None),
            Neuron.layer >= 2,
        )
        if department:
            query = query.where(Neuron.department == department)
        query = query.limit(500)

        result = await db.execute(query)
        rows = result.all()

        if not rows:
            return None, None

        query_vec = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return None, None

        best_id = None
        best_sim = 0.0
        for nid, label, emb_json in rows:
            if not emb_json:
                continue
            emb = np.array(json.loads(emb_json), dtype=np.float32)
            emb_norm = np.linalg.norm(emb)
            if emb_norm == 0:
                continue
            sim = float(np.dot(query_vec, emb) / (query_norm * emb_norm))
            if sim > best_sim:
                best_sim = sim
                best_id = nid

        if best_id is None:
            return None, None

        neuron = await db.get(Neuron, best_id)
        return neuron, best_sim

    except Exception as e:
        print(f"[Corvus] Similarity search failed: {e}")
        return None, None


async def _strengthen_entity_edges(
    db: AsyncSession, entities: list[dict], anchor_neuron_id: int | None = None
) -> int:
    """Strengthen co-firing edges between neurons associated with the observed entities.

    Returns the number of edges strengthened.
    """
    if not entities:
        return 0

    # Extract entity values
    entity_values = [e.get("value", "") for e in entities if e.get("value")]
    if not entity_values:
        return 0

    # Find neurons whose content/label mentions these entities
    from sqlalchemy import text as sql_text

    neuron_ids = set()
    if anchor_neuron_id:
        neuron_ids.add(anchor_neuron_id)

    for val in entity_values[:10]:  # Limit to prevent excessive queries
        result = await db.execute(
            select(Neuron.id).where(
                Neuron.is_active == True,
                (Neuron.label.ilike(f"%{val}%")) | (Neuron.content.ilike(f"%{val}%")),
            ).limit(5)
        )
        for row in result.all():
            neuron_ids.add(row[0])

    if len(neuron_ids) < 2:
        return 0

    # Strengthen edges between all pairs (tiered: promoted table or JSONB)
    from app.services.edge_tier import (
        get_weak_edge, upsert_weak_edge, maybe_promote,
    )
    from sqlalchemy import text as sql_text
    id_list = sorted(neuron_ids)
    strengthened = 0
    for i, src in enumerate(id_list):
        for tgt in id_list[i + 1:]:
            # Try UPDATE in promoted table first
            result = await db.execute(sql_text(
                "UPDATE neuron_edges "
                "SET co_fire_count = co_fire_count + 1, "
                "    weight = LEAST(1.0, (co_fire_count + 1) / 20.0), "
                "    source = CASE WHEN source = 'bootstrap' THEN 'corvus' ELSE source END, "
                "    last_adjusted = now() "
                "WHERE source_id = :src AND target_id = :tgt "
                "RETURNING weight, co_fire_count"
            ), {"src": src, "tgt": tgt})
            row = result.one_or_none()
            if row:
                strengthened += 1
                continue
            # Not in table — handle in JSONB
            entry = await get_weak_edge(db, src, tgt)
            new_c = (entry.get("c", 0) + 1) if entry else 1
            new_w = min(1.0, (new_c + 1) / 20.0)
            data = {"w": new_w, "t": "pyramidal", "c": new_c, "s": "corvus", "q": 0}
            await upsert_weak_edge(db, src, tgt, data)
            await maybe_promote(db, src, tgt, new_w, new_c)
            strengthened += 1

    return strengthened
