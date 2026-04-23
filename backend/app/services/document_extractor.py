"""Pass 2: LLM-driven knowledge extraction from parsed document sections.

For each section, builds a context-aware prompt, calls the LLM to extract
neuron proposals, checks for semantic duplicates, and creates AutopilotProposals
in the existing proposal queue with gap_source="document_ingest".
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models import (
    AutopilotProposal,
    DocumentIngestJob,
    Neuron,
    ProposalItem,
)
from app.services.document_parser import DocumentStructure, Section
from app.services.embedding_service import batch_cosine_similarity, embed_text
from app.services.llm_provider import llm_chat

logger = logging.getLogger(__name__)

# Similarity threshold for flagging duplicates
DUPLICATE_THRESHOLD = 0.85

# Max section text length sent to the LLM (chars) — chunked-path only.
MAX_SECTION_CHARS = 12_000

# Whole-doc path threshold: docs under this many chars go through one LLM call
# with the full text in the prompt. Docs over the threshold fall back to the
# per-section chunked path. ~600k chars ≈ 150k tokens, which leaves comfortable
# headroom in Opus/Sonnet 200k context windows for system + output.
WHOLE_DOC_THRESHOLD_CHARS = 600_000

# Max tokens the whole-doc path asks the LLM to return. 8k leaves comfortable
# room for ~40-50 proposals at ~200 tokens each without pushing Opus into
# multi-minute-per-call generation territory. If a doc genuinely has more
# proposals than this fits, the LLM truncates gracefully and the human
# reviewer can request re-extraction with a tighter prompt.
WHOLE_DOC_MAX_OUTPUT_TOKENS = 8_192

# Timeout for the single whole-doc LLM call (seconds). Opus on ~30k input +
# 8k output typically finishes in 5-8 minutes; 12 min cap gives headroom.
WHOLE_DOC_TIMEOUT_S = 720

# Retry configuration: 3 attempts with exponential backoff (10s, 20s)
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 10


async def _get_existing_neurons(
    db: AsyncSession,
    department: str | None,
) -> list[dict]:
    """Fetch active neurons in the target department for dedup context."""
    query = select(Neuron).where(Neuron.is_active.is_(True))
    if department:
        query = query.where(Neuron.department == department)
    query = query.order_by(Neuron.layer, Neuron.id)
    result = await db.execute(query)
    neurons = result.scalars().all()

    return [
        {
            "id": n.id,
            "label": n.label,
            "layer": n.layer,
            "department": n.department,
            "role_key": n.role_key,
            "parent_id": n.parent_id,
            "summary": n.summary or "",
            "embedding": n.embedding,
        }
        for n in neurons
    ]


def _build_extraction_prompt(
    section: Section,
    section_text: str,
    doc_title: str,
    toc_outline: str,
    existing_neurons_summary: str,
    department: str | None,
    role_key: str | None,
) -> tuple[str, str]:
    """Build system + user prompt for knowledge extraction.

    Returns (system_prompt, user_message).
    """
    system_prompt = """You are a knowledge extraction specialist for Corvus, a hierarchical neuron graph system.

Your task: extract structured knowledge from a document section and propose neurons for the graph.

The graph has 6 layers:
- Layer 0: Department (top-level organizational unit)
- Layer 1: Role (functional role within a department)
- Layer 2: Task (specific task or process)
- Layer 3: System (system, tool, or standard involved)
- Layer 4: Decision (decision point, rule, or criterion)
- Layer 5: Output (deliverable, metric, or communication)

For each piece of extractable knowledge, output a JSON object with:
- "action": "create" (new neuron) or "update" (modify existing)
- "label": concise title (max 200 chars)
- "content": full knowledge content (detailed, actionable)
- "summary": one-line summary (max 500 chars)
- "layer": integer 2-5 (departments and roles are pre-existing)
- "node_type": "knowledge" | "process" | "standard" | "decision" | "metric"
- "parent_label": label of the parent neuron this should attach to (use existing neuron labels when possible)
- "reason": why this knowledge is valuable for the graph

For updates to existing neurons:
- "action": "update"
- "target_label": label of the existing neuron to update
- "field": "content" or "summary"
- "new_value": the updated text
- "reason": what new information this adds

Output ONLY a JSON array of objects. No markdown, no explanation outside the array."""

    dept_context = f"Target department: {department}" if department else "No specific department targeted"
    role_context = f"Target role: {role_key}" if role_key else ""

    user_message = f"""Document: "{doc_title}"
