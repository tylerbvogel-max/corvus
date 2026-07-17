"""Graph Lint — offline corpus-quality maintenance for the memory tenant.

Phase 0 of mind-concept-discovery showed the memory graph's real defect
is quality, not missing abstraction: paraphrase duplicates enter freely
(write-time dedup is exact-label-match only), cross-scope duplicate
pairs were auto-discarded before judging, and pair verdicts evaporated
with each janitor run so the backlog never drained. Duplicates burn
top-6 injection slots — when memory fires, we must not be looking
through copies of the same fact told ten different ways.

This module holds the lint primitives; mind_janitors wires them into
the janitor passes:

  1. CORPUS HEALTH (deterministic, no LLM) — duplicate mass, scope
     consistency, injection-slot waste. Rendered at the START of every
     lint run (pre-mutation state) and persisted beside
     janitor-report.json with an append-only history for trend proof.
  2. VERDICT STORE — persisted pair verdicts (MindPairVerdict) with
     content hashes; judged pairs never re-queue unless a side changed.
     MAX_JUDGED_PAIRS becomes a rate limit draining a pareto-ranked
     backlog (injection co-delivery count first, similarity second).
  3. LEXICAL LANE — entity overlap + token Jaccard as a second signal:
     embedding-high + lexical-high = near-verbatim fast path;
     embedding-high + lexical-low = paraphrase (judge it);
     lexical-high + embedding in the 0.65-0.75 near-miss band = judge
     candidates pure cosine misses.
  4. SCOPE LINT — deterministic machine-fact heuristic flags
     machine-level facts (global paths, tool versions, OS quirks) filed
     under Projects as rescope proposals.
  5. COMPONENT FUSION — connected components of confirmed duplicates
     fuse in ONE proposal (canonical + all absorbed members) instead of
     N path-dependent pairwise passes.

Every mutation is proposal-gated: the lint NEVER auto-approves what it
generated. Tyler countersigns; metrics must move the right way after
each approved batch.
"""

import hashlib
import json
import os
import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AutopilotProposal, MindPairVerdict, Neuron, NeuronFiring, ProposalItem,
)

# ── thresholds ──────────────────────────────────────────────────────────
# NEAR_MISS_SIM + LEXICAL_JACCARD_HIGH calibrated on the live corpus
# 2026-07-16: floor 0.60 / jaccard 0.35 admits exactly 8 sub-borderline
# pairs, five of which are the SAME "read file before editing" fact told
# five ways (0.61-0.74 cosine — all below the 0.75 radar). Tighter
# settings (0.65/0.45) missed three of those five. ENTITY_OVERLAP_HIGH
# remains a guessed constant — only 55/249 lessons carry entities;
# revisit when the backfill widens coverage.
NEAR_MISS_SIM = 0.60
LEXICAL_JACCARD_HIGH = 0.35   # calibrated 2026-07-16 (see above)
ENTITY_OVERLAP_HIGH = 0.5     # guessed — calibrate as entity coverage grows
MAX_SCOPE_LINT_PROPOSALS = 5
MAX_COMPONENT_PROPOSALS = 3
COMPONENT_MIN_MEMBERS = 3     # below this, pairwise proposals suffice
OPUS_COMPOSE_MIN_MEMBERS = 4  # canonical-content composition is rare + expensive

HEALTH_TOP_SLOTS = 6          # injection top-k whose waste we measure

_NON_DUPLICATE_VERDICTS = frozenset(
    {"complementary", "genuinely-scoped", "unrelated", "contradictory"})
_DUPLICATE_VERDICTS = frozenset({"duplicate", "duplicate-mis-scoped"})


# ── pair identity ───────────────────────────────────────────────────────

def pair_key(id_a: int, id_b: int) -> tuple[int, int]:
    """Normalized pair identity: (min, max)."""
    assert id_a != id_b, "a pair needs two distinct neurons"
    return (id_a, id_b) if id_a < id_b else (id_b, id_a)


def content_hash(neuron: Neuron) -> str:
    """Stable hash of the judged text — mismatch means the verdict is stale."""
    text = f"{neuron.label}\n{neuron.content or ''}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


async def load_verdicts(db: AsyncSession) -> dict[tuple[int, int], MindPairVerdict]:
    """All persisted pair verdicts keyed by normalized pair."""
    rows = (await db.execute(select(MindPairVerdict))).scalars().all()
    return {pair_key(v.neuron_a_id, v.neuron_b_id): v for v in rows}


