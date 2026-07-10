"""Token-budgeted prompt assembly from top-K scored neurons.

Groups neurons by department > role for structural coherence.
Score-ordered packing with fallback to summary-only if content doesn't fit.
"""

from types import MappingProxyType

from app.config import settings
from app.models import Neuron
from app.services.scoring_engine import NeuronScoreBreakdown
from app.tenant import tenant

# Rough token estimation: ~4 chars per token
CHARS_PER_TOKEN = 4

AUTHORITY_TAG_MAP = MappingProxyType({
    "binding_standard": "BINDING",
    "regulatory": "REGULATORY",
    "industry_practice": "INDUSTRY",
    "organizational": "ORG",
    "informational": "INFO",
})

# Intent-specific closing instructions to guide Haiku output structure
CLOSING_INSTRUCTION_MAP = MappingProxyType({
    "compliance": "Answer with: (1) Direct answer, (2) Cited regulation clause numbers (FAR/DFARS/CAS/ITAR), (3) [RISK] flags if any compliance exposure, (4) Recommended action. Never fabricate regulation numbers — if uncertain, say so explicitly.",
    "engineering": "Answer with: (1) Technical recommendation, (2) Referenced standard with section number (MIL-STD, DO-178C, AS9100, ASME Y14.5, SAE), (3) Implementation steps (numbered, specific), (4) Verification method or acceptance criteria.",
    "data_engineer": "Answer with: (1) Recommended approach with specific tools, (2) Code example if applicable, (3) When to use this vs alternatives (tradeoffs), (4) Common pitfalls or failure modes.",
    "elt": "Answer with: (1) Recommended approach with specific tools, (2) Code example if applicable, (3) When to use this vs alternatives (tradeoffs), (4) Common pitfalls or failure modes.",
    "databricks": "Answer with: (1) Recommended approach with specific tools, (2) Code example if applicable, (3) When to use this vs alternatives (tradeoffs), (4) Common pitfalls or failure modes.",
    "pipeline": "Answer with: (1) Recommended approach with specific tools, (2) Code example if applicable, (3) When to use this vs alternatives (tradeoffs), (4) Common pitfalls or failure modes.",
    "finance": "Answer with: (1) Direct answer with specific dollar amounts or percentages if known, (2) Applicable FAR/CAS clause, (3) [AUDIT] flags if this is a high-risk treatment, (4) Recommended accounting approach.",
    "procurement": "Answer with: (1) Direct recommendation, (2) Applicable FAR section, (3) Required documentation or certification if any, (4) Risk or compliance considerations.",
    "proposal": "Answer with: (1) Win strategy recommendation or Section L/M compliance approach, (2) Competitive discriminators to emphasize, (3) Known buyer hot buttons or RFP intent, (4) Implementation approach.",
    "program_management": "Answer with: (1) Best practice or recommended approach, (2) Reference (ANSI/EIA 748-B, GAO, DFARS clause), (3) Implementation steps or checkpoints, (4) Common pitfalls.",
    "hr": "Answer with: (1) Direct answer, (2) Regulatory requirement with reference (NISPOM, DOD, etc), (3) Process steps or timeline, (4) Compliance or risk flags.",
    "safety": "Answer with: (1) Hazard classification per MIL-STD-882E (Critical/High/Medium/Low), (2) Hazard analysis steps (FMEA, FTA), (3) Recommended mitigation measures, (4) Residual risk assessment.",
    "it_security": "Answer with: (1) Recommended control or mitigation, (2) Applicable NIST 800-171 control family (e.g., SC-7, AC-3), (3) Implementation guidance (tools, config, procedure), (4) Assessment method for audit readiness.",
    "executive": "Answer with: (1) Strategic recommendation with business rationale, (2) Key financial or risk drivers, (3) Implementation approach and timeline, (4) Success metrics or KPIs.",
    "regulatory": "Answer with: (1) Regulatory requirement(s) with specific clause numbers, (2) Applicability statement (when, to whom, under what conditions), (3) Compliance action or proof needed, (4) Audit/enforcement risk if non-compliant.",
    "general_query": "Use the above knowledge to directly answer the user's question. Provide specific, actionable guidance with concrete examples and code where applicable.",
})

