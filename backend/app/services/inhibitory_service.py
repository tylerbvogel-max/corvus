"""Inhibitory regulation service — GABAergic interneuron analogues.

Three-pass inhibition applied after scoring and spread activation:

1. Regional density suppression (basket cell analogue):
   Per-region (department or role_key), if activation count exceeds the regulator's
   threshold, suppress lowest-scoring neurons down to max_survivors.

2. Redundancy suppression (chandelier cell analogue):
   Within each region, suppress neurons whose embedding cosine similarity > threshold
   to an already-selected neuron. Keeps the higher-scoring one.

3. Cross-reference floor guarantee (Martinotti cell analogue):
   Ensures minimum representation for cross-referenced departments by pulling in
   highest-scoring candidates from underrepresented regions.

Feature-flagged via settings.inhibition_enabled.
"""

import json
import re
import numpy as np

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import InhibitoryRegulator, Neuron
from app.services.scoring_engine import NeuronScoreBreakdown


_REDUNDANCY_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _normalized_memory_text(content: str | None, summary: str | None) -> str:
    """Canonical lexical form used only as deterministic duplicate evidence."""
    text_value = (content or summary or "").lower()
    return " ".join(_REDUNDANCY_TOKEN_RE.findall(text_value))


def _lexical_overlap(left: str, right: str) -> tuple[float, float]:
    """Return (Jaccard, shorter-set containment) for normalized texts."""
    a, b = set(left.split()), set(right.split())
    if not a or not b:
        return 0.0, 0.0
    intersection = len(a & b)
    return intersection / len(a | b), intersection / min(len(a), len(b))


def _deterministic_duplicate(
    left: dict,
    right: dict,
    cosine_threshold: float,
) -> bool:
    """Conservative duplicate verdict; cosine alone is never sufficient.

    Scope equality protects situated/complementary truths. Exact normalized
    content is sufficient within a scope. Near duplicates additionally need a
    high embedding cosine AND strong lexical agreement, preventing the known
    consolidation failure where semantically similar complementary memories
    outrank true duplicates by cosine.
    """
    if left["scope"] != right["scope"]:
        return False
    ltext, rtext = left["text"], right["text"]
    if not ltext or not rtext:
        return False
    if ltext == rtext:
        return True

    lemb, remb = left.get("embedding"), right.get("embedding")
    if lemb is None or remb is None or len(lemb) != len(remb):
        return False
    cosine = float(np.dot(lemb, remb))
    if cosine < cosine_threshold:
        return False
    jaccard, containment = _lexical_overlap(ltext, rtext)
    return jaccard >= 0.82 and containment >= 0.92


async def apply_token_bounded_redundancy(
    db: AsyncSession,
    scored: list[NeuronScoreBreakdown],
) -> tuple[list[NeuronScoreBreakdown], int]:
    """Suppress deterministic duplicates without imposing a survivor target."""
    if not scored:
        return [], 0
    ids = [s.neuron_id for s in scored]
    rows = (await db.execute(
        select(
            Neuron.id, Neuron.department, Neuron.role_key, Neuron.content,
            Neuron.summary, Neuron.embedding,
        ).where(Neuron.id.in_(ids))
    )).all()
    meta: dict[int, dict] = {}
    for nid, department, role_key, content, summary, embedding_json in rows:
        embedding = None
        if embedding_json:
            try:
                embedding = np.asarray(json.loads(embedding_json), dtype=np.float32)
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        meta[nid] = {
            "scope": (department or "", role_key or ""),
            "text": _normalized_memory_text(content, summary),
            "embedding": embedding,
        }

    survivors: list[NeuronScoreBreakdown] = []
    kept_meta: list[dict] = []
    suppressed = 0
    for score in scored:
        candidate = meta.get(score.neuron_id)
        if candidate is not None and any(
            _deterministic_duplicate(candidate, prior, settings.inhibition_redundancy_cosine)
            for prior in kept_meta
        ):
            suppressed += 1
            continue
        survivors.append(score)
        if candidate is not None:
            kept_meta.append(candidate)
    return survivors, suppressed


async def _load_neuron_metadata(
    db: AsyncSession, neuron_ids: list[int],
) -> tuple[dict[int, str], dict[int, str], dict[int, list[float]], dict[int, list[str]]]:
    meta_result = await db.execute(
        select(Neuron.id, Neuron.department, Neuron.role_key, Neuron.embedding, Neuron.cross_ref_departments)
        .where(Neuron.id.in_(neuron_ids))
    )
    dept_map: dict[int, str] = {}
    role_map: dict[int, str] = {}
    emb_map: dict[int, list[float]] = {}
    cross_ref_map: dict[int, list[str]] = {}

    for nid, dept, role, emb_json, xref in meta_result.all():
        dept_map[nid] = dept or ""
        role_map[nid] = role or ""
        if emb_json:
            try:
                emb_map[nid] = json.loads(emb_json)
            except (json.JSONDecodeError, TypeError):
                pass
        if xref:
            try:
                cross_ref_map[nid] = json.loads(xref)
            except (json.JSONDecodeError, TypeError):
                pass

    return dept_map, role_map, emb_map, cross_ref_map


