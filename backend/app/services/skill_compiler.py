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
from sqlalchemy import func as sa_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Neuron
# Charter MEMBERSHIP (which tiers qualify, which verdict admits, and the
# eligibility gate itself) is owned by delivery_mode; this module owns
# RENDERING. Record 04b: the two used to hold half each and import the other
# half back at function scope, which was that entire import cycle.
from app.services.delivery_mode import (  # noqa: F401
    CHARTER_TIERS, STANDING, charter_eligible_filters,
)
from app.services.mind_corpus import (
    LESSON_TYPES, _add_memory_edge, _load_lessons, _log_action,
)

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


CHARTER_NAME = SKILL_PREFIX + "charter"
# ~1500 tokens: the always-on budget. Guessed constant — revisit once
# injection-size telemetry exists (label per data-driven-design rule).
CHARTER_MAX_CHARS = 6000
# (CHARTER_TIERS moved to delivery_mode — see the import at the top.)


def _charter_line(n: Neuron) -> str:
    """One-line hook: scope + label + first sentence + verification stamp."""
    summary = (n.summary or n.content or "").strip().split("\n")[0]
    verified = f" (verified {n.last_verified.date()})" if n.last_verified else ""
    return f"- [{n.department or 'global'}] {n.label}: {summary[:180]}{verified}"