Section: "{section.title}" (Level {section.level})

Table of Contents:
{toc_outline}

{dept_context}
{role_context}

Existing neurons in this area (for deduplication and parent matching):
{existing_neurons_summary}

--- SECTION TEXT ---
{section_text[:MAX_SECTION_CHARS]}
--- END SECTION TEXT ---

Extract all valuable knowledge from this section as neuron proposals. Focus on:
1. Actionable processes, procedures, and best practices
2. Standards, rules, and decision criteria
3. Metrics, KPIs, and quality thresholds
4. System interactions and tool requirements

Skip: table of contents entries, blank sections, boilerplate disclaimers, pure definitions without actionable context.

If the section contains no extractable knowledge, return an empty array: []"""

    return system_prompt, user_message


def _build_toc_outline(structure: DocumentStructure) -> str:
    """Build a compact TOC string from the document structure."""
    lines = []
    for sec in structure.sections[:50]:  # Cap at 50 entries
        indent = "  " * (sec.level - 1)
        lines.append(f"{indent}{sec.title}")
    return "\n".join(lines) if lines else "(no TOC detected)"


def _build_neuron_summary(neurons: list[dict], limit: int = 80) -> str:
    """Build a compact summary of existing neurons for the prompt."""
    if not neurons:
        return "(no existing neurons in this area)"

    lines = []
    for n in neurons[:limit]:
        lines.append(f"- [{n['layer']}] {n['label']}: {n['summary'][:100]}")
    suffix = f"\n... and {len(neurons) - limit} more" if len(neurons) > limit else ""
    return "\n".join(lines) + suffix


def _inject_page_markers(text: str, structure: DocumentStructure) -> str:
    """Insert [PAGE N] markers into the text at section boundaries.

    Coarse-grained (only at section starts, not every page) but sufficient
    for the LLM to cite pages correctly. No-op if structure has no sections
    with page data.
    """
    sections_with_pages = [
        s for s in structure.sections
        if getattr(s, "page_start", None) is not None
    ]
    if not sections_with_pages:
        return text

    sorted_secs = sorted(sections_with_pages, key=lambda s: s.char_start)
    parts: list[str] = []
    last_offset = 0
    last_page = 0
    for sec in sorted_secs:
        if sec.char_start > last_offset:
            parts.append(text[last_offset:sec.char_start])
        if sec.page_start != last_page:
            parts.append(f"\n[PAGE {sec.page_start}]\n")
            last_page = sec.page_start
        last_offset = sec.char_start
    parts.append(text[last_offset:])
    return "".join(parts)


_WHOLE_DOC_SYSTEM_PROMPT = """You are a knowledge extraction specialist for Corvus, a hierarchical neuron graph system. You are processing one regulatory / technical document end-to-end to produce a proposal list that will be reviewed by a human approver.

The graph has 6 layers:
- Layer 0: Department (top-level organizational unit)
- Layer 1: Role (functional role within a department)
- Layer 2: Task (specific task or process)
- Layer 3: System (system, tool, or standard involved)
- Layer 4: Decision (decision point, rule, or criterion)
- Layer 5: Output (deliverable, metric, or communication)

Your job: enumerate every substantive requirement, definition, or constraint in this document as a proposed neuron. One subsection typically maps to one proposal. Favor completeness — the human reviewer will dismiss what they don't need. Sparse extraction is worse than noisy extraction.

For each proposal, output a JSON object with:
- "action": "create" (new neuron) or "update" (modify existing)
- "section": the section number as it appears in the source doc (e.g. "5.1.3.5") — use "" if not applicable
- "page": page number the content begins on (integer). Use the [PAGE N] markers in the text to determine this
- "label": concise title (max 200 chars)
- "content": full requirement text. Preserve numeric thresholds, UTS values, specification numbers, and approval triggers verbatim. Quote liberally
- "summary": one-line summary (max 500 chars)
- "layer": integer 2-5 (departments and roles are pre-existing)
- "node_type": "knowledge" | "process" | "standard" | "decision" | "metric"
- "parent_label": label of the parent neuron this should attach to (use an existing neuron label when possible)
- "reason": why this knowledge is valuable for the graph

