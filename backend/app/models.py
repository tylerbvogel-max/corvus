import datetime
from types import MappingProxyType

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, synonym


class Base(DeclarativeBase):
    pass


# ── Abstraction axis (substrate/ontology split) ─────────────────────
# The universal "what kind of knowledge" gradient, classifiable from
# content alone. Replaces numeric `layer` as the engine's semantic axis;
# `layer` survives as depth-in-projection metadata for navigation/viz.
ABSTRACTION_STRUCTURAL = "structural"   # navigational container, not knowledge
ABSTRACTION_CONCEPT = "concept"         # cross-cutting definitional anchor
ABSTRACTION_PRINCIPLE = "principle"     # the why — policy, standards, decision rationale
ABSTRACTION_PROCESS = "process"         # the flow
ABSTRACTION_PROCEDURE = "procedure"     # the how
ABSTRACTION_ARTIFACT = "artifact"       # the evidence — records, outputs

# Abstraction values that count as knowledge content (vs navigation scaffolding)
KNOWLEDGE_ABSTRACTIONS = (
    ABSTRACTION_PRINCIPLE, ABSTRACTION_PROCESS,
    ABSTRACTION_PROCEDURE, ABSTRACTION_ARTIFACT,
)

# Backfill/classification default: node_type -> abstraction_type.
# Covers every node_type observed in live tenant graphs; unknown types
# stay NULL and engine predicates fall back to layer heuristics.
ABSTRACTION_BY_NODE_TYPE = MappingProxyType({
    "department": ABSTRACTION_STRUCTURAL,
    "role": ABSTRACTION_STRUCTURAL,
    "concept": ABSTRACTION_CONCEPT,
    "task": ABSTRACTION_PROCESS,
    "process": ABSTRACTION_PROCESS,
    "system": ABSTRACTION_PROCEDURE,
    "procedure": ABSTRACTION_PROCEDURE,
    "technique": ABSTRACTION_PROCEDURE,
    "control": ABSTRACTION_PROCEDURE,
    "decision": ABSTRACTION_PRINCIPLE,
    "knowledge": ABSTRACTION_PRINCIPLE,
    "standard": ABSTRACTION_PRINCIPLE,
    "reference": ABSTRACTION_PRINCIPLE,
    "output": ABSTRACTION_ARTIFACT,
    "artifact": ABSTRACTION_ARTIFACT,
    "detail": ABSTRACTION_ARTIFACT,
    "metric": ABSTRACTION_ARTIFACT,
})


class Neuron(Base):
    __tablename__ = "neurons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("neurons.id"), nullable=True, index=True)
    # Depth within the navigational projection (org chart, capability map, ...).
    # Projection metadata ONLY — engine semantics live on abstraction_type.
    layer: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    node_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Universal knowledge-kind gradient (see ABSTRACTION_* constants).
    # NULL = unclassified; engine predicates fall back to layer heuristics.
    abstraction_type: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # `department` is the physical column for the region tag (silo label).
    # New engine code speaks `region` (synonym below); the legacy name is kept
    # to avoid a destructive rename across SQL/frontend/agents.
    department: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    role_key: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    invocations: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    avg_utility: Mapped[float] = mapped_column(Float, default=0.5, server_default="0.5")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    cross_ref_departments: Mapped[str | None] = mapped_column(Text, nullable=True)
    standard_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at_query_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Authoritative source fields
    source_type: Mapped[str] = mapped_column(String(20), default="operational", server_default="operational")
    source_origin: Mapped[str] = mapped_column(String(20), default="seed", server_default="seed")
    citation: Mapped[str | None] = mapped_column(String(500), nullable=True)
    effective_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    last_verified: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    superseded_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("neurons.id"), nullable=True)
    source_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    external_references: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    # Semantic embedding (384-dim vector, JSON-encoded float array)
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Denormalized from highest-authority linked source document
    authority_level: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Reverse link to the proposal item that created this neuron (if any)
    proposal_item_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("proposal_items.id"), nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    # Updated on each neuron firing — for age-based integrity review
    last_accessed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    # Tiered edges: weak edges (below promotion threshold) stored as JSONB
    # Format: {"<peer_id>": {"w": 0.05, "t": "pyramidal", "c": 1, "s": "organic", "q": 42}}
    # Bidirectional: edge(A,B) stored on min(A,B) keyed by str(max(A,B))
    weak_edges: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Normalized degree centrality over promoted edges [0, 1].
    # Denormalized for the cold-start prior; refreshed by consolidation.
    centrality: Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0")

    # Generic region vocabulary for the engine (silos = labeled regions).
    region = synonym("department")

    parent: Mapped["Neuron | None"] = relationship("Neuron", remote_side=[id], foreign_keys=[parent_id], lazy="selectin")
    firings: Mapped[list["NeuronFiring"]] = relationship("NeuronFiring", back_populates="neuron", lazy="select")


