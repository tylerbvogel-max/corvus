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

    # --- continuity boost stage ---
    continuity_boosted_count: int = 0

    # --- spread + inhibit stages ---
    all_scored: list[NeuronScoreBreakdown] = field(default_factory=list)

    # --- regulatory resolve stage ---
    resolved_regulations: list[Any] = field(default_factory=list)

    # --- assemble stage ---
    top_slice: list[NeuronScoreBreakdown] = field(default_factory=list)
    neuron_map: dict[int, Neuron] = field(default_factory=dict)
    system_prompt: str = ""