# Unconditional grounding clause appended to EVERY closing instruction.
# The structured sections above demand specific references ("(2) Referenced
# standard with section number...") — without this escape hatch, a model whose
# context pack lacks the reference is forced to fabricate one from memory to
# satisfy the structure, the diagnosed driver of low faithfulness scores on
# structured intents (2026-07-10). Previously this protection existed only for
# general_query and low-confidence (max_relevance < 0.5) queries.
GROUNDING_CLAUSE = (
    " Where the structure above calls for a standard, regulation, clause, or "
    "specific figure, cite it only if it appears in the knowledge provided — "
    "if it does not, say so explicitly instead of supplying one from memory. "
    "If the knowledge above does not cover part of the question, state that "
    "plainly rather than inferring an answer. Keep each such gap "
    "acknowledgment to a sentence or two — do not build sections cataloguing "
    "what is not covered."
)

INTENT_VOICE_MAP = tenant.intent_voice_map


def _estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def _pack_prior_context(
    parts: list[str],
    prior_neuron_ids: list[int],
    prior_neuron_map: dict[int, Neuron],
    used_tokens: int,
    budget: int,
) -> int:
    """Inject a brief section summarizing neurons from prior conversation turns.

    This gives the LLM awareness of what knowledge was already surfaced,
    creating conversational continuity across the neuron graph.
    """
    header = "## Conversation Context\nThe user has been exploring these topics in this conversation:"
    header_tokens = _estimate_tokens(header)
    if used_tokens + header_tokens > budget:
        return used_tokens
    parts.append(header)
    used_tokens += header_tokens

    # Deduplicate and limit to most recent 15 prior neurons
    seen: set[int] = set()
    for nid in prior_neuron_ids:
        if nid in seen:
            continue
        seen.add(nid)
        neuron = prior_neuron_map.get(nid)
        if not neuron:
            continue
        label = neuron.label
        dept = neuron.department or "General"
        summary = neuron.summary or ""
        entry = f"- [{dept}] {label}: {summary}" if summary else f"- [{dept}] {label}"
        entry_tokens = _estimate_tokens(entry)
        if used_tokens + entry_tokens > budget:
            break
        parts.append(entry)
        used_tokens += entry_tokens
        if len(seen) >= 15:
            break

    parts.append("")
    return used_tokens


def _get_voice(intent: str) -> str:
    """Match intent to behavioral voice framing."""
    intent_lower = intent.lower()
    for key, voice in INTENT_VOICE_MAP.items():
        if key in intent_lower:
            return voice
    return INTENT_VOICE_MAP["general_query"]


def _build_prompt_header(
    intent: str,
    scored_neurons: list[NeuronScoreBreakdown],
    neuron_map: dict[int, Neuron],
) -> tuple[list[str], int]:
    header = _get_voice(intent)
    parts = [header, "", "## Reference Knowledge", ""]

    has_authority = any(
        hasattr(neuron_map.get(s.neuron_id), "authority_level")
        and getattr(neuron_map.get(s.neuron_id), "authority_level", None)
        for s in scored_neurons
    )
    if has_authority:
        legend = "Authority: [BINDING]=binding standard, [REGULATORY]=regulatory requirement, [INDUSTRY]=industry practice"
        parts.append(legend)
        parts.append("")

    used_tokens = _estimate_tokens("\n".join(parts))
    return parts, used_tokens


def _build_signal_briefing(
    intent: str,
    scored_neurons: list[NeuronScoreBreakdown],
    neuron_map: dict[int, Neuron],
    used_tokens: int,
    budget: int,
) -> tuple[int, str]:
    """Build Query Analysis briefing showing intent, confidence, and signal context."""
    # Compute confidence level and max relevance
    max_relevance = max((s.combined for s in scored_neurons), default=0.0)
    if max_relevance >= 0.8:
        confidence = "HIGH"
    elif max_relevance >= 0.5:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    # Count regulatory vs functional neurons
    functional, regulatory = _partition_neurons(scored_neurons, neuron_map)
    neuron_count = f"{len(regulatory)} regulatory + {len(functional)} functional"

    # Build briefing
    briefing = f"## Query Analysis\nIntent: {intent} | Confidence: {confidence} (max relevance ≥ {max_relevance:.2f})\nActive neurons: {neuron_count}"
    briefing_tokens = _estimate_tokens(briefing)

    if used_tokens + briefing_tokens <= budget:
        return used_tokens + briefing_tokens, briefing + "\n"
    return used_tokens, ""