class NeuronFiring(Base):
    __tablename__ = "neuron_firings"
    __table_args__ = (
        Index("ix_neuron_firings_neuron_offset", "neuron_id", "global_query_offset"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    neuron_id: Mapped[int] = mapped_column(Integer, ForeignKey("neurons.id"), nullable=False, index=True)
    query_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("queries.id"), nullable=True, index=True)
    context_type: Mapped[str] = mapped_column(String(50), default="direct")
    outcome: Mapped[str | None] = mapped_column(String(50), nullable=True)
    global_token_offset: Mapped[int] = mapped_column(Integer, default=0)
    global_query_offset: Mapped[int] = mapped_column(Integer, default=0, index=True)
    # Enriched scoring data (Pattern #2 — bidirectional lineage)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    combined_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    burst: Mapped[float | None] = mapped_column(Float, nullable=True)
    impact: Mapped[float | None] = mapped_column(Float, nullable=True)
    precision: Mapped[float | None] = mapped_column(Float, nullable=True)
    novelty: Mapped[float | None] = mapped_column(Float, nullable=True)
    recency: Mapped[float | None] = mapped_column(Float, nullable=True)
    relevance: Mapped[float | None] = mapped_column(Float, nullable=True)
    spread_boost: Mapped[float | None] = mapped_column(Float, nullable=True)
    prompt_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    was_included: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )

    neuron: Mapped["Neuron"] = relationship("Neuron", back_populates="firings")


class NeuronScoreOverride(Base):
    """Per-neuron signal overrides for manual graph tuning.

    Each row applies a floor, ceiling, or multiplier to one scoring signal
    for a specific neuron. Multiple overrides can target the same neuron
    (one per signal). Applied by the scoring engine at scoring time.
    """
    __tablename__ = "neuron_score_overrides"
    __table_args__ = (
        Index("ix_nso_neuron_signal", "neuron_id", "signal", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    neuron_id: Mapped[int] = mapped_column(Integer, ForeignKey("neurons.id"), nullable=False, index=True)
    signal: Mapped[str] = mapped_column(String(20), nullable=False)  # burst|impact|precision|novelty|recency|relevance|combined
    floor: Mapped[float | None] = mapped_column(Float, nullable=True)       # minimum value for this signal
    ceiling: Mapped[float | None] = mapped_column(Float, nullable=True)     # maximum value for this signal
    multiplier: Mapped[float | None] = mapped_column(Float, nullable=True)  # scale factor (applied before floor/ceiling)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class NeuronEdge(Base):
    __tablename__ = "neuron_edges"

    source_id: Mapped[int] = mapped_column(Integer, ForeignKey("neurons.id"), primary_key=True)
    target_id: Mapped[int] = mapped_column(Integer, ForeignKey("neurons.id"), primary_key=True)
    co_fire_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    weight: Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0")
    last_updated_query: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Typed edges: "stellate" (intra-department, local) or "pyramidal" (cross-department, long-range)
    edge_type: Mapped[str | None] = mapped_column(String(20), nullable=True, server_default="pyramidal")
    # Provenance: "organic" (real query firing), "bootstrap" (pre-seeded prior), "concept_seed" (concept linking)
    source: Mapped[str | None] = mapped_column(String(20), nullable=True, server_default="organic")
    # When this edge was last modified (by any source)
    last_adjusted: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True, server_default=func.now())
    # Short text explaining WHY two neurons are linked
    context: Mapped[str | None] = mapped_column(String(300), nullable=True)


class SourceDocument(Base):
    __tablename__ = "source_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)  # e.g., "AS9100D", "FAR 31.205"
    family: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # e.g., "FAR", "AS", "MIL-STD"
    version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active", server_default="active")  # active/superseded/draft/withdrawn
    authority_level: Mapped[str] = mapped_column(String(30), nullable=False)  # binding_standard / regulatory / industry_practice / organizational / informational
    issuing_body: Mapped[str | None] = mapped_column(String(200), nullable=True)
    effective_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    superseded_by_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("source_documents.id"), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())


