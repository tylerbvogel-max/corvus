"""Conflict monitoring — contradiction detection in neuron content.

Biological analogue: the anterior cingulate cortex (ACC) detects when two
active representations contradict each other. It doesn't resolve the conflict
itself — it flags it for higher-order (prefrontal) adjudication.

Two-phase approach:
  1. Embedding prefilter: find pairs in the "contradiction zone" (similar
     enough to potentially conflict, not so similar as to be duplicates)
  2. LLM classification: batch pairs through Haiku to classify as
     consistent, contradictory, or ambiguous

Resolution paths (human-initiated):
  - A correct, B wrong: update/deactivate B
  - Both correct, needs context: add distinguishing detail to both
  - Unresolved: flag for domain expert
"""

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Neuron, IntegrityScan, IntegrityFinding
from app.services.integrity import IntegrityFindingData, IntegrityScanResult
from app.services.integrity.similarity import (
    load_neuron_embeddings, compute_pairwise_similarity,
    extract_pairs_in_range, SimilarPair,
)

_CLASSIFY_SYSTEM_PROMPT = """You are analyzing pairs of knowledge base entries for contradictions.
For each pair, classify the relationship as one of:
- "consistent": the entries are compatible, no contradiction
- "contradictory": the entries assert incompatible facts
- "ambiguous": the entries may conflict but need more context to determine

Return a JSON array of objects, one per pair:
[{"pair_index": 0, "classification": "consistent|contradictory|ambiguous", "reasoning": "brief explanation"}]

Be precise. Two entries about different aspects of the same topic are NOT contradictory.
Entries that apply to different materials, processes, or contexts are NOT contradictory.
Only flag genuine factual conflicts where both cannot be simultaneously true."""


async def _load_neuron_content(
    db: AsyncSession, neuron_ids: list[int],
) -> dict[int, tuple[str, str]]:
    """Load (label, content) for a list of neuron IDs."""
    result = await db.execute(
        select(Neuron.id, Neuron.label, Neuron.content)
        .where(Neuron.id.in_(neuron_ids))
    )
    content_map: dict[int, tuple[str, str]] = {}
    for row in result.all():
        content_map[row[0]] = (row[1], row[2] or "")
    assert isinstance(content_map, dict), "Must return a dict"
    return content_map


def _format_pair_prompt(
    pairs: list[SimilarPair],
    content_map: dict[int, tuple[str, str]],
) -> str:
    """Format pairs into a user prompt for LLM classification."""
    lines: list[str] = []
    for i, pair in enumerate(pairs):
        a_label, a_content = content_map.get(pair.neuron_a_id, ("?", ""))
        b_label, b_content = content_map.get(pair.neuron_b_id, ("?", ""))
        lines.append(f"--- Pair {i} ---")
        lines.append(f"Entry A [{pair.neuron_a_id}]: {a_label}")
        lines.append(f"Content: {a_content[:500]}")
        lines.append(f"Entry B [{pair.neuron_b_id}]: {b_label}")
        lines.append(f"Content: {b_content[:500]}")
        lines.append("")
    assert len(lines) > 0, "Must have at least one pair to format"
    return "\n".join(lines)


async def _classify_batch(
    pairs: list[SimilarPair],
    content_map: dict[int, tuple[str, str]],
    model: str = "haiku",
) -> list[dict]:
    """Call LLM to classify a batch of pairs."""
    from app.services.llm_provider import llm_chat

    user_prompt = _format_pair_prompt(pairs, content_map)
    result = await llm_chat(
        system_prompt=_CLASSIFY_SYSTEM_PROMPT,
        user_message=user_prompt,
        model=model,
        max_tokens=1024,
    )

    text = result.get("text", "")
    # Extract JSON from response
    try:
        # Handle possible markdown code blocks
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        classifications = json.loads(text.strip())
        assert isinstance(classifications, list), "LLM must return a list"
    except (json.JSONDecodeError, IndexError, AssertionError):
        classifications = []

    return classifications


