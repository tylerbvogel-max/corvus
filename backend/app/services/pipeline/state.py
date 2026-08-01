"""Mutable state object threaded through the query-prep pipeline stages.

Pattern #5 philosophy — each stage declares (via docstring + field access)
which fields it reads and which it writes. A single growing state object is
pragmatic for a pipeline that hits the DB at every step; pure functional
dataflow would force every stage to return a new typed record with half the
fields unchanged, which trades clarity for boilerplate.

The existing `PreparedContext` remains the *public* result of the pipeline;
`PipelineState` is the intermediate scratch that stages populate.
"""

from dataclasses import dataclass, field
from typing import Any

from app.models import Neuron
from app.services.scoring_engine import NeuronScoreBreakdown


@dataclass
class PipelineState:
    """Intermediate per-query state; written by stages, read by `prepare_context`."""
    # --- inputs (set by caller before first stage) ---
    user_message: str
    effective_top_k: int
    effective_pool: int
    effective_budget: int
    project_path: str | None = None
    prior_neuron_ids: list[int] | None = None
    # Requester ACL scope (RequesterContext) — None = unrestricted local default
    requester: Any = None
    # Per-query spread-activation overrides — None = tenant settings
    spread_hops: int | None = None
    spread_floor: float | None = None

    # --- classify stage ---
    classify_result: dict[str, Any] = field(default_factory=dict)
    query_embedding: Any = None
    intent: str = "general_query"
    departments: list[str] = field(default_factory=list)
    role_keys: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    # --- prefilter + score stages ---
    total_queries: int = 0
    scored: list[NeuronScoreBreakdown] = field(default_factory=list)
    scored_engrams: list[NeuronScoreBreakdown] = field(default_factory=list)
    candidates_considered: int = 0
    # Lane name (embedding/keyword/entity/filter) → candidate neuron ids.
    # Observe-only provenance for the retrieval_telemetry stage.
    lane_hits: dict[str, list[int]] = field(default_factory=dict)
    # Raw pre-RRF cosine per embedding-lane neuron. RRF rank normalization
    # pins the fused top-1 score to a constant, so these magnitudes are the
    # only usable retrieval-confidence signal downstream.
    embedding_sims: dict[int, float] = field(default_factory=dict)

    # --- continuity boost stage ---
    continuity_boosted_count: int = 0

    # --- spread + inhibit stages ---
    all_scored: list[NeuronScoreBreakdown] = field(default_factory=list)
    neurons_activated: int = 0
    redundancy_suppressed: int = 0
    # Database-backed spread safety-limit observations for calibration.
    spread_traversal: dict[str, Any] = field(default_factory=dict)

    # --- regulatory resolve stage ---
    resolved_regulations: list[Any] = field(default_factory=list)

    # --- assemble stage ---
    top_slice: list[NeuronScoreBreakdown] = field(default_factory=list)
    neuron_map: dict[int, Neuron] = field(default_factory=dict)
    system_prompt: str = ""
    # Frequency-hopped citation grounding: secret per-query key<->neuron map
    # (citation_hopping.HopMap) or None when disabled. Typed Any to keep this
    # scratch module import-light, mirroring `requester`.
    hop_map: Any = None
    neurons_delivered: int = 0
    estimated_memory_tokens: int = 0
    memory_context_chars: int = 0
    memory_context_utf8_bytes: int = 0
    memory_context_text: str = ""
    memory_token_budget: int = 0
    assembly_stop_reason: str = "no_candidates"
    oversized_first_neuron: bool = False
    token_estimator_version: str = ""
    memory_representations: dict[int, str] = field(default_factory=dict)

    # --- retrieval telemetry stage (observe-only) ---
    retrieval_telemetry: dict[str, Any] = field(default_factory=dict)
