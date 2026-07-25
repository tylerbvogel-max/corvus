"""Validation and summaries for project-scoped roadmap ledgers."""

from __future__ import annotations

import copy
import calendar
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any


LEDGER_STATUSES = frozenset({
    "done", "active", "in-progress", "planned", "proposed", "unblocked",
    "bug", "deprioritized", "polish", "conceptual", "cancelled",
})
OUT_OF_SCOPE_STATUSES = frozenset({"deprioritized", "cancelled"})
PLANNING_HORIZONS = frozenset({
    "thesis", "horizon-3", "horizon-2", "horizon-1", "active",
})
ASSUMPTION_STATUSES = frozenset({
    "standing", "supported", "challenged", "invalidated",
})
REVIEW_CADENCE_MONTHS = {
    "monthly": 1,
    "quarterly": 3,
    "semiannual": 6,
    "annual": 12,
}
REVIEW_CADENCES = frozenset({*REVIEW_CADENCE_MONTHS, "event", "manual"})
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,119}$")
RECORD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:120]
    if not slug or not SLUG_RE.fullmatch(slug):
        raise ValueError("name must contain letters or numbers")
    return slug


def empty_state() -> dict[str, Any]:
    return {
        "version": 1,
        "updatedAt": utc_now(),
        "sections": [{
            "id": "roadmap",
            "label": "Roadmap",
            "color": "#3987e5",
        }],
        "nodes": [],
        "edges": [],
        "milestones": [],
    }