def verdict_is_current(
    verdict: MindPairVerdict, a: Neuron, b: Neuron,
) -> bool:
    """A verdict holds only while both sides' content is unchanged."""
    lo, hi = (a, b) if a.id < b.id else (b, a)
    return (verdict.content_hash_a == content_hash(lo)
            and verdict.content_hash_b == content_hash(hi))


async def upsert_verdict(
    db: AsyncSession, a: Neuron, b: Neuron, sim: float, verdict: str,
    source: str = "haiku-judge", detail: dict | None = None,
) -> MindPairVerdict:
    """Persist (or refresh) the verdict for one pair."""
    lo, hi = (a, b) if a.id < b.id else (b, a)
    key = pair_key(a.id, b.id)
    row = (await db.execute(
        select(MindPairVerdict).where(
            MindPairVerdict.neuron_a_id == key[0],
            MindPairVerdict.neuron_b_id == key[1],
        )
    )).scalar_one_or_none()
    if row is None:
        row = MindPairVerdict(neuron_a_id=key[0], neuron_b_id=key[1],
                              content_hash_a="", content_hash_b="",
                              sim=sim, verdict=verdict)
        db.add(row)
    row.content_hash_a = content_hash(lo)
    row.content_hash_b = content_hash(hi)
    row.sim = sim
    row.verdict = verdict
    row.source = source
    row.detail = json.dumps(detail)[:500] if detail else None
    row.judged_at = datetime.utcnow().replace(tzinfo=None)
    await db.flush()
    return row


# ── lexical lane ────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[a-z0-9_.~/-]{3,}")
_STOP = frozenset("""the and for with that this from are was were when must
never always into not use used using has have been its can should would
about over under after before than then them they there here what which
""".split())


def _tokens(neuron: Neuron) -> set[str]:
    text = f"{neuron.label} {neuron.content or ''}".casefold()
    return {t for t in _TOKEN_RE.findall(text) if t not in _STOP}


def token_jaccard(a: Neuron, b: Neuron) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def entity_overlap(a: Neuron, b: Neuron) -> float | None:
    """Jaccard over write-time entities; None when either side has none
    (55/249 coverage as of 2026-07-16 — absence is not evidence)."""
    ea = {str(e).casefold() for e in (a.entities or [])}
    eb = {str(e).casefold() for e in (b.entities or [])}
    if not ea or not eb:
        return None
    return len(ea & eb) / len(ea | eb)


def lexical_high(a: Neuron, b: Neuron) -> bool:
    """Second lint signal: near-verbatim token/entity agreement."""
    if token_jaccard(a, b) >= LEXICAL_JACCARD_HIGH:
        return True
    overlap = entity_overlap(a, b)
    return overlap is not None and overlap >= ENTITY_OVERLAP_HIGH


# ── machine-fact scope heuristic (deterministic, no LLM) ────────────────
# Environment = facts about this machine regardless of which repo the
# session ran in. A Projects-scoped lesson that carries machine-global
# signals and NO repo tie is a rescope candidate.

_MACHINE_SIGNAL_RES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"~/\.config/|/home/\w+/\.config"), "global-config-path"),
    (re.compile(r"\bnvm\b", re.I), "nvm"),
    (re.compile(r"\bnode\s+v?\d+(\.\d+)+", re.I), "node-version"),
    (re.compile(r"\bchrome\s?os\b|\bcrostini\b|\bchromebook\b", re.I), "os"),
    (re.compile(r"\bsystemctl\s+--user\b", re.I), "systemd-user"),
    (re.compile(r"\b(apt|apt-get|dpkg)\b"), "apt"),
    (re.compile(r"\b\d+(\.\d+)?\s*GB\b", re.I), "hardware-capacity"),
    (re.compile(r"\bfuser\b|\blsof\b"), "port-tools"),
    (re.compile(r"/usr/(local/)?bin/"), "system-binary"),
    (re.compile(r"\bpython3\.\d+\b"), "system-python"),
]
_REPO_TIE_RES: list[re.Pattern] = [
    re.compile(r"~/Projects/|/home/\w+/Projects/"),
    re.compile(r"\b(backend|frontend)/"),
    re.compile(r"vite\.config|package\.json|requirements\.txt|alembic"),
    re.compile(r"\bTENANT_ID\b|\bVITE_API_PORT\b|\bPYTHONPATH\b"),
    re.compile(r"\bport\s+\d{4}\b", re.I),          # ports are assigned per-project here
    re.compile(r"\bnpm run\b|\bpytest\b|\buvicorn\b|\bnpm install\b"),
    re.compile(r"\.service\b"),                     # named systemd units belong to their project
]