def _build_conflict_finding(
    pair: SimilarPair,
    classification: str,
    reasoning: str,
) -> IntegrityFindingData:
    """Build a finding for a contradictory or ambiguous pair."""
    severity = "warning" if classification == "contradictory" else "info"
    assert classification in ("contradictory", "ambiguous"), "Invalid classification"

    detail = {
        "neuron_a": {"id": pair.neuron_a_id, "label": pair.a_label,
                     "department": pair.a_department, "layer": pair.a_layer},
        "neuron_b": {"id": pair.neuron_b_id, "label": pair.b_label,
                     "department": pair.b_department, "layer": pair.b_layer},
        "cosine_similarity": round(pair.similarity, 4),
        "classification": classification,
        "llm_reasoning": reasoning,
    }

    return IntegrityFindingData(
        finding_type="contradiction",
        severity=severity,
        priority_score=0.8 if classification == "contradictory" else 0.5,
        description=(
            f"{classification.title()}: '{pair.a_label}' ↔ '{pair.b_label}' — {reasoning}"
        ),
        detail_json=json.dumps(detail),
        neuron_ids=[pair.neuron_a_id, pair.neuron_b_id],
    )


async def scan_contradictions(
    db: AsyncSession,
    scope: str = "global",
    sim_min: float | None = None,
    sim_max: float | None = None,
    batch_size: int = 5,
    max_pairs: int = 200,
    initiated_by: str | None = None,
) -> tuple[IntegrityScan, IntegrityScanResult]:
    """Scan for contradictory neuron content using embedding prefilter + LLM.

    Phase 1: Find pairs in similarity range (too dissimilar = can't contradict,
    too similar = duplicate, not contradiction).
    Phase 2: Classify via Claude CLI (haiku) in batches.
    """
    s_min = sim_min if sim_min is not None else settings.integrity_conflict_sim_min
    s_max = sim_max if sim_max is not None else settings.integrity_conflict_sim_max
    assert 0.0 <= s_min < s_max <= 1.0, "Invalid similarity range"

    scan = IntegrityScan(
        scan_type="conflict_monitor", scope=scope, status="running",
        parameters_json=json.dumps({
            "sim_min": s_min, "sim_max": s_max,
            "batch_size": batch_size, "max_pairs": max_pairs,
        }),
        initiated_by=initiated_by,
    )
    db.add(scan)
    await db.flush()

    metadata, matrix = await load_neuron_embeddings(
        db, scope=scope, max_neurons=settings.integrity_max_scan_neurons,
    )

    if matrix.shape[0] < 2:
        scan.status = "completed"
        scan.completed_at = datetime.utcnow()
        scan.findings_count = 0
        await db.commit()
        return scan, IntegrityScanResult(scan_type="conflict_monitor", scope=scope)

    sim_matrix = compute_pairwise_similarity(matrix)
    candidates = extract_pairs_in_range(sim_matrix, metadata, s_min, s_max, max_pairs)

    findings_data = await _classify_and_build_findings(db, candidates, batch_size)

    _persist_findings(db, scan, findings_data)

    scan.status = "completed"
    scan.completed_at = datetime.utcnow()
    scan.findings_count = len(findings_data)
    await db.commit()

    return scan, IntegrityScanResult(
        scan_type="conflict_monitor", scope=scope,
        findings=findings_data,
        extra={
            "neurons_scanned": matrix.shape[0],
            "candidates_checked": len(candidates),
            "conflicts_found": len(findings_data),
        },
    )


async def _classify_and_build_findings(
    db: AsyncSession,
    candidates: list[SimilarPair],
    batch_size: int,
) -> list[IntegrityFindingData]:
    """Classify candidate pairs in batches via LLM, return findings."""
    if not candidates:
        return []

    # Load content for all candidate neurons
    all_ids = set()
    for p in candidates:
        all_ids.add(p.neuron_a_id)
        all_ids.add(p.neuron_b_id)
    content_map = await _load_neuron_content(db, list(all_ids))

    findings: list[IntegrityFindingData] = []

    # Process in batches
    for start in range(0, len(candidates), batch_size):
        batch = candidates[start:start + batch_size]
        classifications = await _classify_batch(batch, content_map)

        for cls in classifications:
            idx = cls.get("pair_index", -1)
            classification = cls.get("classification", "consistent")
            reasoning = cls.get("reasoning", "")

            if 0 <= idx < len(batch) and classification in ("contradictory", "ambiguous"):
                findings.append(_build_conflict_finding(
                    batch[idx], classification, reasoning,
                ))

    return findings


def _persist_findings(
    db: AsyncSession,
    scan: IntegrityScan,
    findings_data: list[IntegrityFindingData],
) -> None:
    """Persist findings to the database."""
    assert scan.id is not None, "Scan must be flushed"
    for fd in findings_data:
        finding = IntegrityFinding(
            scan_id=scan.id, finding_type=fd.finding_type,
            severity=fd.severity, priority_score=fd.priority_score,
            description=fd.description, detail_json=fd.detail_json,
            neuron_ids_json=json.dumps(fd.neuron_ids),
        )
        db.add(finding)
