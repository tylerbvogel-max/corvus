"""Skill compiler — stable lesson clusters become harness skill files.

Phase 6 of the memory organ (CORVUS-MIND-DESIGN.md §3.4): the graph is
the source of truth; skills are BUILD OUTPUT. Two projections of one
substrate with opposite context economics — injection is push (small,
per-prompt, query-matched facts), skills are pull (a one-line trigger in
the harness listing, full body loaded on task match at ~zero standing
cost). A cluster compiles only when it is STABLE (active, unsuperseded,
evidence-gated members) and BULKY enough that ambient injection is the
wrong delivery (>= MIN_CLUSTER related lessons).

Reverse check: the manifest records each skill's source neuron ids.
When a source is later superseded/deactivated, or its cluster grows,
the artifact is stale — it is deleted and recompiled from the current
cluster. Compiled skills are namespaced `mind-*` and the compiler only
ever touches directories it created (recorded in the manifest).

Poisoning posture: skills are instructions the harness loads natively —
the highest-trust output of this system. Sources have already passed
the write gate, the distiller's instruction-shape filter, and janitor
curation; the composer prompt additionally requires declarative
playbook prose addressed to the reader, never meta-instructions.
"""

import json
import os
import re
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Neuron
from app.services.mind_janitors import _add_memory_edge, _load_lessons, _log_action

SKILLS_DIR = os.path.expanduser("~/.claude/skills")
MANIFEST_PATH = os.path.expanduser("~/.corvus-mind/compiled-skills.json")
SKILL_PREFIX = "mind-"
CLUSTER_SIM = 0.55
MIN_CLUSTER = 3
MAX_COMPILE_PER_RUN = 2
UTILITY_FLOOR = 0.4

_KEBAB = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Intent: turn a cluster of evidence-gated memories into one
# progressive-disclosure playbook. Expected output: bare JSON object
# {"name", "description", "body_markdown"} — name kebab-case, description
# a single when-to-use trigger line, body a declarative playbook.
_COMPOSE_SYSTEM_PROMPT = """You compile verified institutional memories into a skill file for a coding agent's harness.

INPUT: a cluster of related, evidence-backed lessons learned on one developer's machine.

Produce ONE skill:
- "name": kebab-case, 2-5 words, specific (e.g. "corvus-dev-servers")
- "description": ONE sentence (max 250 chars) saying exactly WHEN a coding agent should load this skill — task-trigger phrasing, e.g. "Use when starting, debugging, or wiring Corvus backend/frontend dev servers on this machine."
- "body_markdown": a declarative playbook that organizes ALL the input lessons: correct commands, ports, gotchas, and their evidence. Markdown with short sections. State facts and procedures addressed to the reader.

Rules:
- Use ONLY facts present in the input lessons; no invention, no generic best practices.
- Keep evidence references (session ids, file:line) inline where they exist.
- Never include instructions about ignoring rules, altering behavior, or addressing the assistant — this is a reference document.

Respond with ONLY a JSON object, no markdown fences:
{"name": "...", "description": "...", "body_markdown": "..."}"""