def _record_id(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not RECORD_ID_RE.fullmatch(value):
        raise ValueError(
            f"{field} must be 1-160 letters, numbers, dots, colons, underscores, or hyphens"
        )
    return value


def _require_label(value: Any, *, field: str, max_length: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    if len(value) > max_length:
        raise ValueError(f"{field} must be at most {max_length} characters")
    return value


def _optional_datetime(value: Any, *, field: str) -> None:
    if value in (None, ""):
        return
    if not isinstance(value, str):
        raise ValueError(f"{field} must be an ISO date or timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO date or timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)


def _validate_assumptions(node: dict[str, Any], *, index: int) -> None:
    assumptions = node.get("assumptions", [])
    if not isinstance(assumptions, list) or len(assumptions) > 100:
        raise ValueError(f"nodes[{index}].assumptions must be an array of at most 100 items")
    assumption_ids: set[str] = set()
    for assumption_index, assumption in enumerate(assumptions):
        field = f"nodes[{index}].assumptions[{assumption_index}]"
        if not isinstance(assumption, dict):
            raise ValueError(f"{field} must be an object")
        assumption_id = _record_id(assumption.get("id"), field=f"{field}.id")
        if assumption_id in assumption_ids:
            raise ValueError(f"duplicate assumption id on node {node['id']}: {assumption_id}")
        assumption_ids.add(assumption_id)
        _require_label(
            assumption.get("statement"), field=f"{field}.statement", max_length=2000,
        )
        status = assumption.get("status", "standing")
        if status not in ASSUMPTION_STATUSES:
            raise ValueError(f"{field}.status is unsupported")
        confidence = assumption.get("confidence", 50)
        if (
            not isinstance(confidence, int)
            or isinstance(confidence, bool)
            or not 0 <= confidence <= 100
        ):
            raise ValueError(f"{field}.confidence must be an integer from 0 to 100")
        for evidence_field in ("evidenceFor", "evidenceAgainst"):
            evidence = assumption.get(evidence_field, [])
            if (
                not isinstance(evidence, list)
                or len(evidence) > 100
                or not all(isinstance(item, str) and item.strip() for item in evidence)
            ):
                raise ValueError(f"{field}.{evidence_field} must contain non-empty text")
        for text_field in ("invalidationTrigger", "consequence"):
            text = assumption.get(text_field)
            if text is not None and (
                not isinstance(text, str) or len(text) > 4000
            ):
                raise ValueError(f"{field}.{text_field} must be text under 4000 characters")
        _optional_datetime(
            assumption.get("lastValidatedAt"), field=f"{field}.lastValidatedAt",
        )


def validate_state(raw: Any) -> dict[str, Any]:
    """Return a detached, JSON-safe roadmap document or raise ``ValueError``."""
    if not isinstance(raw, dict):
        raise ValueError("state must be an object")
    try:
        state = json.loads(json.dumps(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError("state must contain only JSON values") from exc

    version = state.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError("state.version must be a positive integer")

    sections = state.get("sections")
    nodes = state.get("nodes")
    edges = state.get("edges", [])
    milestones = state.get("milestones", [])
    if not isinstance(sections, list) or not sections:
        raise ValueError("state.sections must contain at least one section")
    if not isinstance(nodes, list):
        raise ValueError("state.nodes must be an array")
    if not isinstance(edges, list):
        raise ValueError("state.edges must be an array")
    if not isinstance(milestones, list):
        raise ValueError("state.milestones must be an array")
    if len(sections) > 200 or len(nodes) > 5000 or len(edges) > 20000:
        raise ValueError("roadmap exceeds the supported size")

    section_ids: set[str] = set()
    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            raise ValueError(f"sections[{index}] must be an object")
        section_id = _record_id(section.get("id"), field=f"sections[{index}].id")
        _require_label(section.get("label"), field=f"sections[{index}].label", max_length=200)
        if section_id in section_ids:
            raise ValueError(f"duplicate section id: {section_id}")
        section_ids.add(section_id)

    node_ids: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            raise ValueError(f"nodes[{index}] must be an object")
        node_id = _record_id(node.get("id"), field=f"nodes[{index}].id")
        _require_label(node.get("label"), field=f"nodes[{index}].label")
        if node_id in node_ids:
            raise ValueError(f"duplicate node id: {node_id}")
        if node.get("section") not in section_ids:
            raise ValueError(f"node {node_id} references unknown section {node.get('section')!r}")
        if node.get("status") not in LEDGER_STATUSES:
            raise ValueError(f"node {node_id} has unsupported status {node.get('status')!r}")
        horizon = node.get("horizon")
        if horizon is not None and horizon not in PLANNING_HORIZONS:
            raise ValueError(f"node {node_id} has unsupported horizon {horizon!r}")
        cadence = node.get("reviewCadence")
        if cadence is not None and cadence not in REVIEW_CADENCES:
            raise ValueError(f"node {node_id} has unsupported review cadence {cadence!r}")
        _optional_datetime(
            node.get("nextReviewAt"), field=f"nodes[{index}].nextReviewAt",
        )
        _optional_datetime(
            node.get("lastReviewedAt"), field=f"nodes[{index}].lastReviewedAt",
        )
        _validate_assumptions(node, index=index)
        prereqs = node.get("prereqs", [])
        if not isinstance(prereqs, list) or not all(isinstance(x, str) for x in prereqs):
            raise ValueError(f"node {node_id} prereqs must be an array of record ids")
        node_ids.add(node_id)

    for node in nodes:
        node_id = node["id"]
        for prereq in node.get("prereqs", []):
            if prereq not in node_ids:
                raise ValueError(f"node {node_id} references unknown prerequisite {prereq}")
            if prereq == node_id:
                raise ValueError(f"node {node_id} cannot depend on itself")

    for index, edge in enumerate(edges):
        if not isinstance(edge, dict):
            raise ValueError(f"edges[{index}] must be an object")
        source, target = edge.get("from"), edge.get("to")
        if source not in node_ids or target not in node_ids:
            raise ValueError(f"edge {source!r} → {target!r} must reference known records")

    milestone_ids: set[str] = set()
    for index, milestone in enumerate(milestones):
        if not isinstance(milestone, dict):
            raise ValueError(f"milestones[{index}] must be an object")
        milestone_id = _record_id(milestone.get("id"), field=f"milestones[{index}].id")
        _require_label(milestone.get("label"), field=f"milestones[{index}].label")
        if milestone_id in milestone_ids:
            raise ValueError(f"duplicate milestone id: {milestone_id}")
        prereqs = milestone.get("prereqs", [])
        if not isinstance(prereqs, list) or any(x not in node_ids for x in prereqs):
            raise ValueError(f"milestone {milestone_id} references an unknown record")
        milestone_ids.add(milestone_id)

    return copy.deepcopy(state)


def review_status(
    node: dict[str, Any], *, now: datetime | None = None,
) -> str:
    if node.get("status") in {"done", *OUT_OF_SCOPE_STATUSES}:
        return "retired"
    cadence = node.get("reviewCadence")
    next_review = node.get("nextReviewAt")
    if not next_review:
        return cadence if cadence in {"event", "manual"} else "unscheduled"
    parsed = datetime.fromisoformat(str(next_review).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if parsed <= current:
        return "due"
    if parsed <= current + timedelta(days=30):
        return "upcoming"
    return "current"


def next_review_at(
    node: dict[str, Any], *, from_time: datetime | None = None,
) -> str | None:
    months = REVIEW_CADENCE_MONTHS.get(node.get("reviewCadence"))
    if months is None:
        return None
    current = from_time or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    month_index = current.month - 1 + months
    year = current.year + month_index // 12
    month = month_index % 12 + 1
    day = min(current.day, calendar.monthrange(year, month)[1])
    return current.replace(year=year, month=month, day=day).isoformat()


def state_summary(state: dict[str, Any]) -> dict[str, Any]:
    nodes = state.get("nodes", [])
    in_scope = [n for n in nodes if n.get("status") not in OUT_OF_SCOPE_STATUSES]
    done = sum(n.get("status") == "done" for n in in_scope)
    moving = sum(n.get("status") in {"active", "in-progress"} for n in in_scope)
    assumptions = [
        assumption
        for node in in_scope
        for assumption in node.get("assumptions", [])
    ]
    review_states = [review_status(node) for node in in_scope]
    return {
        "records": len(nodes),
        "in_scope": len(in_scope),
        "out_of_scope": len(nodes) - len(in_scope),
        "done": done,
        "moving": moving,
        "completion": round(done / len(in_scope) * 100) if in_scope else 0,
        "assumptions": len(assumptions),
        "challenged_assumptions": sum(
            item.get("status") in {"challenged", "invalidated"}
            for item in assumptions
        ),
        "reviews_due": review_states.count("due"),
        "reviews_upcoming": review_states.count("upcoming"),
        "horizons": {
            horizon: sum(node.get("horizon") == horizon for node in in_scope)
            for horizon in sorted(PLANNING_HORIZONS)
        },
        "unclassified_horizon": sum(not node.get("horizon") for node in in_scope),
    }


def advance_state(raw: Any, previous_version: int) -> dict[str, Any]:
    state = validate_state(raw)
    state["version"] = max(previous_version + 1, int(state["version"]))
    state["updatedAt"] = utc_now()
    return state


def node_is_ready(state: dict[str, Any], node: dict[str, Any]) -> bool:
    if node.get("status") in {"done", *OUT_OF_SCOPE_STATUSES}:
        return False
    by_id = {item["id"]: item for item in state.get("nodes", [])}
    return all(by_id.get(prereq, {}).get("status") == "done"
               for prereq in node.get("prereqs", []))


def compile_work_order_node(
    *,
    ledger_slug: str,
    ledger_revision: int,
    source_version: int,
    node: dict[str, Any],
    task_class: str,
    risk_tier: int,
) -> dict[str, Any]:
    """Compile one human roadmap record into an Agency Lab contract node."""
    acceptance = node.get("verification", [])
    if not isinstance(acceptance, list) or not acceptance:
        raise ValueError("record needs a verification checklist before commissioning")
    if not all(isinstance(item, str) and item.strip() for item in acceptance):
        raise ValueError("verification checklist must contain non-empty text")
    if not 1 <= risk_tier <= 5:
        raise ValueError("risk tier must be between 1 and 5")
    if not task_class.strip():
        raise ValueError("task class is required")
    return {
        "id": node["id"],
        "title": node["label"],
        "outcome": node.get("summary") or f"Deliver {node['label']}",
        "acceptance": acceptance,
        "risk_tier": risk_tier,
        "task_class": task_class.strip(),
        "kickoff_prompt": node.get("prompt"),
        "roadmap_ledger_slug": ledger_slug,
        "roadmap_ledger_revision": ledger_revision,
        "roadmap_source_version": source_version,
        "roadmap_work_kind": "delivery",
    }


def compile_review_node(
    *,
    ledger_slug: str,
    ledger_revision: int,
    source_version: int,
    node: dict[str, Any],
    risk_tier: int,
) -> dict[str, Any]:
    """Compile one strategic record into an evidence-gathering review contract."""
    assumptions = node.get("assumptions", [])
    if not assumptions:
        raise ValueError("record needs at least one explicit assumption before review")
    if not 1 <= risk_tier <= 5:
        raise ValueError("risk tier must be between 1 and 5")
    assumption_lines = "\n".join(
        (
            f"- [{item.get('status', 'standing')}; confidence "
            f"{item.get('confidence', 50)}%] {item['statement']}\n"
            f"  Invalidation trigger: {item.get('invalidationTrigger') or 'not recorded'}"
        )
        for item in assumptions
    )
    return {
        "id": node["id"],
        "title": f"Strategic review — {node['label']}",
        "outcome": (
            "Reassess the record's assumptions against current evidence and recommend "
            "retain, revise, defer, or retire. Do not mutate the roadmap."
        ),
        "acceptance": [
            "Every standing, supported, or challenged assumption is addressed",
            "Supporting and contradicting evidence are separated and cited",
            "Each invalidation trigger is explicitly evaluated",
            "Recommendation is retain, revise, defer, or retire with limitations disclosed",
        ],
        "risk_tier": risk_tier,
        "task_class": "strategic-review",
        "kickoff_prompt": (
            f"Review roadmap record {node['id']} — {node['label']}.\n"
            f"Horizon: {node.get('horizon') or 'unclassified'}.\n"
            f"Summary: {node.get('summary') or 'No summary recorded.'}\n\n"
            f"ASSUMPTIONS\n{assumption_lines}\n\n"
            "Return evidence and a recommendation only. Human acceptance controls any "
            "durable strategic change."
        ),
        "roadmap_ledger_slug": ledger_slug,
        "roadmap_ledger_revision": ledger_revision,
        "roadmap_source_version": source_version,
        "roadmap_work_kind": "strategic-review",
        "roadmap_review_cadence": node.get("reviewCadence"),
        "roadmap_next_review_at": node.get("nextReviewAt"),
        "roadmap_assumptions": copy.deepcopy(assumptions),
    }