def _apply_charter_entry(manifest: list[dict], charter: dict) -> list[dict]:
    """Swap in the freshly compiled charter entry.

    A skipped compile (no delivery verdicts yet) must LEAVE the existing
    entry alone: dropping it would strip the capsule's source ids and
    silently kill charter attribution while the capsule kept injecting
    from disk."""
    if charter.get("skipped"):
        return manifest
    rest = [m for m in manifest if m["name"] != CHARTER_NAME]
    if not charter.get("path"):
        return rest
    return rest + [{
        "name": CHARTER_NAME, "sources": charter["sources"],
        "source_labels": charter.get("source_labels", []),
        "designated": True, "path": charter["path"],
        "compiled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }]


async def compile_charter(db: AsyncSession) -> dict:
    """W1 presence layer: render every STANDING lesson as a one-line rule
    into the designated mind-charter capsule, which the memory hook injects
    WHOLE at SessionStart. Policy is never retrieved — it is always present.
    (Native-memory parity: a MEMORY.md-style index, but membership is EARNED
    via attribution-driven authority promotion plus a re-audited delivery
    verdict, which a hand-written index cannot do.) Deterministic render,
    no LLM — the delivery judgment happens in the janitor cycle, not here.

    Charter-tier authority is now necessary but NOT sufficient: a fact can
    be completely trusted and still belong in the retrieved channel (see
    delivery_mode). Neurons that have never been judged are excluded — an
    unreviewed fact must not buy unconditional injection by default."""
    # IDENTITY WALL (mind-reference-class): the charter is always-present
    # identity — reference-class (document-ingested) neurons are excluded
    # on every axis even if mislabeled or somehow holding charter-tier
    # authority. A PDF can never become standing policy without the
    # human-countersigned graduation path.
    eligible = charter_eligible_filters()
    rows = (await db.execute(
        select(Neuron).where(
            *eligible, Neuron.delivery_mode == STANDING,
        ).order_by(Neuron.avg_utility.desc(), Neuron.id)
    )).scalars().all()
    # COUNTERSIGNED RETIREMENT (mind-delivery-plasticity): a charter line
    # whose capsule pathway reached `retired` — which requires an APPLIED
    # human countersign; the plasticity writer refuses it otherwise — is
    # compiled out here. This is tier-2 amputation, not the tier-1 reflex:
    # attenuated/retire-proposed pathways do NOT thin the capsule. Dropping
    # the line takes this PATHWAY to zero by explicit human decision (not
    # silently — the no-silent-kill floor governs the automatic states);
    # the neuron keeps authority and stays reachable via retrieved lanes.
    from app.models import DeliveryPathway
    retired_ids = set((await db.execute(
        select(DeliveryPathway.neuron_id).where(
            DeliveryPathway.trigger == f"capsule:{CHARTER_NAME}",
            DeliveryPathway.state == "retired")
    )).scalars().all())
    if retired_ids:
        _log_action("compiler.charter_pathway_retired", {
            "excluded": sorted(retired_ids)})
        rows = [n for n in rows if n.id not in retired_ids]
    # SAFETY: an empty verdict set means the classifier has not run (or
    # failed) — that is not evidence that no policy exists, so leave the
    # existing charter standing rather than shipping a blank one.
    if not rows:
        judged = (await db.execute(
            select(sa_func.count(Neuron.id)).where(
                *eligible, Neuron.delivery_mode.is_not(None))
        )).scalar() or 0
        if not judged:
            _log_action("compiler.charter_unclassified", {"candidates": 0})
            return {"included": 0, "candidates": 0, "path": None,
                    "skipped": "no delivery verdicts yet"}
    lines: list[str] = []
    included: list[int] = []
    size = 0
    for n in rows:  # bounded by row count (JPL-2)
        line = _charter_line(n)
        if size + len(line) + 1 > CHARTER_MAX_CHARS:
            _log_action("compiler.charter_overflow", {
                "dropped": len(rows) - len(included)})
            break
        lines.append(line)
        included.append(n.id)
        size += len(line) + 1
    assert len(included) == len(lines), "one neuron id per rendered line"
    if not lines:
        return {"included": 0, "candidates": len(rows), "path": None}
    body = ("Standing working policies, earned through repeated verified use "
            "and re-audited every janitor cycle. Weigh these strongly; each "
            "line names the scope it governs.\n\n" + "\n".join(lines))
    path = _write_skill(
        CHARTER_NAME,
        "Corvus-Mind charter — standing policies injected whole at "
        "SessionStart by the memory hook; not task-triggered.",
        body, included)
    assert os.path.exists(path), "charter rendering must land on disk"
    _log_action("compiler.charter", {
        "included": len(included), "candidates": len(rows), "chars": size})
    labels = [n.label for n in rows[:len(included)]]
    return {"included": len(included), "candidates": len(rows),
            "path": path, "sources": included, "source_labels": labels}


def _reconcile_manifest(manifest: list[dict]) -> tuple[list[dict], list[str]]:
    """Drop entries whose rendering vanished outside a compile run — the
    manifest must never overstate what is live on disk (found 2026-07-12:
    4 entries pointed at skills that had been retired out-of-band)."""
    kept: list[dict] = []
    ghosts: list[str] = []
    for entry in manifest:
        path = os.path.join(SKILLS_DIR, entry["name"], "SKILL.md")
        if os.path.exists(path):
            kept.append(entry)
        else:
            ghosts.append(entry["name"])
    return kept, ghosts


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
        max_tokens=3000, model="opus", timeout=300, workload="skill_compilation",
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
    rendered = frontmatter + body.strip() + "\n"
    # Canonical-first multi-harness projection. Claude remains a target for
    # compatibility, no longer the source format or sole destination.
    from app.services.skill_projection import project_skill
    outputs = project_skill(name, rendered)
    assert outputs.get("claude-code") == path
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
    from app.services.skill_projection import remove_projected_skill
    remove_projected_skill(name)


async def _stale_entries(db: AsyncSession, manifest: list[dict],
                         clusters: list[list[Neuron]]) -> list[dict]:
    """Manifest entries whose sources rotted or whose cluster grew."""
    cluster_sets = [frozenset(x.id for x in c) for c in clusters]
    stale: list[dict] = []
    for entry in manifest:
        # Designated capsules (self-model, charter) are compiler- or
        # hand-owned with mutating source sets; the cluster-growth test
        # would false-positive on them (∅ ⊂ any cluster) and retract
        # identity. They are refreshed by their own paths, never here.
        if entry.get("designated"):
            continue
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
    The node embeds like any neuron and scores in the prepare pipeline
    (measured 2026-07-17: #1137 at 0.771+); recall filters it from hits
    as scaffolding, and its score instead feeds the direct signpost path
    (mind-skill-node-scoring, services/skill_signpost.py)."""
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


SELF_MODEL_NAME = SKILL_PREFIX + "self-model"


async def _self_model_growth_check(db: AsyncSession, manifest: list[dict]) -> None:
    """W7: the self-model capsule is hand-curated (identity is never
    auto-recompiled), but its source cluster can now grow — Assistant-scope
    lessons approved through the review queue land at organizational
    authority outside the designated source set. Surface that drift as a
    logged action so the inbox shows 'your self-model has approved growth
    awaiting curation' instead of silently diverging."""
    entry = next((m for m in manifest if m.get("name") == SELF_MODEL_NAME), None)
    if entry is None:
        return
    from app.services.reference_class import reference_exclusion_filters
    known = set(entry.get("sources", []))
    grown = (await db.execute(
        select(Neuron).where(
            Neuron.is_active.is_(True),
            Neuron.department == "Assistant",
            Neuron.node_type.in_(LESSON_TYPES),
            Neuron.superseded_by.is_(None),
            Neuron.authority_level.in_(CHARTER_TIERS),
            Neuron.id.notin_(known) if known else Neuron.id.isnot(None),
            # Identity wall: document knowledge never drifts into the
            # self-model's growth queue (mind-reference-class).
            *reference_exclusion_filters(),
        )
    )).scalars().all()
    if grown:
        _log_action("compiler.self_model_growth", {
            "pending_curation": [{"neuron_id": n.id, "label": n.label}
                                 for n in grown],
            "designated_sources": sorted(known)})


async def refresh_projections_after_reconsolidation(
    db: AsyncSession, retired_ids: list[int],
) -> dict:
    """Kernel Phase 3E: when reconsolidation retires lessons, no generated
    artifact may keep citing them. Compiled skills whose source set
    intersects the retired members are retracted immediately (archived,
    graph shadow deactivated) — the next compiler run rebuilds them from
    the synthesis; the charter (deterministic, cheap) recompiles in place.
    Runs POST-COMMIT: disk artifacts must never move ahead of a
    transaction that could still roll back. Mutates the DB only for
    retracted skill shadows — the caller commits when db_changed."""
    retired = set(retired_ids)
    manifest, ghosts = _reconcile_manifest(_load_manifest())
    retracted: list[str] = []
    db_changed = False
    for entry in list(manifest):
        if entry.get("designated") or entry.get("name") == CHARTER_NAME:
            continue
        if retired & set(entry.get("sources", [])):
            _remove_skill(entry["name"])
            await _retract_skill_node(db, entry.get("node_id"))
            db_changed = db_changed or entry.get("node_id") is not None
            manifest = [m for m in manifest if m["name"] != entry["name"]]
            retracted.append(entry["name"])
            _log_action("compiler.retract", {
                "skill": entry["name"],
                "reason": "source-reconsolidated",
                "retired_sources": sorted(retired & set(entry.get("sources", []))),
            })
    charter = {"included": 0}
    charter_entry = next(
        (m for m in manifest if m.get("name") == CHARTER_NAME), None)
    if charter_entry is None or retired & set(charter_entry.get("sources", [])):
        charter = await compile_charter(db)
        manifest = _apply_charter_entry(manifest, charter)
    _save_manifest(manifest)
    stale_left = [
        m["name"] for m in manifest
        if retired & set(m.get("sources", []))
    ]
    assert not stale_left, f"stale source ids survived in {stale_left}"
    return {"retracted": retracted, "reconciled_ghosts": ghosts,
            "charter_recompiled": bool(charter.get("path")),
            "db_changed": db_changed}


async def run_compile(db: AsyncSession) -> dict:
    """Reverse check + compile eligible clusters (bounded Opus spend)."""
    lessons = await _load_lessons(db)
    # SLEEP (mind-synaptic-downscaling): dormant lessons stop being
    # COMPILED, which is the whole of what dormancy means — automatic
    # delivery goes quiet, direct recall is untouched, and `_load_lessons`
    # is deliberately left alone so consolidation and lint keep maintaining
    # these rows. Filtered here rather than in the substrate because a
    # dormant memory must still be deduped, judged and re-embedded; it just
    # should not be written into a skill file nobody's use justifies.
    dormant = {n.id for n in lessons if n.dormant_at is not None}
    if dormant:
        _log_action("compiler.dormant_excluded", {"count": len(dormant),
                                                  "excluded": sorted(dormant)})
        lessons = [n for n in lessons if n.dormant_at is None]
    clusters = find_clusters(lessons)
    manifest, ghosts = _reconcile_manifest(_load_manifest())
    for name in ghosts:
        _log_action("compiler.manifest_reconcile", {
            "skill": name, "reason": "rendering-missing-on-disk"})

    stale = await _stale_entries(db, manifest, clusters)
    for entry in stale:
        _remove_skill(entry["name"])
        await _retract_skill_node(db, entry.get("node_id"))
        manifest = [m for m in manifest if m["name"] != entry["name"]]
        _log_action("compiler.retract", {
            "skill": entry["name"], "reason": entry["reason"]})

    compiled_sets = {frozenset(m.get("sources", [])) for m in manifest}
    emitted: list[dict] = []
    composition_attempted = composition_failed = 0
    for cluster in sorted(clusters, key=len, reverse=True):
        if len(emitted) >= MAX_COMPILE_PER_RUN:
            break
        ids = frozenset(x.id for x in cluster)
        if ids in compiled_sets:
            continue
        composition_attempted += 1
        skill = await _compose(cluster)
        if skill is None:
            composition_failed += 1
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
    charter = await compile_charter(db)
    manifest = _apply_charter_entry(manifest, charter)
    await _self_model_growth_check(db, manifest)
    await db.commit()
    _save_manifest(manifest)
    return {"lessons": len(lessons), "clusters": len(clusters),
            "composition_attempted": composition_attempted,
            "composition_failed": composition_failed,
            "retracted": [e["name"] for e in stale],
            "reconciled_ghosts": ghosts, "charter": charter,
            "emitted": emitted, "manifest_size": len(manifest)}