async def _load_region_regulators(db: AsyncSession) -> dict[str, InhibitoryRegulator]:
    reg_result = await db.execute(
        select(InhibitoryRegulator).where(InhibitoryRegulator.is_active == True)
    )
    region_regulators: dict[str, InhibitoryRegulator] = {}
    for reg in reg_result.scalars().all():
        region_regulators[f"{reg.region_type}:{reg.region_value}"] = reg
    return region_regulators


def _apply_density_suppression(
    top_k: list[NeuronScoreBreakdown],
    dept_map: dict[int, str],
    region_regulators: dict[str, InhibitoryRegulator],
) -> tuple[list[NeuronScoreBreakdown], set[int]]:
    # --- Pass 1: Regional density suppression (basket cell) ---
    suppressed_ids: set[int] = set()

    dept_groups: dict[str, list[NeuronScoreBreakdown]] = {}
    for s in top_k:
        dept = dept_map.get(s.neuron_id, "")
        dept_groups.setdefault(dept, []).append(s)

    for dept, group in dept_groups.items():
        reg = region_regulators.get(f"department:{dept}")
        threshold = reg.activation_threshold if reg else settings.inhibition_default_threshold
        max_surv = reg.max_survivors if reg else settings.inhibition_default_max_survivors

        if len(group) > threshold:
            group.sort(key=lambda s: s.combined, reverse=True)
            for s in group[max_surv:]:
                suppressed_ids.add(s.neuron_id)

            if reg:
                reg.total_activations += 1
                reg.total_suppressions += len(group) - max_surv

    survivors = [s for s in top_k if s.neuron_id not in suppressed_ids]
    return survivors, suppressed_ids


def _apply_redundancy_suppression(
    survivors: list[NeuronScoreBreakdown],
    dept_map: dict[int, str],
    emb_map: dict[int, list[float]],
) -> tuple[list[NeuronScoreBreakdown], set[int]]:
    # --- Pass 2: Redundancy suppression (chandelier cell) ---
    redundant_ids: set[int] = set()

    dept_surv_groups: dict[str, list[NeuronScoreBreakdown]] = {}
    for s in survivors:
        dept_surv_groups.setdefault(dept_map.get(s.neuron_id, ""), []).append(s)

    for dept, group in dept_surv_groups.items():
        if len(group) < 2:
            continue

        group_embs = []
        group_ids = []
        for s in group:
            if s.neuron_id in emb_map:
                group_embs.append(emb_map[s.neuron_id])
                group_ids.append(s.neuron_id)

        if len(group_embs) < 2:
            continue

        _suppress_redundant_in_group(group_ids, group_embs, redundant_ids)

    survivors = [s for s in survivors if s.neuron_id not in redundant_ids]
    return survivors, redundant_ids


def _suppress_redundant_in_group(
    group_ids: list[int],
    group_embs: list[list[float]],
    redundant_ids: set[int],
) -> None:
    mat = np.array(group_embs, dtype=np.float32)
    sims = mat @ mat.T

    cosine_threshold = settings.inhibition_redundancy_cosine
    for i in range(len(group_ids) - 1, 0, -1):
        if group_ids[i] in redundant_ids:
            continue
        for j in range(i):
            if group_ids[j] in redundant_ids:
                continue
            if sims[i][j] >= cosine_threshold:
                redundant_ids.add(group_ids[i])
                break


def _apply_crossref_floor(
    survivors: list[NeuronScoreBreakdown],
    top_k: list[NeuronScoreBreakdown],
    below: list[NeuronScoreBreakdown],
    dept_map: dict[int, str],
    cross_ref_map: dict[int, list[str]],
    suppressed_ids: set[int],
    redundant_ids: set[int],
) -> list[NeuronScoreBreakdown]:
    # --- Pass 3: Cross-reference floor guarantee (Martinotti cell) ---
    cross_ref_depts: set[str] = set()
    for s in survivors:
        cross_ref_depts.update(cross_ref_map.get(s.neuron_id, []))

    if not cross_ref_depts:
        return survivors

    surv_dept_counts: dict[str, int] = {}
    for s in survivors:
        dept = dept_map.get(s.neuron_id, "")
        surv_dept_counts[dept] = surv_dept_counts.get(dept, 0) + 1

    min_floor = settings.diversity_floor_min
    surv_ids = {s.neuron_id for s in survivors}
    additions: list[NeuronScoreBreakdown] = []
    rejected_ids = suppressed_ids | redundant_ids

    for dept in cross_ref_depts:
        current = surv_dept_counts.get(dept, 0)
        if current >= min_floor:
            continue
        needed = min_floor - current
        all_below = below + [s for s in top_k if s.neuron_id in rejected_ids]
        for s in all_below:
            if s.neuron_id in surv_ids:
                continue
            if dept_map.get(s.neuron_id, "") == dept:
                additions.append(s)
                surv_ids.add(s.neuron_id)
                needed -= 1
                if needed <= 0:
                    break

    survivors = survivors + additions
    return survivors