class NeuronSourceLink(Base):
    __tablename__ = "neuron_source_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    neuron_id: Mapped[int] = mapped_column(Integer, ForeignKey("neurons.id"), nullable=False, index=True)
    source_document_id: Mapped[int] = mapped_column(Integer, ForeignKey("source_documents.id"), nullable=False, index=True)
    derivation_type: Mapped[str] = mapped_column(String(30), nullable=False, default="references", server_default="references")  # derived_from / references / implements / constrained_by
    section_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)  # specific clause, e.g., "31.205-6(b)"
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="current", server_default="current")  # current / stale / broken
    flagged_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    reviewed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    link_origin: Mapped[str] = mapped_column(String(20), nullable=False, default="auto_detected", server_default="auto_detected")  # auto_detected / ingest / manual
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())


class InhibitoryRegulator(Base):
    """GABAergic interneuron analogue — monitors regional activation density and suppresses over-firing."""
    __tablename__ = "inhibitory_regulators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    region_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "department" | "role_key"
    region_value: Mapped[str] = mapped_column(String(100), nullable=False)
    inhibition_strength: Mapped[float] = mapped_column(Float, default=0.5, server_default="0.5")
    activation_threshold: Mapped[int] = mapped_column(Integer, default=15, server_default="15")
    max_survivors: Mapped[int] = mapped_column(Integer, default=8, server_default="8")
    redundancy_cosine_threshold: Mapped[float] = mapped_column(Float, default=0.92, server_default="0.92")
    total_suppressions: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_activations: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    avg_post_suppression_utility: Mapped[float] = mapped_column(Float, default=0.5, server_default="0.5")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())


class Query(Base):
    __tablename__ = "queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_message: Mapped[str] = mapped_column(Text, nullable=False)
    classified_intent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    classified_departments: Mapped[str | None] = mapped_column(Text, nullable=True)
    classified_role_keys: Mapped[str | None] = mapped_column(Text, nullable=True)
    classified_keywords: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_neuron_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    assembled_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    opus_response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    opus_input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    opus_output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    run_neuron: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    run_opus: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    results_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array of slot results
    eval_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    eval_model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    eval_input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    eval_output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    user_rating: Mapped[float | None] = mapped_column(Float, nullable=True)
    classify_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    classify_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    execute_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    execute_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    refine_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    neuron_scores_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    model_version: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Pattern #5: typed pipeline DAG telemetry — per-stage timing + status snapshot.
    # Shape: list[{stage, status, duration_ms, detail?, error_message?}]
    stage_telemetry_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class SystemState(Base):
    __tablename__ = "system_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    global_token_counter: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_consolidation_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    total_queries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class IntentNeuronMap(Base):
    __tablename__ = "intent_neuron_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    intent_label: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    neuron_id: Mapped[int] = mapped_column(Integer, ForeignKey("neurons.id"), nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    avg_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0")


class EvalScore(Base):
    __tablename__ = "eval_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    query_id: Mapped[int] = mapped_column(Integer, ForeignKey("queries.id"), nullable=False, index=True)
    eval_model: Mapped[str] = mapped_column(String(50), nullable=False)
    answer_mode: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "haiku_neuron"
    answer_label: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "A"
    accuracy: Mapped[int] = mapped_column(Integer, nullable=False)      # 1-5
    completeness: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-5
    clarity: Mapped[int] = mapped_column(Integer, nullable=False)       # 1-5
    faithfulness: Mapped[int] = mapped_column(Integer, nullable=False)  # 1-5 (5 = no hallucinations)
    overall: Mapped[int] = mapped_column(Integer, nullable=False)       # 1-5
    verdict: Mapped[str | None] = mapped_column(Text, nullable=True)    # free-text verdict
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class NeuronRefinement(Base):
    __tablename__ = "neuron_refinements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    query_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("queries.id"), nullable=True, index=True)
    neuron_id: Mapped[int] = mapped_column(Integer, ForeignKey("neurons.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # "update" | "create"
    field: Mapped[str | None] = mapped_column(String(50), nullable=True)  # for updates: content/summary/label/is_active
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class AutopilotConfig(Base):
    __tablename__ = "autopilot_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    directive: Mapped[str] = mapped_column(Text, default="", server_default="")
    interval_minutes: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    focus_neuron_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("neurons.id"), nullable=True)
    max_layer: Mapped[int] = mapped_column(Integer, default=5, server_default="5")
    eval_model: Mapped[str] = mapped_column(String(20), default="haiku", server_default="haiku")
    last_tick_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)


