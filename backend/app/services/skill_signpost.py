"""Skill signposting (mind-skill-signpost): push→pull pointers.

Compiled mind-* skills are dark matter at query time — their graph nodes
are filtered out of recall results as scaffolding, so the graph's own
"this cluster is relevant" signal never reaches the harness, and pull
delivery starves (measured 2026-07-17: 1 lifetime Skill-tool load across
the compiled set vs 6x/3x for harness skills). This module converts
push-side relevance into pull-side awareness: when a skill signals in
the over-fetched recall candidate set, /recall emits a ~20-token pointer
naming the skill. Two eligibility paths (mind-skill-node-scoring):
enough of the skill's manifest source neurons score (member-lesson
vote), OR the skill's own graph node scores high — the compiler embeds
skill nodes like any neuron, so the graph can say "this cluster is
relevant" even when the individual lessons lose the candidate race.
Bodies are never emitted — pull still pays for itself only on load.

Pure manifest + disk reads. No DB, no LLM — the recall hot path stays
LLM-free.
"""

import json

from app.services.skill_compiler import MANIFEST_PATH

MAX_POINTERS = 2
MIN_MEMBER_VOTES = 2
# Direct-path threshold: a skill's own graph node at/above this recall
# score earns the pointer even when the member vote misses. Calibrated
# 2026-07-17 on 6 probe queries: 3 far probes (chickpea dinner, FL
# beaches, tomato pruning) put NO skill node in the top-200 candidates
# (far max ~0); near-cluster true positives scored 0.9406 (#1137 on the
# 'modify a service under backend/app/services' miss case), 0.7013
# (#1184 on a transcripts query), 0.771 (#1137, the original measured
# miss); the one wrong-skill datum was 0.4185 (#1136 scoring on the
# transcripts query it doesn't answer). 0.55 clears the wrong-skill
# datum and stays under the weakest true positive, with margin both
# ways. Revisit when pointer-path telemetry accumulates.
SKILL_NODE_MIN_SCORE = 0.55


def _frontmatter_description(path: str) -> str | None:
    """`description:` from a rendered SKILL.md's frontmatter.

    None means the rendering is missing/unreadable — a skill retired
    out-of-band must not be signposted even if a stale manifest row
    survives. An empty string means readable but undescribed (pointer
    still emitted; the name alone carries the signal).
    """
    try:
        with open(path, encoding="utf-8") as fh:
            head = fh.read(4000)
    except OSError:
        return None
    if not head.startswith("---"):
        return ""
    parts = head.split("---", 2)
    if len(parts) < 3:
        return ""
    for line in parts[1].splitlines():
        if line.startswith("description:"):
            return line.split(":", 1)[1].strip()
    return ""


def skill_pointers_for(
    scored_candidates: list[tuple[int, float]],
    skill_node_candidates: list[tuple[str, float]] | None = None,
) -> list[dict]:
    """[{name, description, votes, top_member_score, path, node_score?}]
    for compiled skills that signaled in this recall's candidate set.

    Member-lesson vote: a skill is relevant when >= MIN_MEMBER_VOTES of
    its manifest sources appear among the scored candidates. Direct
    node-score path: the skill's own graph node (label == manifest name)
    scored >= SKILL_NODE_MIN_SCORE among skill_node_candidates. Either
    path earns the pointer; one pointer per skill even when both fire
    (path records which: 'member-vote' | 'node-score' | 'both').

    Mapping node label -> manifest gates on a LIVE manifest entry plus
    a rendering on disk — the graph carries stale active skill nodes
    (e.g. #186 mind-read-before-edit-tool) that must never signpost.
    Designated capsules (charter, self-model) never earn a pointer:
    they are already push-injected whole at SessionStart, so a pointer
    would be pure noise.
    """
    try:
        with open(MANIFEST_PATH, encoding="utf-8") as fh:
            manifest = json.load(fh)
    except (OSError, ValueError):
        return []
    score_of: dict[int, float] = {}
    for nid, score in scored_candidates:
        prev = score_of.get(nid)
        score_of[nid] = score if prev is None or score > prev else prev
    node_score_of: dict[str, float] = {}
    for label, score in skill_node_candidates or []:
        prev = node_score_of.get(label)
        node_score_of[label] = score if prev is None or score > prev else prev
    pointers = []
    for entry in manifest:
        if not isinstance(entry, dict):
            continue
        if entry.get("designated") or entry.get("retired"):
            continue
        member_scores = [score_of[nid] for nid in entry.get("sources", [])
                         if nid in score_of]
        node_score = node_score_of.get(entry.get("name", ""))
        member_fired = len(member_scores) >= MIN_MEMBER_VOTES
        node_fired = node_score is not None and node_score >= SKILL_NODE_MIN_SCORE
        if not (member_fired or node_fired):
            continue
        description = _frontmatter_description(entry.get("path", ""))
        if description is None:
            continue
        pointer = {
            "name": entry.get("name", ""),
            "description": description,
            "votes": len(member_scores),
            "top_member_score": round(max(member_scores), 4) if member_scores else 0.0,
            "path": ("both" if member_fired and node_fired
                     else "member-vote" if member_fired else "node-score"),
        }
        if node_score is not None:
            pointer["node_score"] = round(node_score, 4)
        pointers.append(pointer)
    # A 2-vote member win outranks a lone hot node; among equals the
    # strongest signal (member or node) breaks the tie.
    pointers.sort(key=lambda p: (-p["votes"],
                                 -max(p["top_member_score"],
                                      p.get("node_score", 0.0))))
    return pointers[:MAX_POINTERS]
