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
# Commit-sha-shaped tokens in free-text evidence. Abbreviated shas start at 7
# characters, so shorter hex (counts, sizes, dates) cannot match.  Real
# receipts also carry non-commit hex — fixture sha256 digests, for one — so
# this only NOMINATES candidates; resolution against the repository decides.
COMMIT_TOKEN_RE = re.compile(r"\b[0-9a-fA-F]{7,40}\b")
# A checked receipt says which: every claim resolved, or there was no
# repository to resolve against.  Silence is never a pass.
COMMIT_VERIFICATION_STATES = frozenset({"verified", "unverifiable-no-repo"})
MAX_COMMIT_CANDIDATES = 20


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


def extract_commit_candidates(evidence: list[str]) -> list[str]:
    """Nominate commit-sha-shaped tokens from free-text evidence, in order.

    Deliberately generous: evidence is prose written by coding agents, and a
    receipt that names its commit as "commit 8cc5db3 on wt/pretooluse-reach"
    should not have to learn a syntax.  Over-nomination is safe because the
    repository, not this function, decides what is real — and a receipt only
    needs ONE claim to resolve.
    """
    seen: dict[str, None] = {}
    for entry in evidence:
        for match in COMMIT_TOKEN_RE.findall(entry or ""):
            seen.setdefault(match.lower(), None)
            if len(seen) >= MAX_COMMIT_CANDIDATES:
                return list(seen)
    return list(seen)


def reconcile_node(
    node: dict[str, Any],
    *,
    ledger_revision: int,
    disposition: str,
    result_recap: str,
    verification_passed: bool,
    confidence: float,
    claims: list[str],
    limitations: list[str],
    disclosures: list[str],
    evidence: list[str],
    verifier: str,
    accepted_by: str,
    next_action: str | None = None,
    accepted_at: datetime | None = None,
    evidence_commits: list[str] | None = None,
    commit_verification: str | None = None,
) -> dict[str, Any]:
    """Attach a human-accepted verification receipt directly to one record.

    Execution identity, permissions, and worker selection belong to the coding
    harness.  The ledger keeps only the durable return contract: what was
    claimed, what evidence supports it, what remains limited, who verified it,
    and who accepted it into forward state.

    ``evidence_commits`` are the commits the caller RESOLVED against the
    ledger's repository; ``commit_verification`` says whether resolution
    happened at all.  Callers that skip both still get the structural check —
    verified completion must name a commit — because a naive caller must not
    be able to mint a ``done`` that no history backs.
    """
    if disposition not in {"complete", "partial", "failed", "blocked"}:
        raise ValueError("unsupported reconciliation disposition")
    if not result_recap.strip():
        raise ValueError("result recap is required")
    if not verifier.strip() or not accepted_by.strip():
        raise ValueError("verifier and accepting human are required")
    if verifier.strip().casefold() == accepted_by.strip().casefold():
        raise ValueError("verifier must be independent from the accepting human")
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")
    if commit_verification is not None and commit_verification not in COMMIT_VERIFICATION_STATES:
        raise ValueError("unsupported commit verification state")

    verified_completion = verification_passed and disposition == "complete"
    # Only verified completion is gated.  An honest partial/failed/blocked
    # report often has no commit precisely BECAUSE the work did not land, and
    # filing one must stay cheap or the ledger learns to lie.
    if verified_completion:
        checklist = node.get("verification", [])
        if not isinstance(checklist, list) or not checklist:
            raise ValueError("verified completion requires a verification checklist")
        if not evidence:
            raise ValueError("verified completion requires evidence")
        claimed = (list(evidence_commits) if evidence_commits is not None
                   else extract_commit_candidates(evidence))
        if not claimed:
            raise ValueError(
                "verified completion must name a commit in its evidence: no "
                "commit-sha-shaped token (7-40 hex characters) was found. A "
                "record is not done while its work exists only in a working tree."
            )
    else:
        claimed = list(evidence_commits) if evidence_commits else []

    timestamp = accepted_at or datetime.now(timezone.utc)
    receipt = {
        # v2 adds evidenceCommits + commitVerification. Receipts are durable,
        # so the shape names itself: a v1 receipt predates the commit gate and
        # its silence about commits is history, not a passed check.
        "schema": "corvus.roadmap-reconciliation/v2",
        "acceptedAt": timestamp.isoformat(),
        "acceptedBy": accepted_by.strip(),
        "verifier": verifier.strip(),
        "ledgerRevision": ledger_revision,
        "disposition": disposition,
        "verificationPassed": verification_passed,
        "confidence": confidence,
        "claims": copy.deepcopy(claims),
        "limitations": copy.deepcopy(limitations),
        "disclosures": copy.deepcopy(disclosures),
        "evidence": copy.deepcopy(evidence),
        "evidenceCommits": claimed,
        "commitVerification": commit_verification,
        "nextAction": next_action.strip() if next_action and next_action.strip() else None,
    }
    reconciled = copy.deepcopy(node)
    history = list(reconciled.get("reconciliationHistory", []))
    history.append(receipt)
    reconciled.update({
        "disposition": disposition,
        "resultRecap": result_recap.strip(),
        "verificationResults": receipt,
        "reconciliationHistory": history,
    })
    if verification_passed and disposition == "complete":
        reconciled["status"] = "done"
        reconciled["completedAt"] = timestamp.isoformat()
    return reconciled