class AutopilotRun(Base):
    __tablename__ = "autopilot_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    query_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("queries.id"), nullable=True)
    proposal_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("autopilot_proposals.id"), nullable=True)
    generated_query: Mapped[str] = mapped_column(Text, nullable=False)
    directive: Mapped[str] = mapped_column(Text, nullable=False)
    focus_neuron_label: Mapped[str | None] = mapped_column(String(500), nullable=True)
    gap_source: Mapped[str | None] = mapped_column(String(30), nullable=True)  # emergent_queue|low_eval|thin_neuron|sparse_subtree|directive
    gap_target: Mapped[str | None] = mapped_column(Text, nullable=True)  # Human-readable gap description
    neurons_activated: Mapped[int] = mapped_column(Integer, default=0)
    updates_applied: Mapped[int] = mapped_column(Integer, default=0)
    neurons_created: Mapped[int] = mapped_column(Integer, default=0)
    eval_overall: Mapped[int] = mapped_column(Integer, default=3)
    eval_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    refine_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(20), default="completed")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Pattern #8: typed pipeline DAG telemetry — per-stage timing + status snapshot
    # captured by the autopilot tick runner. Shape mirrors Query.stage_telemetry_json:
    # list[{stage, status, duration_ms, detail?, error_message?}]
    stage_telemetry_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class AutopilotProposal(Base):
    """Staged autopilot proposal with full provenance chain.

    State machine: proposed → approved → applied
                   proposed → rejected
    """
    __tablename__ = "autopilot_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    autopilot_run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("autopilot_runs.id"), nullable=True, index=True)
    query_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("queries.id"), nullable=True, index=True)
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="proposed", server_default="proposed")
    # Gap provenance
    gap_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    gap_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    gap_evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # Serialized list[GapEvidence]
    priority_score: Mapped[float] = mapped_column(Float, default=0.0)
    # LLM provenance
    llm_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # SHA-256
    # Eval context
    eval_overall: Mapped[int] = mapped_column(Integer, default=0)
    eval_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Approval provenance
    reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    applied_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Timestamps
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    items: Mapped[list["ProposalItem"]] = relationship("ProposalItem", back_populates="proposal", lazy="selectin")


class ProposalItem(Base):
    """Individual mutation within a proposal (update or create)."""
    __tablename__ = "proposal_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    proposal_id: Mapped[int] = mapped_column(Integer, ForeignKey("autopilot_proposals.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # "update" | "create"
    target_neuron_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("neurons.id"), nullable=True)
    field: Mapped[str | None] = mapped_column(String(50), nullable=True)  # For updates: content/summary/label/is_active
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    neuron_spec_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # For creates: full neuron spec
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # After application
    created_neuron_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("neurons.id"), nullable=True)
    refinement_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("neuron_refinements.id"), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    proposal: Mapped["AutopilotProposal"] = relationship("AutopilotProposal", back_populates="items")