def _partition_neurons(
    scored_neurons: list[NeuronScoreBreakdown],
    neuron_map: dict[int, Neuron],
) -> tuple[list[tuple[NeuronScoreBreakdown, Neuron]], list[tuple[NeuronScoreBreakdown, Neuron]]]:
    functional: list[tuple[NeuronScoreBreakdown, Neuron]] = []
    regulatory: list[tuple[NeuronScoreBreakdown, Neuron]] = []
    for score in scored_neurons:
        neuron = neuron_map.get(score.neuron_id)
        if not neuron:
            continue
        if neuron.department == tenant.regulatory_department_name:
            regulatory.append((score, neuron))
        else:
            functional.append((score, neuron))
    return functional, regulatory


def _pack_functional_section(
    parts: list[str],
    functional: list[tuple[NeuronScoreBreakdown, Neuron]],
    used_tokens: int,
    budget: int,
    citation_label_by_id: dict[int, str] | None = None,
) -> int:
    grouped: dict[str, dict[str, list[tuple[NeuronScoreBreakdown, Neuron]]]] = {}
    for score, neuron in functional:
        dept = neuron.department or "General"
        role = neuron.role_key or "general"
        grouped.setdefault(dept, {}).setdefault(role, []).append((score, neuron))

    for dept, roles in grouped.items():
        dept_header = f"### {dept}"
        dept_tokens = _estimate_tokens(dept_header)
        if used_tokens + dept_tokens > budget:
            break
        parts.append(dept_header)
        used_tokens += dept_tokens

        for role_key, items in roles.items():
            items.sort(key=lambda x: x[0].combined, reverse=True)
            for score, neuron in items:
                used_tokens = _pack_neuron(parts, score, neuron, used_tokens, budget, citation_label_by_id)
    return used_tokens


def _pack_regulatory_section(
    parts: list[str],
    regulatory: list[tuple[NeuronScoreBreakdown, Neuron]],
    used_tokens: int,
    budget: int,
    citation_label_by_id: dict[int, str] | None = None,
) -> int:
    if not regulatory:
        return used_tokens

    reg_header = "\n## Regulatory Context"
    reg_tokens = _estimate_tokens(reg_header)
    if used_tokens + reg_tokens > budget:
        return used_tokens
    parts.append(reg_header)
    used_tokens += reg_tokens

    reg_grouped: dict[str, list[tuple[NeuronScoreBreakdown, Neuron]]] = {}
    for score, neuron in regulatory:
        rk = neuron.role_key or "general"
        reg_grouped.setdefault(rk, []).append((score, neuron))

    for role_key, items in reg_grouped.items():
        items.sort(key=lambda x: x[0].combined, reverse=True)
        for score, neuron in items:
            used_tokens = _pack_neuron(parts, score, neuron, used_tokens, budget, citation_label_by_id)
    return used_tokens


def _pack_resolved_regulations(
    parts: list[str],
    resolved: list,
    used_tokens: int,
    budget: int,
    engram_citation_tokens: dict[int, str] | None = None,
) -> int:
    """Pack resolved engram regulatory text into the prompt. When hop tokens are
    supplied, each regulation carries a per-query citation key ALONGSIDE its real
    CFR ref, so the exit layer can verify the model cited a regulation that was
    actually resolved (while the human-readable federal ref stays visible)."""
    if not resolved:
        return used_tokens
    header = "\n## Authoritative Regulatory Text (live from eCFR)"
    header_tokens = _estimate_tokens(header)
    if used_tokens + header_tokens > budget:
        return used_tokens
    parts.append(header)
    used_tokens += header_tokens

    for reg in sorted(resolved, key=lambda r: r.token_count):
        tag = ""
        if engram_citation_tokens:
            tok = engram_citation_tokens.get(reg.engram_id)
            if tok:
                tag = f"[{tok}] "
        entry = f"{tag}**{reg.cfr_ref}** [REGULATORY]\n{reg.text}"
        entry_tokens = _estimate_tokens(entry)
        if used_tokens + entry_tokens > budget:
            break
        parts.append(entry)
        used_tokens += entry_tokens
    return used_tokens