def _reassemble_result(
    survivors: list[NeuronScoreBreakdown],
    top_k: list[NeuronScoreBreakdown],
    below: list[NeuronScoreBreakdown],
    suppressed_ids: set[int],
    redundant_ids: set[int],
    top_k_count: int,
    input_length: int,
) -> tuple[list[NeuronScoreBreakdown], int]:
    survivors.sort(key=lambda s: s.combined, reverse=True)

    surv_ids = {s.neuron_id for s in survivors}
    remaining = [s for s in below if s.neuron_id not in surv_ids]
    suppressed_below = [s for s in top_k if s.neuron_id in (suppressed_ids | redundant_ids) and s.neuron_id not in surv_ids]
    suppressed_below.sort(key=lambda s: s.combined, reverse=True)

    survivor_count = len(survivors)
    assert survivor_count <= top_k_count, f"survivor_count {survivor_count} exceeds top_k_count {top_k_count}"
    result_list = survivors + suppressed_below + remaining
    assert len(result_list) >= input_length, f"Inhibition lost neurons: {len(result_list)} < {input_length}"
    return result_list, survivor_count


async def apply_inhibition(
    db: AsyncSession,
    scored: list[NeuronScoreBreakdown],
    top_k_count: int,
) -> tuple[list[NeuronScoreBreakdown], int]:
    """Apply 3-pass inhibitory regulation to the scored neuron list.

    Returns (full_list, survivor_count) where survivor_count is the number of
    neurons that passed inhibition. The executor should use survivor_count as
    the effective top-K for prompt assembly and firing.
    """
    assert top_k_count > 0, f"top_k_count must be positive, got {top_k_count}"
    input_length = len(scored)

    if not settings.inhibition_enabled or not scored:
        return scored, min(top_k_count, len(scored))

    top_k = scored[:top_k_count]
    below = scored[top_k_count:]

    if not top_k:
        return scored

    top_ids = [s.neuron_id for s in top_k]
    dept_map, role_map, emb_map, cross_ref_map = await _load_neuron_metadata(db, top_ids)
    region_regulators = await _load_region_regulators(db)

    survivors, suppressed_ids = _apply_density_suppression(top_k, dept_map, region_regulators)
    survivors, redundant_ids = _apply_redundancy_suppression(survivors, dept_map, emb_map)
    survivors = _apply_crossref_floor(
        survivors, top_k, below, dept_map, cross_ref_map, suppressed_ids, redundant_ids,
    )

    return _reassemble_result(survivors, top_k, below, suppressed_ids, redundant_ids, top_k_count, input_length)


async def seed_inhibitory_regulators(db: AsyncSession) -> int:
    """Create default inhibitory regulators for each department and high-volume role.

    Returns the number of regulators created.
    """
    # Get all departments
    dept_result = await db.execute(
        text("SELECT DISTINCT department FROM neurons WHERE department IS NOT NULL AND is_active = true")
    )
    departments = [r[0] for r in dept_result.all()]

    # Get high-volume roles (>20 neurons)
    role_result = await db.execute(
        text("""
            SELECT department, role_key, COUNT(*) as cnt
            FROM neurons
            WHERE role_key IS NOT NULL AND is_active = true
            GROUP BY department, role_key
            HAVING COUNT(*) > 20
        """)
    )
    high_volume_roles = role_result.all()

    created = 0

    # Department-level regulators
    for dept in departments:
        existing = await db.execute(
            select(InhibitoryRegulator).where(
                InhibitoryRegulator.region_type == "department",
                InhibitoryRegulator.region_value == dept,
            )
        )
        if existing.scalar_one_or_none() is None:
            db.add(InhibitoryRegulator(
                region_type="department",
                region_value=dept,
                activation_threshold=settings.inhibition_default_threshold,
                max_survivors=settings.inhibition_default_max_survivors,
                redundancy_cosine_threshold=settings.inhibition_redundancy_cosine,
            ))
            created += 1

    # Role-level regulators for high-volume roles
    for dept, role_key, cnt in high_volume_roles:
        existing = await db.execute(
            select(InhibitoryRegulator).where(
                InhibitoryRegulator.region_type == "role_key",
                InhibitoryRegulator.region_value == role_key,
            )
        )
        if existing.scalar_one_or_none() is None:
            db.add(InhibitoryRegulator(
                region_type="role_key",
                region_value=role_key,
                activation_threshold=max(10, cnt // 3),
                max_survivors=max(5, cnt // 5),
                redundancy_cosine_threshold=settings.inhibition_redundancy_cosine,
            ))
            created += 1

    await db.flush()
    return created