class PropagationLog(Base):
    __tablename__ = "propagation_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query_id: Mapped[int] = mapped_column(Integer, ForeignKey("queries.id"), nullable=False)
    source_neuron_id: Mapped[int] = mapped_column(Integer, ForeignKey("neurons.id"), nullable=False)
    target_neuron_id: Mapped[int] = mapped_column(Integer, ForeignKey("neurons.id"), nullable=False)
    activation_value: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class SynapticLearningEvent(Base):
    __tablename__ = "synaptic_learning_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query_id: Mapped[int] = mapped_column(Integer, ForeignKey("queries.id"), nullable=False, index=True)
    neuron_id: Mapped[int] = mapped_column(Integer, ForeignKey("neurons.id"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "reward" | "penalty"
    old_avg_utility: Mapped[float] = mapped_column(Float, nullable=False)
    new_avg_utility: Mapped[float] = mapped_column(Float, nullable=False)
    delta: Mapped[float] = mapped_column(Float, nullable=False)
    effective_delta: Mapped[float] = mapped_column(Float, nullable=False)
    combined_score: Mapped[float] = mapped_column(Float, nullable=False)
    attribution_weight: Mapped[float] = mapped_column(Float, nullable=False)
    outcome: Mapped[str] = mapped_column(String(10), nullable=False)  # "win" | "loss"
    winner_mode: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class SystemAlert(Base):
    __tablename__ = "system_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "drift" | "quality_drop" | "api_change" | "circuit_breaker"
    severity: Mapped[str] = mapped_column(String(20), nullable=False)    # "info" | "warning" | "critical"
    signal: Mapped[str | None] = mapped_column(String(50), nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    acknowledged: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    acknowledged_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class EmergentQueue(Base):
    __tablename__ = "emergent_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    citation_pattern: Mapped[str] = mapped_column(String(200), nullable=False)
    domain: Mapped[str] = mapped_column(String(20), nullable=False)  # "regulatory" or "technical"
    family: Mapped[str | None] = mapped_column(String(50), nullable=True)
    detection_count: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    first_detected_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    last_detected_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    detected_in_neuron_ids: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    detected_in_query_ids: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    resolved_neuron_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("neurons.id"), nullable=True)
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class ObservationQueue(Base):
    """Corvus observation ingestion queue — observations awaiting review or already processed."""
    __tablename__ = "observation_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="corvus")
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, default="anonymous")
    observation_type: Mapped[str] = mapped_column(String(30), nullable=False)  # decision|process|entity|pattern|digest
    text: Mapped[str] = mapped_column(Text, nullable=False)
    entities_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    app_context: Mapped[str | None] = mapped_column(String(100), nullable=True)
    project_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    proposed_department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    proposed_role_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    proposed_layer: Mapped[int] = mapped_column(Integer, default=3)
    similar_neuron_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("neurons.id"), nullable=True)
    similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", server_default="queued")  # queued|evaluated|approved|rejected|duplicate
    created_neuron_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("neurons.id"), nullable=True)
    eval_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    eval_model: Mapped[str | None] = mapped_column(String(20), nullable=True)
    eval_input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    eval_output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())


class ProjectProfile(Base):
    __tablename__ = "project_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_path: Mapped[str] = mapped_column(String(500), unique=True, nullable=False)
    project_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    neuron_relevance: Mapped[str] = mapped_column(Text, default="{}", server_default="{}")  # JSON: {neuron_id: cumulative_score}
    query_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_query_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())


