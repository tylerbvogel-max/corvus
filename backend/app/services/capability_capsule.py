"""Harness-neutral Corvus Capability Capsule.

The graph remains source of truth.  A capsule is a signed, deterministic
projection for transport, inspection, and harness compilation; it is never a
database backup and never creates an unguarded write path.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Neuron

SCHEMA = "corvus.capability-capsule"
SCHEMA_VERSION = "1.0.0"
PORTABILITY_SCOPES = ("universal", "user", "organization", "project", "machine", "harness")
CAPSULE_DIR = Path(os.path.expanduser("~/.corvus-mind/capsules"))
CANONICAL_SKILLS_DIR = Path(os.path.expanduser("~/.corvus-mind/capabilities/skills"))
HMAC_ENV = "CORVUS_CAPSULE_SIGNING_KEY"

_REGION_SCOPE = {
    "Assistant": "universal",
    "User": "user",
    "Projects": "project",
    "Environment": "machine",
    "Harness": "harness",
    "Reference Library": "organization",
}


def portability_scope(neuron: Neuron) -> str:
    """Conservative boundary: unknown regions do not escape organization."""
    return _REGION_SCOPE.get(neuron.department or "", "organization")


def stable_memory_id(item: dict[str, Any]) -> str:
    identity = {
        "kind": item.get("kind"), "label": item.get("label"),
        "scope": item.get("portability_scope"),
        "project": item.get("project"),
    }
    raw = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "mem_" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def memory_content_hash(item: dict[str, Any]) -> str:
    value = {"content": item.get("content"), "summary": item.get("summary"),
             "authority_level": item.get("authority_level"),
             "superseded_by": item.get("superseded_by")}
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def canonical_bytes(capsule: dict[str, Any], include_integrity: bool = False) -> bytes:
    value = capsule if include_integrity else {k: v for k, v in capsule.items() if k != "integrity"}
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def seal(capsule: dict[str, Any], signing_key: str | None = None) -> dict[str, Any]:
    digest = hashlib.sha256(canonical_bytes(capsule)).hexdigest()
    key = signing_key if signing_key is not None else os.getenv(HMAC_ENV)
    integrity: dict[str, Any] = {"algorithm": "sha256", "digest": digest}
    if key:
        integrity.update({"signature_algorithm": "hmac-sha256",
                          "signature": hmac.new(key.encode(), digest.encode(), hashlib.sha256).hexdigest()})
    capsule["integrity"] = integrity
    return capsule


def verify(capsule: dict[str, Any], signing_key: str | None = None) -> dict[str, Any]:
    errors: list[str] = []
    if capsule.get("schema") != SCHEMA:
        errors.append("unsupported schema")
    if capsule.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported schema version")
    integrity = capsule.get("integrity") or {}
    actual = hashlib.sha256(canonical_bytes(capsule)).hexdigest()
    if not hmac.compare_digest(str(integrity.get("digest", "")), actual):
        errors.append("digest mismatch")
    key = signing_key if signing_key is not None else os.getenv(HMAC_ENV)
    signature = integrity.get("signature")
    if signature:
        if not key:
            errors.append("signature present but no verification key configured")
        else:
            expected = hmac.new(key.encode(), actual.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(str(signature), expected):
                errors.append("signature mismatch")
    return {"valid": not errors, "errors": errors, "digest": actual,
            "signed": bool(signature)}


def load_harness_profiles() -> dict[str, dict[str, Any]]:
    root = Path(__file__).resolve().parents[3] / "harness" / "profiles"
    profiles: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        profiles[data["id"]] = data
    return profiles


def _skill_manifest() -> list[dict[str, Any]]:
    path = Path(os.path.expanduser("~/.corvus-mind/compiled-skills.json"))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, list) else []
    except (OSError, ValueError):
        return []


async def export_capsule(db: AsyncSession, *, include_scopes: set[str] | None = None,
                         source_instance: str = "corvus-mind") -> dict[str, Any]:
    allowed = include_scopes or set(PORTABILITY_SCOPES)
    if not allowed <= set(PORTABILITY_SCOPES):
        raise ValueError("unknown portability scope")
    rows = (await db.execute(select(Neuron).where(
        Neuron.is_active.is_(True), Neuron.superseded_by.is_(None), Neuron.node_type.in_((
            "lesson", "tool-profile", "context-scope", "reference"))))).scalars().all()
    parent_ids = {n.parent_id for n in rows if n.parent_id}
    project_by_parent: dict[int, str] = {}
    if parent_ids:
        project_rows = (await db.execute(select(Neuron.id, Neuron.label).where(
            Neuron.id.in_(parent_ids), Neuron.node_type == "project"))).all()
        project_by_parent = {row.id: row.label for row in project_rows}
    memories: list[dict[str, Any]] = []
    for n in rows:
        pscope = portability_scope(n)
        if pscope not in allowed:
            continue
        item = {
            "source_neuron_id": n.id, "kind": n.node_type, "label": n.label,
            "content": n.content, "summary": n.summary, "region": n.department,
            "portability_scope": pscope, "project": project_by_parent.get(n.parent_id),
            "authority_level": n.authority_level, "utility": n.avg_utility,
            "source_origin": n.source_origin, "source_version": n.source_version,
            "created_at": n.created_at.isoformat() if n.created_at else None,
            "last_verified": n.last_verified.isoformat() if n.last_verified else None,
            "superseded_by": n.superseded_by,
        }
        item["memory_id"] = stable_memory_id(item)
        item["content_hash"] = memory_content_hash(item)
        memories.append(item)
    memories.sort(key=lambda x: x["memory_id"])
    capsule = {
        "schema": SCHEMA, "schema_version": SCHEMA_VERSION,
        "capsule_id": "cap_" + hashlib.sha256("|".join(x["memory_id"] for x in memories).encode()).hexdigest()[:24],
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_instance": source_instance,
        "identity": [x["memory_id"] for x in memories if x["region"] == "Assistant"],
        "governance": [x["memory_id"] for x in memories if x.get("authority_level") in ("guidance", "organizational")],
        "memories": memories,
        "skills": _skill_manifest(),
        "capability_contract": {
            "semantic_capabilities": ["filesystem", "hooks", "mcp"],
            "lifecycle_events": ["session_start", "prompt_submit", "pre_tool", "post_tool", "stop"],
        },
        "tool_semantics": {"vocabulary": "corvus.semantic-tools.v1"},
        "harness_profiles": load_harness_profiles(),
    }
    return seal(capsule)


def reconcile(capsule: dict[str, Any], local_memory_ids: set[str] | dict[str, str], *, target_harness: str) -> dict[str, Any]:
    check = verify(capsule)
    profiles = load_harness_profiles()
    profile = profiles.get(target_harness)
    incoming_items = {m.get("memory_id"): m for m in capsule.get("memories", [])}
    incoming = set(incoming_items)
    local_index = ({key: "" for key in local_memory_ids} if isinstance(local_memory_ids, set)
                   else local_memory_ids)
    local_ids = set(local_index)
    conflicts = sorted(mid for mid in incoming & local_ids
                       if local_index.get(mid) and incoming_items[mid].get("content_hash")
                       and local_index[mid] != incoming_items[mid].get("content_hash"))
    contract = capsule.get("capability_contract", {})
    required = set(contract.get("semantic_capabilities", []))
    required |= set().union(*(set(s.get("requires", [])) for s in capsule.get("skills", [])))
    available = set((profile or {}).get("semantic_capabilities", []))
    required_events = set(contract.get("lifecycle_events", []))
    available_events = set((profile or {}).get("lifecycle_events", []))
    return {
        "integrity": check, "target_harness": target_harness,
        "harness_known": profile is not None,
        "memory": {"incoming": len(incoming), "already_present": len(incoming & local_ids),
                   "new": len(incoming - local_ids), "conflicts": conflicts},
        "capabilities": {"required": sorted(required), "available": sorted(available),
                         "missing": sorted(required - available)},
        "lifecycle": {"required": sorted(required_events), "available": sorted(available_events),
                      "missing": sorted(required_events - available_events)},
        "apply_allowed": check["valid"] and profile is not None,
    }


def transfer_health(capsule: dict[str, Any], target_harness: str) -> dict[str, Any]:
    profile = load_harness_profiles().get(target_harness)
    memories = capsule.get("memories", [])
    dimensions = {
        "integrity": 1.0 if verify(capsule)["valid"] else 0.0,
        "identity": 1.0 if capsule.get("identity") else 0.0,
        "governance": 1.0 if capsule.get("governance") else 0.0,
        "memory": 1.0 if memories else 0.0,
        "skills": 1.0 if capsule.get("skills") else 0.0,
        "harness": 1.0 if profile else 0.0,
    }
    if profile:
        contract = capsule.get("capability_contract", {})
        required = set(contract.get("semantic_capabilities", []))
        required |= set().union(*(set(x.get("requires", [])) for x in capsule.get("skills", [])))
        available = set(profile.get("semantic_capabilities", []))
        dimensions["tool_coverage"] = 1.0 if not required else len(required & available) / len(required)
        required_events = set(contract.get("lifecycle_events", []))
        available_events = set(profile.get("lifecycle_events", []))
        dimensions["lifecycle_coverage"] = (1.0 if not required_events else
                                             len(required_events & available_events) / len(required_events))
    else:
        dimensions["tool_coverage"] = 0.0
        dimensions["lifecycle_coverage"] = 0.0
    score = round(100 * sum(dimensions.values()) / len(dimensions), 1)
    return {"target_harness": target_harness, "score": score,
            "status": "full" if score == 100 else "degraded", "dimensions": dimensions}


async def local_memory_ids(db: AsyncSession) -> dict[str, str]:
    local = await export_capsule(db)
    return {m["memory_id"]: m["content_hash"] for m in local["memories"]}


async def import_capsule(db: AsyncSession, capsule: dict[str, Any], *,
                         target_harness: str, apply: bool = False) -> dict[str, Any]:
    """Reconcile and optionally gate-import novel experiential memories.

    Identity/governance are deliberately never auto-committed. They arrive as
    informational lessons and must earn or receive countersigned promotion by
    the existing janitor/review machinery.
    """
    from app.services.evidence_frame import EvidenceFrameError
    from app.services.lesson_store import save_lesson

    existing = await local_memory_ids(db)
    plan = reconcile(capsule, existing, target_harness=target_harness)
    if not apply or not plan["apply_allowed"]:
        plan["applied"] = []
        return plan
    applied: list[dict[str, Any]] = []
    digest = plan["integrity"]["digest"]
    for item in capsule.get("memories", []):
        if item.get("memory_id") in existing:
            continue
        # Machine and harness facts are meaningful only in their original
        # embodiment. Transport them in the capsule, but never bind them to a
        # different body automatically.
        if item.get("portability_scope") in ("machine", "harness"):
            applied.append({"memory_id": item.get("memory_id"), "status": "withheld-embodiment"})
            continue
        # EVIDENCE FRAME (mind-neuron-evidence-frame): a capsule from a
        # framed graph transports its frames intact. A capsule from a
        # legacy graph carries prose we cannot frame without inventing the
        # slots the original author never wrote — so it is withheld and
        # reported, never silently imported as an unframed durable memory.
        try:
            result = await save_lesson(
                db, lesson=(item.get("content") or item.get("summary") or "").strip(),
                evidence=f"Capability Capsule {digest}; source neuron {item.get('source_neuron_id')}",
                label=str(item.get("label") or "Imported memory")[:200],
                scope=item.get("region"), node_type=item.get("kind") or "lesson",
                abstraction_type="principle", summary=item.get("summary"),
                authority_level="informational", project=item.get("project"),
                source_origin="capability_capsule",
            )
        except EvidenceFrameError as exc:
            applied.append({"memory_id": item.get("memory_id"),
                            "status": "withheld-unframed",
                            "violations": exc.errors})
            continue
        applied.append({"memory_id": item.get("memory_id"), "status": "gated", "result": result})
    plan["applied"] = applied
    return plan