def _append_citation_instruction(
    parts: list[str],
    citation_label_by_id: dict[int, str] | None,
    is_hopping: bool = False,
) -> None:
    """Tell the LLM to cite sources inline. No-op if no citation map was
    assigned. When ``is_hopping`` the citation keys are per-query ephemeral
    tokens (frequency hopping) rather than plain numbers."""
    if not citation_label_by_id:
        return
    if is_hopping:
        parts.append(
            "\n**Citing sources:** When you state a fact from the knowledge above "
            "(including the regulatory sources), cite it inline using its exact "
            "bracketed citation key as shown in that source's header, e.g. "
            "[FQ-1A2B3C]. These keys are unique to this answer. Only cite keys that "
            "actually appear in the source headers above — never invent, alter, or "
            "reuse a key from elsewhere. Do NOT name any regulation, standard, or "
            "specification as authority (do not pull clause or standard numbers from "
            "memory) unless it carries a citation key here. If a claim cannot be "
            "traced to a listed key, say so explicitly rather than fabricating one or "
            "invoking an ungrounded source."
        )
        return
    parts.append(
        "\n**Citing sources:** When you state a fact from the knowledge above, "
        "cite the source inline using its bracketed number, e.g. [1] or [2]. "
        "Only cite numbers that actually appear in the source headers. "
        "If a claim cannot be traced to a numbered source, say so explicitly "
        "rather than fabricating a citation."
    )


def _get_closing_instruction(intent: str, max_relevance: float) -> str:
    """Intent-specific closing instruction + the unconditional GROUNDING_CLAUSE,
    plus an explicit confidence statement when retrieval confidence is low."""
    assert isinstance(intent, str), "intent must be a string"
    intent_lower = intent.lower()

    # Match intent prefix, falling back to general_query
    instruction = CLOSING_INSTRUCTION_MAP["general_query"]
    for key, candidate in CLOSING_INSTRUCTION_MAP.items():
        if key in intent_lower:
            instruction = candidate
            break

    instruction += GROUNDING_CLAUSE
    if max_relevance < 0.5:
        instruction += " State your confidence level."
    return instruction


# Workspace priming: stating the key concepts up front pre-loads the model's
# active workspace before it reads the packed context (transformer-circuits
# 2026 "global workspace" finding: instructions alone modulate which ~10-25
# concepts a model holds active). Capped near that workspace size.
PRIMING_MAX_TOPICS = 10


def build_priming_line(intent: str, topic_labels: list[str]) -> str:
    """One-line focus preamble naming the packed context's key topics.

    MUST be built only from labels of sources actually packed into the prompt
    (never raw classify keywords): a topic named here but absent from the pack
    would register as "grounded" and rot the ungrounded-refs detector.
    Returns "" when there is nothing to prime with.
    """
    assert isinstance(topic_labels, list), "topic_labels must be a list"
    assert isinstance(intent, str), "intent must be a string"

    seen: dict[str, None] = {}
    for label in topic_labels:
        clean = (label or "").strip()
        if clean:
            seen.setdefault(clean, None)
        if len(seen) >= PRIMING_MAX_TOPICS:
            break
    if not seen:
        return ""
    intent_part = f" ({intent.replace('_', ' ')})" if intent else ""
    return (
        f"Focus{intent_part}: this question concerns the following topics from "
        f"the knowledge context below — {'; '.join(seen)}.\n\n"
    )


def _build_citation_labels(
    scored_neurons: list[NeuronScoreBreakdown],
    citation_tokens: dict[int, str] | None,
) -> tuple[dict[int, str], bool]:
    """Citation labels: per-query frequency-hop keys when supplied by the caller
    (anti-hallucination — see citation_hopping.py), otherwise 1-based indices.
    Returns (label_by_neuron_id, is_hopping)."""
    if citation_tokens is not None:
        return dict(citation_tokens), True
    return {n.neuron_id: str(i + 1) for i, n in enumerate(scored_neurons)}, False