class BatchJob(Base):
    __tablename__ = "batch_jobs"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # short uuid
    status: Mapped[str] = mapped_column(String(20), default="running", server_default="running")  # running|done|cancelled|interrupted|error
    step: Mapped[str] = mapped_column(String(200), default="Starting...")
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    current_chunk: Mapped[int] = mapped_column(Integer, default=0)
    proposals_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")  # JSON array
    errors_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")  # JSON array
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    citation: Mapped[str] = mapped_column(String(200), default="")
    source_type: Mapped[str] = mapped_column(String(50), default="regulatory_primary")
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    role_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parent_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    queue_entry_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_chars: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str] = mapped_column(String(20), default="haiku")
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    effective_date: Mapped[str | None] = mapped_column(String(20), nullable=True)
    chunks_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array of chunk texts for resume
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)  # stored for resume
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class DocumentIngestJob(Base):
    """Tracks a document ingestion pipeline run (upload -> parse -> extract -> propose)."""
    __tablename__ = "document_ingest_jobs"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default="uploading", server_default="uploading")
    step: Mapped[str] = mapped_column(String(200), default="Uploading...")
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_format: Mapped[str] = mapped_column(String(10), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    total_pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(500), default="")
    source_type: Mapped[str] = mapped_column(String(50), default="operational")
    authority_level: Mapped[str] = mapped_column(String(30), default="guidance")
    citation: Mapped[str] = mapped_column(String(500), default="")
    source_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    department: Mapped[str | None] = mapped_column(String(100), nullable=True)
    role_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    structure_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_sections: Mapped[int] = mapped_column(Integer, default=0)
    current_section: Mapped[int] = mapped_column(Integer, default=0)
    proposal_ids_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    model: Mapped[str] = mapped_column(String(50), default="sonnet")
    duplicates_flagged: Mapped[int] = mapped_column(Integer, default=0)
    errors_json: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ManagementReview(Base):
    __tablename__ = "management_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_type: Mapped[str] = mapped_column(String(50), nullable=False)  # pii_audit | scoring_health | governance_review | incident_review | compliance_audit | neuron_expansion | model_change
    reviewer: Mapped[str] = mapped_column(String(200), nullable=False)
    review_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    findings: Mapped[str] = mapped_column(Text, nullable=False)
    decisions: Mapped[str] = mapped_column(Text, nullable=False)
    action_items: Mapped[str] = mapped_column(Text, default="[]", server_default="[]")  # JSON array: [{description, due_date, completed}]
    status: Mapped[str] = mapped_column(String(20), default="completed", server_default="completed")  # completed | action_required | escalated
    compliance_snapshot_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class ComplianceSnapshot(Base):
    __tablename__ = "compliance_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_date: Mapped[datetime.datetime] = mapped_column(DateTime, nullable=False)
    snapshot_data: Mapped[str] = mapped_column(Text, nullable=False)  # Full JSON of compliance-audit response
    pii_clean: Mapped[bool] = mapped_column(Boolean, nullable=False)
    coverage_cv: Mapped[float] = mapped_column(Float, nullable=False)
    fairness_pass: Mapped[bool] = mapped_column(Boolean, nullable=False)
    missing_citations_count: Mapped[int] = mapped_column(Integer, nullable=False)
    stale_neurons_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_neurons: Mapped[int] = mapped_column(Integer, nullable=False)
    total_evals: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger: Mapped[str] = mapped_column(String(30), default="manual", server_default="manual")  # manual | scheduled | pre_expansion
    diff_summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON diff vs previous snapshot
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())


class EvidenceMapping(Base):
    __tablename__ = "evidence_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    framework: Mapped[str] = mapped_column(String(30), nullable=False)  # nist_ai_rmf | aiuc_1 | iso_42001
    requirement_id: Mapped[str] = mapped_column(String(30), nullable=False)
    requirement_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="gap", server_default="gap")  # addressed | partial | gap | not_applicable
    evidence_type: Mapped[str] = mapped_column(String(20), nullable=False)  # endpoint | table | document | review_log | code
    evidence_location: Mapped[str] = mapped_column(String(500), nullable=False)
    verification_query: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_verified: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    last_verified_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AuditLog(Base):
    """Immutable audit trail for all state-changing API actions.

    Addresses: NIST 800-53 AU-2/AU-3/AU-4/AU-8/AU-12, CMMC 3.3.1/3.3.5/3.3.6,
    SOC 2 CC7.2/CC7.3, FedRAMP AU family.
    """
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    action: Mapped[str] = mapped_column(String(10), nullable=False)  # POST, PUT, DELETE, PATCH
    endpoint: Mapped[str] = mapped_column(String(500), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)  # IPv4 or IPv6
    request_body_summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # Truncated, no secrets
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    archived_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    messages: Mapped[list["ChatSessionMessage"]] = relationship(
        "ChatSessionMessage", back_populates="session", lazy="selectin",
        order_by="ChatSessionMessage.created_at",
    )


class ChatSessionMessage(Base):
    __tablename__ = "chat_session_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("chat_sessions.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cost: Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0")
    neurons_activated: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    neuron_scores_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    session: Mapped["ChatSession"] = relationship("ChatSession", back_populates="messages")


# ═══════════════════════════════════════════════════════════════════
# ENGRAMS — retrieval indices for external authoritative sources.
# Engrams participate in neuron scoring but fetch content live from
# government APIs (eCFR) at query time.  The engram persists; its
# resolved content is ephemeral.
# ═══════════════════════════════════════════════════════════════════


