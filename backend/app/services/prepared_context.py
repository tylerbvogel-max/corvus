"""The shape of a prepared recall result.

Extracted from ``executor`` by roadmap record durability-modular-monolith
(04, seam 4). ``structural_resolver`` CONSTRUCTS these — it is not a type-only
import — so leaving the class inside executor forced the resolver to import the
executor back, and executor reaches the resolver through the pipeline stages.
That was one of the two loops braided into the eight-module cycle.

This module holds vocabulary only: no queries, no orchestration, no imports from
executor or from the pipeline package.
"""

from dataclasses import dataclass, field

from app.models import Neuron
from app.services.citation_hopping import HopMap
from app.services.scoring_engine import NeuronScoreBreakdown


@dataclass
class PreparedContext:
    """Result of the classify → score → spread → inhibit → assemble pipeline."""
    system_prompt: str
    intent: str
    departments: list[str]
    role_keys: list[str]
    keywords: list[str]
    neuron_scores: list[dict] = field(default_factory=list)
    neurons_activated: int = 0
    candidates_considered: int = 0
    neurons_delivered: int = 0
    estimated_memory_tokens: int = 0
    memory_context_chars: int = 0
    memory_context_utf8_bytes: int = 0
    memory_context_text: str = ""
    memory_token_budget: int = 0
    assembly_stop_reason: str = "no_candidates"
    redundancy_suppressed: int = 0
    token_estimator_version: str = ""
    oversized_first_neuron: bool = False
    memory_representations: dict[int, str] = field(default_factory=dict)
    recall_latency_ms: float = 0.0
    neuron_map: dict[int, Neuron] = field(default_factory=dict)
    all_scored: list[NeuronScoreBreakdown] = field(default_factory=list)
    classify_cost_usd: float = 0.0
    classify_input_tokens: int = 0
    classify_output_tokens: int = 0
    # Pattern #5: typed pipeline DAG telemetry — per-stage timing + status.
    # Populated when prepare_context runs through the pipeline runner;
    # None for structural fast-path results (no pipeline ran).
    stage_telemetry: list[dict] = field(default_factory=list)
    # Frequency-hopped citation grounding: the secret per-query key<->neuron
    # map used to render tokens and verify citations. None when disabled.
    # Never serialise to the client — it is secret to the analysis layer.
    hop_map: HopMap | None = None
    # Resolved eCFR regulations (ResolvedRegulation) surfaced this query — used
    # to label engram hop citations for the frontend. Empty when none resolved.
    resolved_regulations: list = field(default_factory=list)
    # Full post-spread/post-redundancy activation list for telemetry. Public
    # all_scored remains the delivered slice for attribution compatibility.
    activated_scores: list[NeuronScoreBreakdown] = field(default_factory=list)