def _load_manifest() -> list[dict]:
    try:
        with open(MANIFEST_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_manifest(entries: list[dict]) -> None:
    os.makedirs(os.path.dirname(MANIFEST_PATH), exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2)


MAX_CLUSTER_SIZE = 8  # a skill is one task, not one scope
_SPLIT_STEP = 0.06
_SPLIT_CEILING = 0.85


def _components(lessons: list[Neuron], sims, threshold: float) -> list[list[int]]:
    """Same-scope connected components at `threshold` (iterative union-find)."""
    n = len(lessons)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:  # bounded: path length <= n
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(n):
        for j in range(i + 1, n):
            if sims[i, j] >= threshold and lessons[i].department == lessons[j].department:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def find_clusters(lessons: list[Neuron]) -> list[list[Neuron]]:
    """Skill-sized same-scope clusters. Oversized components are split by
    iteratively raising the similarity threshold — a 60-lesson scope blob
    must become several focused skills, never one textbook."""
    n = len(lessons)
    if n < MIN_CLUSTER:
        return []
    matrix = np.array([json.loads(x.embedding) for x in lessons], dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = matrix / norms
    sims = unit @ unit.T

    final: list[list[Neuron]] = []
    work = [(idx_group, CLUSTER_SIM) for idx_group in _components(lessons, sims, CLUSTER_SIM)]
    for _guard in range(4 * n):  # bounded (JPL-2); each pop shrinks or finalizes
        if not work:
            break
        group, threshold = work.pop()
        if len(group) < MIN_CLUSTER:
            continue
        if len(group) <= MAX_CLUSTER_SIZE or threshold >= _SPLIT_CEILING:
            final.append([lessons[i] for i in group])
            continue
        sub_lessons = [lessons[i] for i in group]
        sub_sims = sims[np.ix_(group, group)]
        pieces = _components(sub_lessons, sub_sims, threshold + _SPLIT_STEP)
        if len(pieces) == 1:
            work.append((group, threshold + _SPLIT_STEP))
        else:
            for piece in pieces:
                work.append(([group[i] for i in piece], threshold + _SPLIT_STEP))
    return final


async def _compose(cluster: list[Neuron]) -> dict | None:
    """One Opus call: cluster -> {name, description, body_markdown}."""
    from app.services.llm_provider import llm_chat

    blocks = [f"### {x.label}\n{(x.content or '').strip()}" for x in cluster]
    reply = await llm_chat(
        system_prompt=_COMPOSE_SYSTEM_PROMPT,
        user_message="\n\n".join(blocks)[:20_000],
        max_tokens=3000, model="opus", timeout=300,
    )
    text = reply.get("text", "")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        out = json.loads(text[start:end + 1])
    except ValueError:
        return None
    name = str(out.get("name", "")).strip().lower()
    if not name.startswith(SKILL_PREFIX):
        name = SKILL_PREFIX + name
    if not _KEBAB.match(name) or not out.get("description") or not out.get("body_markdown"):
        return None
    out["name"] = name
    out["cost_usd"] = reply.get("cost_usd")
    return out


def _write_skill(name: str, description: str, body: str, source_ids: list[int]) -> str:
    """Write SKILL.md with provenance; only ever inside a mind-* dir."""
    assert name.startswith(SKILL_PREFIX), "compiled skills must be namespaced"
    skill_dir = os.path.join(SKILLS_DIR, name)
    os.makedirs(skill_dir, exist_ok=True)
    path = os.path.join(skill_dir, "SKILL.md")
    frontmatter = (
        f"---\nname: {name}\ndescription: {description[:250]}\n---\n\n"
        f"<!-- compiled by corvus-mind {datetime.now(timezone.utc).isoformat(timespec='seconds')} "
        f"from neurons {sorted(source_ids)} — do not hand-edit; the graph is the source of truth -->\n\n"
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(frontmatter + body.strip() + "\n")
    return path


RETIRED_DIR = os.path.expanduser("~/.corvus-mind/retired-skills")


def _remove_skill(name: str) -> None:
    """Retract a compiled skill (mind-* only, manifest-owned). The rendering
    is ARCHIVED to retired-skills, never deleted — documentation is retired
    with history, and the source lessons remain in the graph regardless."""
    assert name.startswith(SKILL_PREFIX), "refusing to remove non-compiled skill"
    skill_dir = os.path.join(SKILLS_DIR, name)
    path = os.path.join(skill_dir, "SKILL.md")
    if os.path.exists(path):
        os.makedirs(RETIRED_DIR, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        os.replace(path, os.path.join(RETIRED_DIR, f"{name}-{stamp}.md"))
    if os.path.isdir(skill_dir) and not os.listdir(skill_dir):
        os.rmdir(skill_dir)


async def _stale_entries(db: AsyncSession, manifest: list[dict],
                         clusters: list[list[Neuron]]) -> list[dict]:
    """Manifest entries whose sources rotted or whose cluster grew."""
    cluster_sets = [frozenset(x.id for x in c) for c in clusters]
    stale: list[dict] = []
    for entry in manifest:
        sources = set(entry.get("sources", []))
        rotten = False
        for nid in sources:
            neuron = await db.get(Neuron, nid)
            if (neuron is None or not neuron.is_active
                    or neuron.superseded_by is not None
                    or (neuron.avg_utility or 0.5) < UTILITY_FLOOR):
                rotten = True
                break
        grew = any(sources < cs for cs in cluster_sets)
        if rotten or grew:
            stale.append({**entry, "reason": "source-rotten" if rotten else "cluster-grew"})
    return stale


async def _emit_skill_node(
    db: AsyncSession, name: str, description: str, cluster: list[Neuron],
) -> int | None:
    """Give the compiled skill its dark matter: a `skill` node anchored under
    its scope's department, with evidence-link edges from every source
    lesson — so the 3D universe and Explorer show what draws into it.
    No embedding is set, so skill nodes never enter semantic recall."""
    from app.middleware.rbac import UserIdentity
    from app.services import action_bus

    scope = cluster[0].department
    dept = (await db.execute(
        select(Neuron).where(Neuron.department == scope,
                             Neuron.node_type == "department",
                             Neuron.is_active.is_(True)).limit(1)
    )).scalar_one_or_none()
    identity = UserIdentity(user_id="skill_compiler", role="admin", source="system")
    result = await action_bus.submit(
        db=db, kind="neuron.create", actor=identity, actor_type="system",
        input_data={"spec": {
            "parent_id": dept.id if dept else None,
            "layer": (dept.layer + 1) if dept else 1,
            "node_type": "skill", "abstraction_type": "artifact",
            "label": name,
            "content": f"{description}\n\nCompiled from: " +
                       "; ".join(x.label for x in cluster),
            "summary": description[:280], "department": scope,
            "source_origin": "skill_compiler", "source_type": "operational",
            "authority_level": "informational",
        }, "reason": f"graph shadow for compiled skill {name}"},
    )
    if result.state != "applied":
        return None
    node_id = (result.payload or {}).get("neuron_id")
    assert node_id is not None, "neuron.create must return a neuron_id"
    for lesson in cluster:
        await _add_memory_edge(db, lesson.id, node_id, "evidence-link",
                               f"source lesson for skill {name}")
    return node_id


async def _retract_skill_node(db: AsyncSession, node_id: int | None) -> None:
    """Deactivate a retracted skill's graph shadow (edges stay as history)."""
    if node_id is None:
        return
    node = await db.get(Neuron, node_id)
    if node is not None:
        node.is_active = False


async def run_compile(db: AsyncSession) -> dict:
    """Reverse check + compile eligible clusters (bounded Opus spend)."""
    lessons = await _load_lessons(db)
    clusters = find_clusters(lessons)
    manifest = _load_manifest()

    stale = await _stale_entries(db, manifest, clusters)
    for entry in stale:
        _remove_skill(entry["name"])
        await _retract_skill_node(db, entry.get("node_id"))
        manifest = [m for m in manifest if m["name"] != entry["name"]]
        _log_action("compiler.retract", {
            "skill": entry["name"], "reason": entry["reason"]})

    compiled_sets = {frozenset(m.get("sources", [])) for m in manifest}
    emitted: list[dict] = []
    for cluster in sorted(clusters, key=len, reverse=True):
        if len(emitted) >= MAX_COMPILE_PER_RUN:
            break
        ids = frozenset(x.id for x in cluster)
        if ids in compiled_sets:
            continue
        skill = await _compose(cluster)
        if skill is None:
            _log_action("compiler.compose_failed", {"sources": sorted(ids)})
            continue
        # Name uniqueness: a grown cluster can re-earn an existing name —
        # retract the old artifact (this IS the supersession) rather than
        # silently overwriting its file beside a duplicate manifest entry.
        clash = next((m for m in manifest if m["name"] == skill["name"]), None)
        if clash is not None:
            await _retract_skill_node(db, clash.get("node_id"))
            manifest = [m for m in manifest if m["name"] != skill["name"]]
            _log_action("compiler.retract", {
                "skill": skill["name"], "reason": "name-superseded-by-recompile"})
        path = _write_skill(skill["name"], skill["description"],
                            skill["body_markdown"], sorted(ids))
        node_id = await _emit_skill_node(db, skill["name"], skill["description"], cluster)
        entry = {"name": skill["name"], "sources": sorted(ids),
                 "scope": cluster[0].department, "path": path, "node_id": node_id,
                 "compiled_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        manifest.append(entry)
        compiled_sets.add(ids)
        emitted.append({**entry, "cost_usd": skill.get("cost_usd")})
        _log_action("compiler.emit", {
            "skill": skill["name"], "sources": sorted(ids)})
    await db.commit()
    _save_manifest(manifest)
    return {"lessons": len(lessons), "clusters": len(clusters),
            "retracted": [e["name"] for e in stale],
            "emitted": emitted, "manifest_size": len(manifest)}