class Engram(Base):
    __tablename__ = "engrams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Scoring-compatible fields (shared interface with Neuron)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)  # retrieval cues / key terms, NOT regulation text
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)  # 384-dim JSON
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    invocations: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    avg_utility: Mapped[float] = mapped_column(Float, default=0.5, server_default="0.5")
    created_at_query_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Regulatory source coordinates — maps directly to eCFR API URL structure
    cfr_title: Mapped[int] = mapped_column(Integer, nullable=False)                        # e.g. 48 (Title 48)
    cfr_part: Mapped[str] = mapped_column(String(20), nullable=False)                      # e.g. "31"
    cfr_section: Mapped[str | None] = mapped_column(String(30), nullable=True)             # e.g. "205-14"
    source_api: Mapped[str] = mapped_column(String(30), default="ecfr", server_default="ecfr")  # ecfr | federal_register

    # Ephemeral cache — resolved text lives here between TTL refreshes
    cached_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    cached_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    cache_ttl_hours: Mapped[int] = mapped_column(Integer, default=24, server_default="24")
    cached_token_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Provenance
    authority_level: Mapped[str] = mapped_column(String(30), default="regulatory", server_default="regulatory")
    issuing_body: Mapped[str | None] = mapped_column(String(200), nullable=True)
    effective_date: Mapped[datetime.date | None] = mapped_column(Date, nullable=True)
    last_verified: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    firings: Mapped[list["EngramFiring"]] = relationship("EngramFiring", back_populates="engram", lazy="select")


class EngramFiring(Base):
    __tablename__ = "engram_firings"
    __table_args__ = (
        Index("ix_engram_firings_engram_offset", "engram_id", "global_query_offset"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    engram_id: Mapped[int] = mapped_column(Integer, ForeignKey("engrams.id"), nullable=False, index=True)
    query_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("queries.id"), nullable=True, index=True)
    global_query_offset: Mapped[int] = mapped_column(Integer, default=0, index=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    engram: Mapped["Engram"] = relationship("Engram", back_populates="firings")


class EngramEdge(Base):
    """Engram-to-neuron co-firing edges. Separate from NeuronEdge to keep adjacency caches clean."""
    __tablename__ = "engram_edges"

    engram_id: Mapped[int] = mapped_column(Integer, ForeignKey("engrams.id"), primary_key=True)
    neuron_id: Mapped[int] = mapped_column(Integer, ForeignKey("neurons.id"), primary_key=True)
    co_fire_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    weight: Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0")
    edge_type: Mapped[str] = mapped_column(String(20), default="regulatory", server_default="regulatory")
    source: Mapped[str] = mapped_column(String(20), default="bootstrap", server_default="bootstrap")
    last_updated_query: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    context: Mapped[str | None] = mapped_column(String(300), nullable=True)


# ═══════════════════════════════════════════════════════════════════
# INTEGRITY — graph self-consistency monitoring and repair.
# Inspired by neurological self-correcting processes: synaptic
# homeostasis, pattern separation/completion, conflict monitoring,
# and age-based reconsolidation review.
# ═══════════════════════════════════════════════════════════════════


class IntegrityScan(Base):
    """A single integrity scan execution with its parameters and results."""
    __tablename__ = "integrity_scans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(100), nullable=False, server_default="global")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="running")
    parameters_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    findings_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    initiated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    findings: Mapped[list["IntegrityFinding"]] = relationship(
        "IntegrityFinding", back_populates="scan", lazy="selectin",
    )


class IntegrityFinding(Base):
    """Individual finding from an integrity scan, awaiting human review."""
    __tablename__ = "integrity_findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_id: Mapped[int] = mapped_column(Integer, ForeignKey("integrity_scans.id"), nullable=False, index=True)
    finding_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, server_default="warning")
    priority_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0.0")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    neuron_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    edge_ids_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="open", index=True)
    resolution: Mapped[str | None] = mapped_column(String(30), nullable=True)
    proposal_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("autopilot_proposals.id"), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    scan: Mapped["IntegrityScan"] = relationship("IntegrityScan", back_populates="findings")


class Action(Base):
    """Universal write primitive — every state mutation passes through one of these.

    Generalizes the AutopilotProposal -> apply state machine into a typed,
    validated, audited record. Most actions auto-apply (requires_approval=False);
    sensitive ones (e.g., admin operations, classification changes) can require
    review. Actions form a parent/child tree so an autopilot proposal that emits
    multiple writes is grouped under a single root.
    """

    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Identity
    kind: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # actor_type: "user" | "autopilot" | "system" | "external_agent"
    actor_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Payload
    input_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Provenance — what triggered this action
    source_query_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("queries.id"), nullable=True, index=True,
    )
    source_proposal_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("autopilot_proposals.id"), nullable=True, index=True,
    )
    parent_action_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("actions.id"), nullable=True, index=True,
    )

    # Approval state machine — pending -> applied/rejected/failed
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending",
        server_default="pending", index=True,
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Outcome
    applied_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Idempotency — client-supplied unique key prevents double-execution on retry
    idempotency_key: Mapped[str | None] = mapped_column(
        String(200), nullable=True, unique=True,
    )

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())


