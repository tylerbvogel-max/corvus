"""Deterministic, token-bounded neuron packet assembly.

Claude does not expose a trusted public offline tokenizer.  This module keeps
the pre-call estimate deliberately simple, versioned, and observable; provider
usage after execution remains a separate measurement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from app.config import settings
from app.models import Neuron
from app.services.prompt_assembler import AUTHORITY_TAG_MAP
from app.services.scoring_engine import NeuronScoreBreakdown


UTF8_BYTES_DIV4_CEIL_V1 = "utf8_bytes_div4_ceil_v1"
SUPPORTED_ESTIMATORS = frozenset({UTF8_BYTES_DIV4_CEIL_V1})


def estimate_memory_tokens(text: str, estimator: str | None = None) -> int:
    """Estimate tokens deterministically from UTF-8 bytes.

    ``utf8_bytes_div4_ceil_v1`` is a provisional assembly estimator, not a
    claim about Claude's tokenizer.  Ceil avoids reporting non-empty fragments
    as zero and UTF-8 bytes make non-ASCII behavior explicit.
    """
    assert isinstance(text, str), "text must be a string"
    name = estimator or settings.memory_token_estimator
    if name not in SUPPORTED_ESTIMATORS:
        raise ValueError(f"Unsupported memory token estimator: {name}")
    if not text:
        return 0
    return math.ceil(len(text.encode("utf-8")) / 4)


def _citation_tag(label: str | None) -> str:
    return f"[{label}] " if label else ""


def _metadata_tags(neuron: Neuron) -> str:
    tags: list[str] = []
    authority = AUTHORITY_TAG_MAP.get(neuron.authority_level or "")
    if authority:
        tags.append(authority)
    if neuron.source_origin == "document":
        tags.append("REFERENCE")
    if neuron.department:
        tags.append(f"scope:{neuron.department}")
    if neuron.role_key:
        tags.append(f"role:{neuron.role_key}")
    if neuron.source_origin:
        tags.append(f"source:{neuron.source_origin}")
    return "".join(f" [{tag}]" for tag in tags)


def render_memory_entry(
    score: NeuronScoreBreakdown,
    neuron: Neuron,
    citation_label: str | None,
    representation: str = "full",
) -> str:
    """Render one indivisible memory block with source-visible metadata."""
    assert representation in {"full", "summary"}, "invalid representation"
    body = neuron.content if representation == "full" else neuron.summary
    body = (body or "").strip()
    header = (
        f"{_citation_tag(citation_label)}**{neuron.label}**"
        f"{_metadata_tags(neuron)} (L{neuron.layer}; score:{score.combined:.2f})"
    )
    return f"{header}\n{body}" if body else header


@dataclass
class MemoryAssemblyResult:
    scores: list[NeuronScoreBreakdown] = field(default_factory=list)
    representations: dict[int, str] = field(default_factory=dict)
    entries: list[str] = field(default_factory=list)
    estimated_tokens: int = 0
    chars: int = 0
    utf8_bytes: int = 0
    budget: int = 0
    stop_reason: str = "no_candidates"
    oversized_first_neuron: bool = False
    estimator_version: str = UTF8_BYTES_DIV4_CEIL_V1

    @property
    def memory_text(self) -> str:
        return "\n\n".join(self.entries)


def assemble_memory_packet(
    ranked_scores: list[NeuronScoreBreakdown],
    neuron_map: dict[int, Neuron],
    token_budget: int,
    safety_neuron_cap: int,
    *,
    citation_labels: dict[int, str] | None = None,
    estimator: str | None = None,
) -> MemoryAssemblyResult:
    """Greedily pack whole ranked neurons under a memory-only token ceiling.

    Full content is preferred.  A stored summary is the only compact fallback;
    no generated truncation or neuron splitting occurs.  A non-fitting record
    is skipped so a pathological verbose neuron cannot starve all lower-ranked
    knowledge.  The first record is nevertheless admitted if neither its full
    nor summary representation fits, and that overflow is explicit telemetry.
    """
    assert token_budget > 0, "token_budget must be positive"
    assert safety_neuron_cap > 0, "safety_neuron_cap must be positive"
    name = estimator or settings.memory_token_estimator
    if name not in SUPPORTED_ESTIMATORS:
        raise ValueError(f"Unsupported memory token estimator: {name}")

    available = [s for s in ranked_scores if s.neuron_id in neuron_map]
    result = MemoryAssemblyResult(
        budget=token_budget,
        estimator_version=name,
        stop_reason="no_candidates" if not available else "candidate_exhausted",
    )
    if not available:
        return result

    hit_ceiling = False
    for score in available:
        if len(result.scores) >= safety_neuron_cap:
            result.stop_reason = "safety_neuron_cap"
            break

        neuron = neuron_map[score.neuron_id]
        label = (citation_labels or {}).get(score.neuron_id)
        full = render_memory_entry(score, neuron, label, "full")
        choices = [("full", full)]
        if neuron.summary and neuron.summary.strip() != (neuron.content or "").strip():
            choices.append(("summary", render_memory_entry(score, neuron, label, "summary")))

        chosen: tuple[str, str] | None = None
        for representation, entry in choices:
            trial_entries = [*result.entries, entry]
            trial_text = "\n\n".join(trial_entries)
            if estimate_memory_tokens(trial_text, name) <= token_budget:
                chosen = (representation, entry)
                break

        if chosen is None and not result.scores:
            # Admission floor: one indivisible memory is more useful than an
            # empty packet. Prefer full fidelity when overflow is unavoidable.
            chosen = choices[0]
            result.oversized_first_neuron = True
        elif chosen is None:
            hit_ceiling = True
            continue

        representation, entry = chosen
        result.scores.append(score)
        result.representations[score.neuron_id] = representation
        result.entries.append(entry)

    else:
        if hit_ceiling:
            result.stop_reason = "token_ceiling"

    text = result.memory_text
    result.chars = len(text)
    result.utf8_bytes = len(text.encode("utf-8"))
    result.estimated_tokens = estimate_memory_tokens(text, name)
    if result.stop_reason == "candidate_exhausted" and hit_ceiling:
        result.stop_reason = "token_ceiling"
    return result