Skip: cover page, table of contents, revision history, page footers, the long lists of applicable-document references in section 2 (they're pointers to other specs, not content). Focus on Definitions, General Requirements, Detailed Requirements, Notes, and Tables.

OUTPUT FORMAT — strict requirements:
- Begin your response with the character '[' and end it with the character ']'.
- Emit ONE JSON array at the top level. No prose, no preface, no summary, no markdown fences, no trailing commentary.
- If you have nothing to emit, return the empty array [].
- Your response will be parsed as json.loads(...) directly; anything outside a valid JSON array is discarded."""


def _build_whole_doc_user_message(
    doc_title: str, toc_outline: str, paginated_text: str,
    existing_neurons_summary: str, department: str | None, role_key: str | None,
    request_nonce: str = "",
) -> str:
    dept_context = f"Target department: {department}" if department else "No specific department targeted"
    role_context = f"Target role: {role_key}" if role_key else ""
    nonce_line = f"Request id: {request_nonce} (fresh extraction — do not treat as a continuation)\n\n" if request_nonce else ""
    return f"""{nonce_line}Document: "{doc_title}"

Detected structure (table of contents):
{toc_outline}

{dept_context}
{role_context}

Existing neurons in this area (for deduplication and parent matching):
{existing_neurons_summary}

--- FULL DOCUMENT TEXT ---
{paginated_text}
--- END DOCUMENT TEXT ---

This is a STANDALONE request, not a continuation of any prior conversation. Produce the JSON proposal array now in ONE self-contained response. Aim for thorough coverage of every substantive subsection — if this document has 30 subsections with real requirements, you should emit 30 proposals (not 5 that summarize them)."""


def _build_whole_doc_prompt(
    doc_title: str,
    toc_outline: str,
    paginated_text: str,
    existing_neurons_summary: str,
    department: str | None,
    role_key: str | None,
    request_nonce: str = "",
) -> tuple[str, str]:
    """Build system + user prompt for whole-document proposal enumeration."""
    user_message = _build_whole_doc_user_message(
        doc_title, toc_outline, paginated_text,
        existing_neurons_summary, department, role_key,
        request_nonce=request_nonce,
    )
    return _WHOLE_DOC_SYSTEM_PROMPT, user_message


async def extract_whole_document(
    job: DocumentIngestJob,
    extracted_text: str,
    structure: DocumentStructure,
    existing_neurons: list[dict],
) -> tuple[list[dict], dict, str]:
    """Whole-doc path: one LLM call, full text, enumerative proposal list.

    Returns (proposals_list, usage_dict, raw_text). The raw text is the
    unparsed LLM response so the caller can record it for diagnosis when
    parsing yields 0 proposals. Each proposal carries an optional 'section'
    string and 'page' integer identifying where in the source it came from;
    the caller groups by section and persists proposal rows.
    """
    paginated_text = _inject_page_markers(extracted_text, structure)
    toc_outline = _build_toc_outline(structure)
    neuron_summary = _build_neuron_summary(existing_neurons)

    system_prompt, user_message = _build_whole_doc_prompt(
        doc_title=structure.title,
        toc_outline=toc_outline,
        paginated_text=paginated_text,
        existing_neurons_summary=neuron_summary,
        department=job.department,
        role_key=job.role_key,
        request_nonce=job.id,  # cache-bust: each run gets a unique prefix
    )

    result = await llm_chat(
        system_prompt=system_prompt,
        user_message=user_message,
        max_tokens=WHOLE_DOC_MAX_OUTPUT_TOKENS,
        model=job.model,
        timeout=WHOLE_DOC_TIMEOUT_S,
    )

    usage = {
        "input_tokens": result.get("input_tokens", 0),
        "output_tokens": result.get("output_tokens", 0),
        "cost_usd": result.get("cost_usd", 0.0),
    }

    text = result.get("text", "").strip()
    proposals = _parse_llm_proposals(text)
    return proposals, usage, text


async def extract_section_knowledge(
    section: Section,
    section_text: str,
    doc_title: str,
    toc_outline: str,
    existing_neurons: list[dict],
    department: str | None,
    role_key: str | None,
    model: str,
) -> tuple[list[dict], dict]:
    """Extract knowledge from one section via LLM.

    Returns (proposals_list, usage_dict).
    """
    neuron_summary = _build_neuron_summary(existing_neurons)
    system_prompt, user_message = _build_extraction_prompt(
        section, section_text, doc_title, toc_outline,
        neuron_summary, department, role_key,
    )

    result = await llm_chat(
        system_prompt=system_prompt,
        user_message=user_message,
        max_tokens=4096,
        model=model,
        timeout=300,
    )

    usage = {
        "input_tokens": result.get("input_tokens", 0),
        "output_tokens": result.get("output_tokens", 0),
        "cost_usd": result.get("cost_usd", 0.0),
    }

    # Parse LLM response
    text = result.get("text", "").strip()
    proposals = _parse_llm_proposals(text)

    return proposals, usage


def _strip_code_fences(text: str) -> str:
    """Remove ``` fences from a fenced code block, leaving inner content."""
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    lines = [l for l in lines if not l.strip().startswith("```")]
    return "\n".join(lines).strip()


def _find_balanced_span(text: str, open_ch: str, close_ch: str, start_from: int = 0) -> tuple[int, int] | None:
    """Find the outermost `open_ch`...`close_ch` balanced span starting at or after start_from.

    Skips bracket chars that appear inside string literals. Returns (start, end_inclusive)
    or None. End is inclusive of the closing char.
    """
    start = text.find(open_ch, start_from)
    if start < 0:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return (start, i)
    return None


def _find_json_array_span(text: str) -> str | None:
    """Find the outermost [...]  JSON array in text, ignoring surrounding prose."""
    span = _find_balanced_span(text, "[", "]", 0)
    return text[span[0]:span[1] + 1] if span else None


def _find_all_json_objects(text: str) -> list[dict]:
    """Extract every top-level balanced {...} block that parses as a JSON object.

    Fallback for when the LLM emits individual objects (often fenced
    ```json blocks) instead of a single top-level array. Each balanced
    {...} span is tried individually; failures are skipped.
    """
    objects: list[dict] = []
    pos = 0
    while pos < len(text):
        span = _find_balanced_span(text, "{", "}", pos)
        if span is None:
            break
        start, end = span
        chunk = text[start:end + 1]
        try:
            parsed = json.loads(chunk)
            if isinstance(parsed, dict):
                objects.append(parsed)
        except json.JSONDecodeError:
            pass
        pos = end + 1
    return objects


def _parse_llm_proposals(text: str) -> list[dict]:
    """Parse the LLM's JSON array response, tolerant of surrounding prose.

    Strategy, in order:
      1. Strip ``` fences and try json.loads on the whole string.
      2. Scan for the outermost [...] array and json.loads that span.
      3. Fallback: extract every balanced {...} object (handles cases where
         the LLM emitted individual fenced objects instead of a single array,
         e.g. when it thinks it's continuing a truncated prior response).

    Returns [] on any failure; the caller logs + records the raw output.
    """
    text = _strip_code_fences(text).strip()
    if not text or text == "[]":
        return []

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    span = _find_json_array_span(text)
    if span is not None:
        try:
            parsed = json.loads(span)
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError as exc:
            logger.warning(
                "JSON array span failed to parse (err=%s, first 200): %r",
                exc, span[:200],
            )

    # Last resort: extract individual {...} objects scattered through prose.
    objects = _find_all_json_objects(text)
    if objects:
        logger.info("Parser fallback: extracted %d individual {...} objects", len(objects))
        return objects

    logger.warning(
        "No parseable JSON found in LLM extraction response (first 200 chars): %r",
        text[:200],
    )
    return []


async def check_semantic_duplicates(
    proposals: list[dict],
    existing_neurons: list[dict],
) -> list[dict]:
    """Check each create proposal against existing neurons for semantic similarity.

    Adds a "duplicate_of" field to proposals that are too similar to existing neurons.
    Returns the annotated proposals list.
    """
    if not proposals or not existing_neurons:
        return proposals

    # Build embedding vectors for existing neurons that have them
    neurons_with_embeddings = [
        n for n in existing_neurons if n.get("embedding")
    ]
    if not neurons_with_embeddings:
        return proposals

    neuron_vecs = [json.loads(n["embedding"]) for n in neurons_with_embeddings]

    for prop in proposals:
        if prop.get("action") != "create":
            continue

        label = prop.get("label", "")
        content = prop.get("content", "")
        text_to_embed = f"{label}: {content[:500]}"

        prop_vec = embed_text(text_to_embed)
        similarities = batch_cosine_similarity(prop_vec, neuron_vecs)
        max_sim = max(similarities) if similarities else 0.0

        if max_sim >= DUPLICATE_THRESHOLD:
            max_idx = similarities.index(max_sim)
            dup_neuron = neurons_with_embeddings[max_idx]
            prop["duplicate_of"] = {
                "neuron_id": dup_neuron["id"],
                "label": dup_neuron["label"],
                "similarity": round(max_sim, 3),
            }

    return proposals


async def _resolve_parent_id(
    prop: dict,
    existing_neurons: list[dict],
    department: str | None,
    role_key: str | None,
) -> int | None:
    """Resolve a parent neuron ID from the proposal's parent_label."""
    parent_label = prop.get("parent_label", "")
    if not parent_label:
        # Default: find first matching role neuron
        for n in existing_neurons:
            if n["layer"] == 1 and (not department or n["department"] == department):
                if not role_key or n.get("role_key") == role_key:
                    return n["id"]
        # Fallback: first department neuron
        for n in existing_neurons:
            if n["layer"] == 0 and (not department or n["department"] == department):
                return n["id"]
        return None

    # Fuzzy match on label
    parent_label_lower = parent_label.lower().strip()
    best_match = None
    best_score = 0.0

    for n in existing_neurons:
        n_label = n["label"].lower().strip()
        if n_label == parent_label_lower:
            return n["id"]
        # Partial match score
        if parent_label_lower in n_label or n_label in parent_label_lower:
            score = len(min(parent_label_lower, n_label, key=len)) / len(max(parent_label_lower, n_label, key=len))
            if score > best_score:
                best_score = score
                best_match = n

    if best_match and best_score > 0.5:
        return best_match["id"]

    return None


async def _add_create_item(
    db: AsyncSession,
    proposal_id: int,
    prop: dict,
    job: DocumentIngestJob,
    section_label: str,
    existing_neurons: list[dict],
) -> None:
    """Add a create ProposalItem for a single extracted neuron."""
    parent_id = await _resolve_parent_id(
        prop, existing_neurons, job.department, job.role_key,
    )
    spec = {
        "parent_id": parent_id,
        "layer": prop.get("layer", 3),
        "node_type": prop.get("node_type", "knowledge"),
        "label": prop.get("label", ""),
        "content": prop.get("content", ""),
        "summary": prop.get("summary", ""),
        "department": job.department,
        "role_key": job.role_key,
        "source_origin": "document",
        "source_type": job.source_type,
        "citation": job.citation,
        "source_url": job.source_url,
        "authority_level": job.authority_level,
    }
    reason = prop.get("reason", f"Extracted from {job.filename}, section: {section_label}")
    if prop.get("duplicate_of"):
        dup = prop["duplicate_of"]
        reason += f" [DUPLICATE WARNING: {dup['similarity']:.0%} similar to neuron #{dup['neuron_id']} '{dup['label']}']"

    db.add(ProposalItem(
        proposal_id=proposal_id,
        action="create",
        target_neuron_id=parent_id,
        neuron_spec_json=json.dumps(spec),
        reason=reason,
    ))


def _add_update_item(
    db: AsyncSession,
    proposal_id: int,
    prop: dict,
    job: DocumentIngestJob,
    section_label: str,
    existing_neurons: list[dict],
) -> None:
    """Add an update ProposalItem for a single neuron field change."""
    target_label = prop.get("target_label", "")
    target_id = None
    for n in existing_neurons:
        if n["label"].lower().strip() == target_label.lower().strip():
            target_id = n["id"]
            break

    if target_id:
        db.add(ProposalItem(
            proposal_id=proposal_id,
            action="update",
            target_neuron_id=target_id,
            field=prop.get("field", "content"),
            old_value=None,
            new_value=prop.get("new_value", ""),
            reason=prop.get("reason", f"Updated from {job.filename}, section: {section_label}"),
        ))


def _build_evidence(
    job: DocumentIngestJob,
    section_label: str,
    section_ref: str | None,
    page: int | None,
) -> dict:
    """Provenance blob stored in AutopilotProposal.gap_evidence_json."""
    evidence = {
        "source": "document_ingest",
        "document": job.filename,
        "section": section_label,
        "section_id": section_ref,
        "job_id": job.id,
    }
    if page is not None:
        evidence["page"] = page
    return evidence


async def _persist_proposal_group(
    job: DocumentIngestJob,
    *,
    group_key: str,
    section_label: str,
    section_ref: str | None,
    page: int | None,
    proposals: list[dict],
    existing_neurons: list[dict],
    db: AsyncSession,
) -> int | None:
    """Create one AutopilotProposal row + ProposalItem children from a batch.

    Shared implementation used by both the chunked (per-Section) and the
    whole-doc (LLM-emitted section-group) extraction paths.
    """
    if not proposals:
        return None
    valid_proposals = [p for p in proposals if p.get("action") in ("create", "update")]
    if not valid_proposals:
        return None

    prompt_hash = hashlib.sha256(
        f"{job.id}:{group_key}:{job.model}".encode()
    ).hexdigest()
    n_creates = sum(1 for p in valid_proposals if p["action"] == "create")
    n_updates = sum(1 for p in valid_proposals if p["action"] == "update")
    n_dupes = sum(1 for p in valid_proposals if p.get("duplicate_of"))

    proposal = AutopilotProposal(
        state="proposed",
        gap_source="document_ingest",
        gap_description=f"Section: {section_label} (from {job.filename})",
        gap_evidence_json=json.dumps([_build_evidence(job, section_label, section_ref, page)]),
        priority_score=0.5,
        llm_reasoning=f"Extracted {n_creates} new neurons and {n_updates} updates. {n_dupes} potential duplicates.",
        llm_model=job.model,
        prompt_hash=prompt_hash,
        eval_overall=0,
        eval_text=None,
    )
    db.add(proposal)
    await db.flush()

    for prop in valid_proposals:
        if prop["action"] == "create":
            await _add_create_item(db, proposal.id, prop, job, section_label, existing_neurons)
        elif prop["action"] == "update":
            _add_update_item(db, proposal.id, prop, job, section_label, existing_neurons)

    await db.flush()
    return proposal.id


async def create_section_proposal(
    job: DocumentIngestJob,
    section: Section,
    proposals: list[dict],
    existing_neurons: list[dict],
    db: AsyncSession,
) -> int | None:
    """Chunked-path: create an AutopilotProposal from one pre-declared Section."""
    return await _persist_proposal_group(
        job,
        group_key=section.id,
        section_label=section.title,
        section_ref=section.id,
        page=getattr(section, "page_start", None),
        proposals=proposals,
        existing_neurons=existing_neurons,
        db=db,
    )


def _group_whole_doc_proposals(proposals: list[dict]) -> dict[str, list[dict]]:
    """Bucket LLM-emitted proposals by their `section` value.

    Proposals missing or with empty `section` bucket under the sentinel key
    '__unassigned__' so they still get persisted (under a single combined
    proposal group).
    """
    groups: dict[str, list[dict]] = {}
    for prop in proposals:
        key = str(prop.get("section") or "").strip() or "__unassigned__"
        groups.setdefault(key, []).append(prop)
    return groups


def _group_label_and_page(group_key: str, props: list[dict], doc_title: str) -> tuple[str, int | None]:
    """Synthesize the human-readable label and representative page for a group."""
    if group_key == "__unassigned__":
        label = f"{doc_title} (unassigned proposals)"
    else:
        # Borrow the first proposal's label as a hint after the section number
        first_label = next((p.get("label", "") for p in props if p.get("label")), "")
        label = f"{group_key} {first_label}".strip() if first_label else group_key

    pages = [p.get("page") for p in props if isinstance(p.get("page"), int)]
    page = min(pages) if pages else None
    return label, page


async def create_whole_doc_proposals(
    job: DocumentIngestJob,
    proposals: list[dict],
    existing_neurons: list[dict],
    db: AsyncSession,
    doc_title: str,
) -> list[int]:
    """Whole-doc path: persist a batch of LLM-emitted proposals.

    Groups by the LLM's `section` value and creates one AutopilotProposal
    per group, each with its own ProposalItem children. Returns the list
    of created AutopilotProposal IDs.
    """
    groups = _group_whole_doc_proposals(proposals)
    proposal_ids: list[int] = []
    for group_key, group_props in groups.items():
        label, page = _group_label_and_page(group_key, group_props, doc_title)
        pid = await _persist_proposal_group(
            job,
            group_key=group_key,
            section_label=label,
            section_ref=None,
            page=page,
            proposals=group_props,
            existing_neurons=existing_neurons,
            db=db,
        )
        if pid is not None:
            proposal_ids.append(pid)
    return proposal_ids


@dataclass
class _ExtractionProgress:
    """Mutable accumulator for extraction progress across sections."""
    proposal_ids: list[int] = field(default_factory=list)
    total_cost: float = 0.0
    total_input: int = 0
    total_output: int = 0
    duplicates_flagged: int = 0
    errors: list[str] = field(default_factory=list)


async def _process_section(
    db: AsyncSession,
    job: DocumentIngestJob,
    section: Section,
    section_text: str,
    toc_outline: str,
    doc_title: str,
    existing_neurons: list[dict],
    progress: _ExtractionProgress,
) -> None:
    """Extract knowledge from one section and create a proposal."""
    proposals, usage = await extract_section_knowledge(
        section=section,
        section_text=section_text,
        doc_title=doc_title,
        toc_outline=toc_outline,
        existing_neurons=existing_neurons,
        department=job.department,
        role_key=job.role_key,
        model=job.model,
    )

    progress.total_cost += usage.get("cost_usd", 0.0)
    progress.total_input += usage.get("input_tokens", 0)
    progress.total_output += usage.get("output_tokens", 0)

    if proposals:
        proposals = await check_semantic_duplicates(proposals, existing_neurons)
        progress.duplicates_flagged += sum(1 for p in proposals if p.get("duplicate_of"))

    proposal_id = await create_section_proposal(
        job, section, proposals, existing_neurons, db,
    )
    if proposal_id is not None:
        progress.proposal_ids.append(proposal_id)


def _sync_progress(job: DocumentIngestJob, progress: _ExtractionProgress) -> None:
    """Write accumulated progress onto the job record."""
    job.cost_usd = progress.total_cost
    job.input_tokens = progress.total_input
    job.output_tokens = progress.total_output
    job.duplicates_flagged = progress.duplicates_flagged
    job.proposal_ids_json = json.dumps(progress.proposal_ids)
    job.errors_json = json.dumps(progress.errors)


async def _run_whole_doc_extraction(
    db: AsyncSession,
    job: DocumentIngestJob,
    structure: DocumentStructure,
    full_text: str,
    existing_neurons: list[dict],
) -> _ExtractionProgress:
    """Whole-doc path orchestration: one LLM call, persist batched proposals."""
    progress = _ExtractionProgress()

    job.step = "Extracting (whole-doc, large call in progress)"
    job.total_sections = 0
    job.current_section = 0
    await db.commit()

    try:
        proposals, usage, raw_text = await extract_whole_document(
            job, full_text, structure, existing_neurons,
        )
    except (AssertionError, RuntimeError, TimeoutError) as exc:
        error_msg = f"Whole-doc extraction failed: {exc}"
        logger.error("Job %s: %s", job.id, error_msg)
        progress.errors.append(error_msg)
        return progress

    progress.total_cost += usage.get("cost_usd", 0.0)
    progress.total_input += usage.get("input_tokens", 0)
    progress.total_output += usage.get("output_tokens", 0)

    # When the LLM ran but produced no parseable proposals, record the raw
    # head + tail of its response so a human reviewer can see what it said.
    if not proposals and raw_text:
        preview = raw_text[:1000] + (" … " + raw_text[-500:] if len(raw_text) > 1500 else "")
        progress.errors.append(
            f"LLM produced {len(raw_text)} chars but 0 parseable proposals. "
            f"Preview: {preview}"
        )

    if proposals:
        proposals = await check_semantic_duplicates(proposals, existing_neurons)
        progress.duplicates_flagged = sum(1 for p in proposals if p.get("duplicate_of"))

    job.step = "Persisting proposals..."
    await db.commit()

    pids = await create_whole_doc_proposals(
        job, proposals, existing_neurons, db, structure.title,
    )
    progress.proposal_ids.extend(pids)
    return progress


async def run_document_extraction(job_id: str) -> None:
    """Orchestrator: run Pass 2 (LLM extraction) for a document ingest job.

    Pass 1 (structure) should already be complete when this is called.
    """
    async with async_session() as db:
        job = await db.get(DocumentIngestJob, job_id)
        if not job or job.status == "cancelled":
            return

        assert job.structure_json, "structure_json must be populated before extraction"
        assert job.extracted_text, "extracted_text must be populated before extraction"

        structure_data = json.loads(job.structure_json)
        structure = DocumentStructure(
            title=structure_data["title"],
            total_pages=structure_data.get("total_pages"),
            sections=[Section(**s) for s in structure_data["sections"]],
        )

        full_text = job.extracted_text

        job.status = "extracting"
        job.step = "Loading existing neurons..."
        await db.commit()

        existing_neurons = await _get_existing_neurons(db, job.department)

        # Dispatch: whole-doc (single-pass) vs chunked per-section fallback.
        if len(full_text) <= WHOLE_DOC_THRESHOLD_CHARS:
            logger.info(
                "Job %s: whole-doc path (%d chars <= %d)",
                job_id, len(full_text), WHOLE_DOC_THRESHOLD_CHARS,
            )
            progress = await _run_whole_doc_extraction(
                db, job, structure, full_text, existing_neurons,
            )
            job.status = "done"
            job.step = f"Complete: {len(progress.proposal_ids)} proposals (whole-doc pass)"
            _sync_progress(job, progress)
            await db.commit()
            logger.info(
                "Document ingest job %s complete (whole-doc): %d proposals, $%.4f cost",
                job_id, len(progress.proposal_ids), progress.total_cost,
            )
            return

        # Fallback: chunked per-section path for oversized docs.
        logger.info(
            "Job %s: chunked path (%d chars > %d)",
            job_id, len(full_text), WHOLE_DOC_THRESHOLD_CHARS,
        )
        await _run_chunked_extraction(
            db, job, job_id, structure, full_text, existing_neurons,
        )


async def _process_section_with_retry(
    db: AsyncSession,
    job: DocumentIngestJob,
    job_id: str,
    section: Section,
    section_text: str,
    toc_outline: str,
    doc_title: str,
    existing_neurons: list[dict],
    progress: _ExtractionProgress,
) -> None:
    """Process one section with bounded retry. Records errors on progress."""
    for attempt in range(MAX_RETRIES):
        try:
            await _process_section(
                db, job, section, section_text,
                toc_outline, doc_title, existing_neurons, progress,
            )
            return
        except (AssertionError, RuntimeError, TimeoutError, ValueError) as exc:
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF_BASE * (2 ** attempt)
                logger.warning(
                    "Job %s section %s attempt %d failed: %s. Retrying in %ds...",
                    job_id, section.id, attempt + 1, exc, wait,
                )
                job.step = f"Retry {attempt + 2}/{MAX_RETRIES} for: {section.title[:60]}..."
                await db.commit()
                await asyncio.sleep(wait)
            else:
                error_msg = (
                    f"Section {section.id} ({section.title}): "
                    f"failed after {MAX_RETRIES} attempts. Last error: {exc}"
                )
                logger.error("Extraction failed in job %s: %s", job_id, error_msg)
                progress.errors.append(error_msg)


async def _run_chunked_extraction(
    db: AsyncSession,
    job: DocumentIngestJob,
    job_id: str,
    structure: DocumentStructure,
    full_text: str,
    existing_neurons: list[dict],
) -> None:
    """Chunked-path orchestration: per-section LLM calls in sequence."""
    toc_outline = _build_toc_outline(structure)
    job.total_sections = len(structure.sections)
    job.current_section = 0
    await db.commit()
    progress = _ExtractionProgress()

    for i, section in enumerate(structure.sections):
        await db.refresh(job)
        if job.status == "cancelled":
            logger.info("Job %s cancelled at section %d", job_id, i)
            return

        job.current_section = i + 1
        job.step = f"Extracting section {i + 1}/{len(structure.sections)}: {section.title[:80]}"
        await db.commit()

        section_text = full_text[section.char_start:section.char_end].strip()
        if not section_text or len(section_text) < 50:
            continue

        await _process_section_with_retry(
            db, job, job_id, section, section_text,
            toc_outline, structure.title, existing_neurons, progress,
        )
        _sync_progress(job, progress)
        await db.commit()

    job.status = "done"
    job.step = f"Complete: {len(progress.proposal_ids)} proposals from {len(structure.sections)} sections"
    _sync_progress(job, progress)
    await db.commit()

    logger.info(
        "Document ingest job %s complete: %d proposals, $%.4f cost, %d duplicates",
        job_id, len(progress.proposal_ids), progress.total_cost, progress.duplicates_flagged,
    )