def assemble_prompt(
    intent: str,
    scored_neurons: list[NeuronScoreBreakdown],
    neuron_map: dict[int, Neuron],
    budget_tokens: int | None = None,
    prior_neuron_ids: list[int] | None = None,
    prior_neuron_map: dict[int, Neuron] | None = None,
    resolved_regulations: list | None = None,
    citation_tokens: dict[int, str] | None = None,
    engram_citation_tokens: dict[int, str] | None = None,
) -> str:
    """Pack top-K neurons + resolved regulatory text into a system prompt within token budget.

    Groups by department > role for structural coherence.
    Falls back to summary-only if full content exceeds budget.

    Tier C inline citations (Pattern: hero-UX trust transform, 2026-04-23):
    Each neuron in ``scored_neurons`` is assigned a 1-based citation index
    by its position in the list. The index is injected into the prompt
    headers (`[1] **label**`) and the LLM is instructed to cite sources
    using those brackets. The frontend reads the same neuron_scores order
    and turns [N] markers into clickable superscripts pointing at source N.
    """
    budget = budget_tokens or settings.token_budget

    # Neuron blocks carry [label] tags the LLM must cite by.
    citation_label_by_id, is_hopping = _build_citation_labels(scored_neurons, citation_tokens)
    is_hopping = is_hopping or engram_citation_tokens is not None

    parts, used_tokens = _build_prompt_header(intent, scored_neurons, neuron_map)

    # Inject signal-aware briefing showing why neurons were selected
    used_tokens, briefing = _build_signal_briefing(intent, scored_neurons, neuron_map, used_tokens, budget)
    if briefing:
        parts.append(briefing)

    # Inject conversation continuity context from prior turns
    if prior_neuron_ids and prior_neuron_map:
        used_tokens = _pack_prior_context(
            parts, prior_neuron_ids, prior_neuron_map, used_tokens, budget,
        )

    functional, regulatory = _partition_neurons(scored_neurons, neuron_map)
    used_tokens = _pack_functional_section(parts, functional, used_tokens, budget, citation_label_by_id)
    used_tokens = _pack_regulatory_section(parts, regulatory, used_tokens, budget, citation_label_by_id)

    # Pack live regulatory text from resolved engrams
    if resolved_regulations:
        used_tokens = _pack_resolved_regulations(parts, resolved_regulations, used_tokens, budget, engram_citation_tokens)

    parts.append("")

    # Compute max relevance for confidence calibration
    max_relevance = max((s.combined for s in scored_neurons), default=0.0)
    closing = _get_closing_instruction(intent, max_relevance)
    parts.append(closing)
    _append_citation_instruction(parts, citation_label_by_id, is_hopping)

    return "\n".join(parts)


def _pack_neuron(
    parts: list[str],
    score: NeuronScoreBreakdown,
    neuron: Neuron,
    used_tokens: int,
    budget: int,
    citation_label_by_id: dict[int, str] | None = None,
) -> int:
    """Try to pack a neuron into parts. Returns updated used_tokens."""
    # Prepend the citation tag so the LLM can reference this source as [label].
    # The label is either a 1-based index (numeric mode) or a per-query ephemeral
    # frequency-hop key (e.g. [FQ-7F3A2C]). Empty if the neuron isn't in the map.
    citation_tag = ""
    if citation_label_by_id is not None:
        label = citation_label_by_id.get(neuron.id)
        if label is not None:
            citation_tag = f"[{label}] "

    authority_tag = ""
    if hasattr(neuron, "authority_level") and neuron.authority_level:
        tag = AUTHORITY_TAG_MAP.get(neuron.authority_level)
        if tag:
            authority_tag = f" [{tag}]"

    # Build signal rationale for high-scoring neurons (score >= 0.8)
    signal_note = ""
    if score.combined >= 0.8 and hasattr(score, "relevance") and hasattr(score, "impact"):
        signals = []
        if score.relevance > 0.3:
            signals.append("relevance")
        if score.impact > 0.3:
            signals.append("impact")
        if hasattr(score, "recency") and score.recency > 0.3:
            signals.append("recency")
        if hasattr(score, "precision") and score.precision > 0.3:
            signals.append("precision")
        if len(signals) >= 2:
            signal_note = f" ← {', '.join(signals[:2])} driver"
        elif len(signals) == 1:
            signal_note = f" ← {signals[0]} driver"

    if neuron.content:
        full_entry = f"{citation_tag}**{neuron.label}**{authority_tag} (L{neuron.layer}){signal_note}\n{neuron.content}"
        full_tokens = _estimate_tokens(full_entry)
        if used_tokens + full_tokens <= budget:
            parts.append(full_entry)
            return used_tokens + full_tokens

    if neuron.summary:
        summary_entry = f"- {citation_tag}{neuron.summary}{authority_tag}{signal_note} (score: {score.combined:.2f})"
        summary_tokens = _estimate_tokens(summary_entry)
        if used_tokens + summary_tokens <= budget:
            parts.append(summary_entry)
            return used_tokens + summary_tokens

    return used_tokens