class OutputViolation(Base):
    """Runtime policy-gate violation record.

    Written once per (query, rule) match when an output_guard policy flags
    the LLM response at generation time. Distinct from OutputCheckOut
    (eval-time, advisory) — these are load-bearing: ``action="block"``
    halts the response (422), ``redact`` mutates it in place, ``flag``
    attaches for auditor review without altering the answer.

    Pattern #7 — Runtime output policy gates (Phase 1.5 GTM gate).
    """

    __tablename__ = "output_violations"
    __table_args__ = (
        Index("ix_output_violations_query_severity", "query_id", "severity"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Provenance
    query_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("queries.id"), nullable=True, index=True,
    )

    # Policy identity
    rule_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    # "info" | "warn" | "error" | "critical"
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    # "flag" | "redact" | "block"

    # What matched + optional structured detail
    matched_span: Mapped[str | None] = mapped_column(Text, nullable=True)
    redaction: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Audit link to the action_bus "output.policy.check" call
    action_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("actions.id"), nullable=True, index=True,
    )

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())


class EvalRun(Base):
    """Immutable, append-only eval run artifact.

    Pattern #3 — Evals as immutable, first-class artifacts.

    One row per suite execution. Captures the full snapshot a safety
    reviewer would ask for: which suite, which model versions, which
    scoring-engine version, which NeuronScoreOverride rows were active,
    and aggregate verdicts. Paired with EvalRunCase children (one per
    query). Once status transitions out of ``running``, fields are never
    updated — re-runs create new rows. The ``eval_run_id`` exposed on
    ``/v1/query`` responses is the currently-certified run (see
    TenantConfig.certified_eval_run_id).
    """

    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    suite_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    suite_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    # Snapshotted versioning context — what produced these results
    model_versions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    scoring_engine_version: Mapped[str] = mapped_column(String(50), nullable=False)
    overrides_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Aggregate results — pass/fail/blocked counts, violations by severity
    summary_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Lifecycle — running -> completed | failed. Append-only beyond that.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running",
        server_default="running", index=True,
    )
    started_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False,
    )
    completed_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    started_by: Mapped[str] = mapped_column(String(100), nullable=False)

    # Audit link to the action_bus "eval.run.start" call
    action_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("actions.id"), nullable=True, index=True,
    )

    cases: Mapped[list["EvalRunCase"]] = relationship(
        "EvalRunCase", back_populates="run", lazy="select",
        cascade="all, delete-orphan",
    )


class EvalRunCase(Base):
    """One query result within an EvalRun. Append-only after run completes."""

    __tablename__ = "eval_run_cases"
    __table_args__ = (
        Index("ix_eval_run_cases_run_label", "eval_run_id", "case_label"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    eval_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("eval_runs.id"), nullable=False, index=True,
    )
    case_label: Mapped[str] = mapped_column(String(200), nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)

    # Execution result
    query_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("queries.id"), nullable=True, index=True,
    )
    response_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    lineage_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    violations_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    scores_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    run: Mapped["EvalRun"] = relationship("EvalRun", back_populates="cases", lazy="select")


class TenantConfig(Base):
    """Single-row per-tenant runtime config. Currently holds the certified
    EvalRun pointer (Pattern #3); future: other per-tenant live pointers.

    One DB == one tenant in this architecture, so id is fixed at 1.
    """

    __tablename__ = "tenant_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    certified_eval_run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("eval_runs.id"), nullable=True,
    )
    certified_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    certified_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(),
    )