def machine_fact_signals(neuron: Neuron) -> tuple[list[str], bool]:
    """(machine-global signal names, has any repo tie) for one lesson."""
    text = f"{neuron.label} {neuron.content or ''}"
    hits = [name for rx, name in _MACHINE_SIGNAL_RES if rx.search(text)]
    repo_tied = any(rx.search(text) for rx in _REPO_TIE_RES)
    return hits, repo_tied


def scope_lint_flag(neuron: Neuron) -> list[str] | None:
    """Signal names if this lesson looks machine-level but is filed under
    Projects; None otherwise. Conservative: any repo tie clears it."""
    if neuron.department != "Projects":
        return None
    hits, repo_tied = machine_fact_signals(neuron)
    if hits and not repo_tied:
        return hits
    return None


# ── duplicate components (union-find) ───────────────────────────────────

def duplicate_components(
    lesson_ids: list[int], pair_edges: list[tuple[int, int]],
) -> list[list[int]]:
    """Connected components (size >= 2) over the duplicate-pair graph.
    Inputs are neuron ids; edges are normalized id pairs."""
    index = {nid: i for i, nid in enumerate(lesson_ids)}
    parent = list(range(len(lesson_ids)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pair_edges:
        if a in index and b in index:
            parent[find(index[a])] = find(index[b])
    groups: dict[int, list[int]] = {}
    for nid, i in index.items():
        groups.setdefault(find(i), []).append(nid)
    return [sorted(g) for g in groups.values() if len(g) >= 2]


# ── injection co-delivery (pareto signal + waste metric) ────────────────

async def included_query_sets(db: AsyncSession) -> dict[int, set[int]]:
    """neuron_id -> set of query_ids where the neuron was actually
    delivered (was_included) — the raw material for both the slot-waste
    metric and the pareto ranking of the judge queue."""
    rows = (await db.execute(
        select(NeuronFiring.neuron_id, NeuronFiring.query_id)
        .where(NeuronFiring.was_included.is_(True))
    )).all()
    out: dict[int, set[int]] = {}
    for neuron_id, query_id in rows:
        out.setdefault(neuron_id, set()).add(query_id)
    return out


def codelivery_count(
    a_id: int, b_id: int, inclusion: dict[int, set[int]],
) -> int:
    """How many queries delivered BOTH sides of the pair (slot burn)."""
    qa, qb = inclusion.get(a_id), inclusion.get(b_id)
    if not qa or not qb:
        return 0
    return len(qa & qb)


# ── corpus health (item 0 — deterministic, no LLM) ──────────────────────

def _health_path() -> str:
    from app.services.mind_janitors import EPISODE_DIR
    return os.path.join(EPISODE_DIR, "corpus-health.json")


def _mass_metrics(components: list[list[int]], active: int) -> dict:
    in_components = sum(len(c) for c in components)
    return {
        "components": len(components),
        "largest": max((len(c) for c in components), default=0),
        "lessons_in_components": in_components,
        "pct_of_active": round(100.0 * in_components / active, 1)
        if active else 0.0,
    }


def _scope_metrics(lessons: list[Neuron]) -> dict:
    """Machine-fact heuristic vs department (deterministic)."""
    flagged = []
    for n in lessons:
        hits = scope_lint_flag(n)
        if hits:
            flagged.append({"id": n.id, "label": n.label[:80],
                            "scope": n.department, "signals": hits})
    return {
        "projects_lessons": sum(1 for n in lessons if n.department == "Projects"),
        "machine_fact_flags": len(flagged),
        "rate": round(1.0 - (len(flagged) / len(lessons)), 3) if lessons else 1.0,
        "flagged": flagged,
    }


def _slot_waste_metrics(
    by_id: dict[int, Neuron], raw_components: list[list[int]],
    inclusion: dict[int, set[int]],
) -> dict:
    """Duplicate co-delivery within delivered injection slots. Components
    come from the RAW graph — waste counts what the reader actually saw,
    regardless of later verdicts."""
    component_of: dict[int, int] = {}
    for ci, comp in enumerate(raw_components):
        for nid in comp:
            component_of[nid] = ci
    query_slots: dict[int, list[int]] = {}
    for nid, qids in inclusion.items():
        if nid not in by_id:
            continue  # non-lesson or inactive neuron
        for qid in qids:
            query_slots.setdefault(qid, []).append(nid)
    total_slots = wasted_slots = queries_with_waste = 0
    for nids in query_slots.values():
        total_slots += len(nids)
        comp_counts: dict[int, int] = {}
        for nid in nids:
            ci = component_of.get(nid)
            if ci is not None:
                comp_counts[ci] = comp_counts.get(ci, 0) + 1
        waste = sum(c - 1 for c in comp_counts.values() if c > 1)
        if waste:
            wasted_slots += waste
            queries_with_waste += 1
    return {
        "queries_with_delivery": len(query_slots),
        "delivered_slots": total_slots,
        "duplicate_codelivered_slots": wasted_slots,
        "queries_with_waste": queries_with_waste,
        "waste_rate": round(wasted_slots / total_slots, 4) if total_slots else 0.0,
    }


def _verdict_metrics(
    verdicts: dict, by_id: dict[int, Neuron],
    id_pairs: list[tuple[int, int]],
) -> dict:
    verdict_counts: dict[str, int] = {}
    for v in verdicts.values():
        verdict_counts[v.verdict] = verdict_counts.get(v.verdict, 0) + 1
    judged_current = sum(
        1 for k, v in verdicts.items()
        if k[0] in by_id and k[1] in by_id
        and verdict_is_current(v, by_id[k[0]], by_id[k[1]]))
    backlog = sum(
        1 for k in id_pairs
        if k not in verdicts
        or not verdict_is_current(verdicts[k], by_id[k[0]], by_id[k[1]]))
    return {"total": len(verdicts), "current": judged_current,
            "by_verdict": verdict_counts, "unjudged_backlog": backlog}


def _persist_health(report: dict) -> None:
    path = _health_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    history = {k: v for k, v in report.items() if k != "scope_consistency"} | {
        "scope_consistency": {k: v for k, v in report["scope_consistency"].items()
                              if k != "flagged"}}
    with open(path.replace(".json", "-history.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps(history) + "\n")


async def corpus_health(db: AsyncSession, persist: bool = True) -> dict:
    """Deterministic corpus-quality metrics. Read-only against the graph;
    persists corpus-health.json + appends corpus-health-history.jsonl."""
    from app.services.mind_janitors import (
        BORDERLINE_SIM, _load_lessons, _similar_pairs,
    )
    lessons = await _load_lessons(db)
    by_id = {n.id: n for n in lessons}
    pairs = _similar_pairs(lessons)  # (i, j, sim) at >= BORDERLINE_SIM
    id_pairs = [pair_key(lessons[i].id, lessons[j].id) for i, j, _ in pairs]
    verdicts = await load_verdicts(db)

    def _resolved_non_duplicate(key: tuple[int, int]) -> bool:
        v = verdicts.get(key)
        return (v is not None and v.verdict in _NON_DUPLICATE_VERDICTS
                and verdict_is_current(v, by_id[key[0]], by_id[key[1]]))

    all_ids = [n.id for n in lessons]
    raw_components = duplicate_components(all_ids, id_pairs)
    unresolved_components = duplicate_components(
        all_ids, [k for k in id_pairs if not _resolved_non_duplicate(k)])
    report = {
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "active_lessons": len(lessons),
        "pair_threshold": BORDERLINE_SIM,
        "duplicate_mass": {
            "raw": _mass_metrics(raw_components, len(lessons)),
            "unresolved": _mass_metrics(unresolved_components, len(lessons)),
        },
        "scope_consistency": _scope_metrics(lessons),
        "injection_slot_waste": _slot_waste_metrics(
            by_id, raw_components, await included_query_sets(db)),
        "verdict_store": _verdict_metrics(verdicts, by_id, id_pairs),
    }
    if persist:
        _persist_health(report)
    return report


# ── proposal plumbing ───────────────────────────────────────────────────

_LINT_GAP_SOURCES = ("consolidation_dedup", "component_fusion", "scope_lint")


async def open_or_rejected_item_targets(db: AsyncSession) -> set[tuple[int, str]]:
    """(target_neuron_id, field) already covered by a proposed OR rejected
    lint proposal. Proposed: don't double-queue. Rejected: the human said
    no — the lint must never nag the same mutation back into the inbox."""
    rows = (await db.execute(
        select(ProposalItem.target_neuron_id, ProposalItem.field)
        .join(AutopilotProposal,
              AutopilotProposal.id == ProposalItem.proposal_id)
        .where(AutopilotProposal.gap_source.in_(_LINT_GAP_SOURCES),
               AutopilotProposal.state.in_(("proposed", "rejected")))
    )).all()
    return {(nid, field) for nid, field in rows if nid is not None and field}
