"""Pydantic request/response DTOs."""

from pydantic import BaseModel, Field


class QuerySlotRequest(BaseModel):
    mode: str = Field(..., min_length=1)  # e.g. "haiku_neuron", "sonnet_raw", "opus_neuron"
    token_budget: int = Field(8000, ge=1000, le=32000)
    # None means "tenant policy". An explicit value remains a hard
    # candidate/delivery safety cap for compatibility.
    top_k: int | None = Field(None, ge=1, le=500)
    max_output_tokens: int | None = Field(None, ge=256, le=8192)
    label: str | None = None
    # Per-slot reasoning effort override (None = inherit the request-level
    # effort). Enables side-by-side effort comparisons: same model, low vs high.
    effort: str | None = Field(None, pattern="^(low|medium|high)$")
    # Workspace priming: prefix this slot's prompt with a one-line topic
    # preamble built from the packed sources. Default ON (A/B eval 2026-07:
    # +0.83 completeness, no accuracy/faithfulness cost) — front-facing chat
    # inherits it; Query Lab sends an explicit per-card value so it stays
    # toggleable for A/B comparisons. No-op for raw slots (no packed context).
    priming: bool = True
    # Per-slot spread-activation overrides (None = tenant defaults, currently
    # 3 hops / 0.15 floor). Slots with distinct values get their own context
    # prep so associative reach is comparable side-by-side. No-op for raw slots.
    spread_hops: int | None = Field(None, ge=1, le=6)
    spread_floor: float | None = Field(None, ge=0.0, le=0.5)
    # Audit-grade answer (arch-tier-routing): the explicit opus@low action —
    # max-precision terse answers (measured acc 4.7 / faithfulness 5.0). Forces
    # opus at low effort, bypassing the effort floor and tier routing. An
    # explicit user action, deliberately NOT a routing tier.
    audit: bool = False


class QueryRequest(BaseModel):
    # max_length bumped from 5000 → 50000 (2026-04-23) to accommodate the
    # packed conversation history the hero page includes on follow-ups.
    # 5000 was a single-turn assumption; real sessions accumulate.
    # Upstream the LLM's own context window still bounds effective size,
    # and the hero's "Summarize older messages" button is the escape
    # valve when that bound is reached.
    message: str = Field(..., min_length=1, max_length=50000)
    slots: list[QuerySlotRequest] | None = None  # Multi-slot testing; if None, use default single slot
    prior_neuron_ids: list[int] | None = None
    effort: str | None = None  # reasoning effort: low|medium|high (None -> settings.default_effort)
    # Persisted CLI session (hero chat): opt in with persist_session; pass the
    # llm_session_id returned by the previous turn to resume it. The server
    # then carries conversation memory (prompt-cached) so the client does NOT
    # pack history into `message`. Claude models only; other providers return
    # no session id and the client falls back to history packing.
    persist_session: bool = False
    llm_session_id: str | None = Field(None, max_length=64, pattern="^[0-9a-fA-F-]{8,64}$")
    # Force a fresh context pack this turn even if the drift gate would reuse
    # (user-driven re-ground, e.g. after consolidation).
    refresh_context: bool = False
    # Audit-grade answer action: run the primary slot as opus@low (see
    # QuerySlotRequest.audit). Convenience for single-slot callers (hero chat)
    # — marks slot 0 audit, creating the default slot when none was sent.
    audit_grade: bool = False


class SlotResult(BaseModel):
    mode: str
    model: str
    neurons: bool
    # Layer-2 citation grounding (log-only): per-claim relevance scores
    citation_relevance: dict | None = None
    response: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    observed_total_input_tokens: int = 0
    estimated_memory_tokens: int = 0
    memory_estimation_error_tokens: int | None = None
    token_budget: int | None = None
    top_k: int | None = None
    label: str | None = None
    # Tier-elastic routing telemetry (primary slot of single-slot queries):
    # {escalated, model, reasons, signals} — recorded even when not escalated
    # so thresholds stay recalibratable. None for compare/audit/raw slots.
    routing: dict | None = None
    # True when this slot ran as the explicit audit-grade action (opus@low).
    audit_grade: bool = False


class NeuronScoreResponse(BaseModel):
    neuron_id: int
    combined: float
    burst: float
    impact: float
    precision: float
    novelty: float
    recency: float
    relevance: float
    spread_boost: float = 0
    label: str | None = None
    department: str | None = None
    layer: int = 0
    parent_id: int | None = None
    summary: str | None = None


class InputGuardOut(BaseModel):
    verdict: str = "pass"  # "pass" | "warn" | "block"
    flags: list[dict] = []
    flag_count: int = 0


class GroundingOut(BaseModel):
    grounded: bool | None = None
    confidence: float | None = None
    overlap_terms: int | None = None
    response_terms: int | None = None
    ungrounded_references: list[str] = []
    reason: str = ""


class OutputCheckOut(BaseModel):
    mode: str | None = None
    risk_flags: list[dict] = []
    grounding: GroundingOut | None = None
    # Opt-in claim-entailment pass (advisory): checked/unsupported_count/results/status
    entailment: dict | None = None


class OutputViolationOut(BaseModel):
    """Serialized runtime policy-gate violation (Pattern #7)."""

    id: int
    rule_id: str
    severity: str
    action: str
    matched_span: str | None = None
    redaction: str | None = None
    detail: dict | None = None


class StageTelemetryOut(BaseModel):
    """Per-stage timing + status for the query-prep pipeline (Pattern #5)."""
    stage: str
    status: str  # "done" | "error" | "skipped"
    duration_ms: float
    detail: dict | None = None
    error_message: str | None = None


class QueryResponse(BaseModel):
    query_id: int
    llm_session_id: str | None = None  # persisted-session id (pass back to resume)
    context_reused: bool = False       # drift gate reused the session's active context
    context_overlap: float | None = None  # fresh-pack vs active-pack overlap (calibration)
    # Tier-elastic routing decision for the primary slot (None when routing
    # didn't run: disabled, multi-slot compare, raw slot, or audit action).
    tier_routing: dict | None = None
    intent: str | None = None
    departments: list[str] = []
    role_keys: list[str] = []
    keywords: list[str] = []
    neurons_activated: int = 0
    candidates_considered: int = 0
    neurons_delivered: int = 0
    estimated_memory_tokens: int = 0
    memory_context_chars: int = 0
    memory_context_utf8_bytes: int = 0
    memory_token_budget: int = 0
    assembly_stop_reason: str | None = None
    redundancy_suppressed: int = 0
    token_estimator_version: str | None = None
    oversized_first_neuron: bool = False
    recall_latency_ms: float = 0.0
    observed_total_model_input_tokens: int = 0
    neuron_scores: list[NeuronScoreResponse] = []
    classify_cost: float = 0
    classify_input_tokens: int = 0
    classify_output_tokens: int = 0
    slots: list[SlotResult] = []
    total_cost: float = 0
    input_guard: InputGuardOut | None = None
    output_checks: list[OutputCheckOut] = []
    output_violations: list[OutputViolationOut] = []
    stage_telemetry: list[StageTelemetryOut] = []
    failed_stage: str | None = None  # populated on hard-fail responses


class EvalRequest(BaseModel):
    model: str = Field("sonnet", min_length=1)


class EvalScoreOut(BaseModel):
    answer_label: str
    answer_mode: str
    # 1-5 in half steps (unrounded mean of counterbalanced judge passes)
    accuracy: float
    completeness: float
    clarity: float
    faithfulness: float
    overall: float


class SynapticLearningOut(BaseModel):
    outcome: str
    winner_mode: str | None = None
    neurons_adjusted: int = 0
    edges_adjusted: int = 0
    avg_delta: float = 0.0
    total_reward: float = 0.0
    total_penalty: float = 0.0


class EvalResponse(BaseModel):
    query_id: int
    eval_text: str
    eval_model: str
    eval_input_tokens: int
    eval_output_tokens: int
    scores: list[EvalScoreOut] = []
    winner: str | None = None
    learning: SynapticLearningOut | None = None


class LearningEventOut(BaseModel):
    id: int
    query_id: int
    neuron_id: int
    neuron_label: str | None = None
    event_type: str
    old_avg_utility: float
    new_avg_utility: float
    effective_delta: float
    combined_score: float
    attribution_weight: float
    outcome: str
    winner_mode: str | None = None
    created_at: str | None = None


class LearningAnalytics(BaseModel):
    total_events: int
    total_wins: int
    total_losses: int
    avg_reward: float
    avg_penalty: float
    recent_events: list[LearningEventOut] = []


class EvalScoreSummary(BaseModel):
    id: int
    query_id: int
    eval_model: str
    answer_mode: str
    answer_label: str
    # 1-5 in half steps (unrounded mean of counterbalanced judge passes)
    accuracy: float
    completeness: float
    clarity: float
    faithfulness: float
    overall: float
    created_at: str | None


class RatingRequest(BaseModel):
    utility: float = Field(..., ge=0.0, le=1.0)


class RatingResponse(BaseModel):
    query_id: int
    utility: float
    neurons_updated: int


class NeuronDetail(BaseModel):
    id: int
    parent_id: int | None
    layer: int
    node_type: str
    label: str
    content: str | None
    summary: str | None
    department: str | None
    role_key: str | None
    invocations: int
    avg_utility: float
    is_active: bool
    cross_ref_departments: list[str] | None = None
    standard_date: str | None = None


class NeuronScoreDetail(BaseModel):
    neuron_id: int
    burst: float
    impact: float
    precision: float
    novelty: float
    recency: float
    relevance: float
    combined: float


class SeedResponse(BaseModel):
    status: str
    neuron_count: int


class ResetResponse(BaseModel):
    status: str


class MaintenanceWorkloadCost(BaseModel):
    workload: str
    calls: int
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int
    cache_read_tokens: int
    models: list[str]
    equivalent_cost_usd: float


class CostReportResponse(BaseModel):
    total_queries: int
    total_cost_usd: float
    avg_cost_per_query: float
    total_input_tokens: int
    total_output_tokens: int
    # Graph-upkeep cost at API list price (hot path is LLM-free; the jobs
    # below are what make it viable). Amortized over all queries.
    maintenance_cost_usd: float = 0.0
    maintenance_per_query_usd: float = 0.0
    maintenance_by_workload: list[MaintenanceWorkloadCost] = []
    maintenance_since: str | None = None


class QuerySummary(BaseModel):
    id: int
    user_message: str
    classified_intent: str | None
    modes: list[str] = []
    cost_usd: float | None
    user_rating: float | None
    created_at: str | None


class NeuronHit(BaseModel):
    neuron_id: int
    label: str
    layer: int
    department: str | None
    parent_id: int | None = None
    summary: str | None = None
    combined: float
    burst: float
    impact: float
    precision: float
    novelty: float
    recency: float
    relevance: float
    spread_boost: float = 0


class RefinementOut(BaseModel):
    id: int
    neuron_id: int
    action: str  # "update" | "create"
    field: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    reason: str | None = None
    neuron_label: str | None = None


class QueryDetail(BaseModel):
    id: int
    user_message: str
    classified_intent: str | None
    departments: list[str]
    role_keys: list[str]
    keywords: list[str]
    assembled_prompt: str | None
    classify_input_tokens: int
    classify_output_tokens: int
    classify_cost: float = 0
    slots: list[SlotResult] = []
    total_cost: float = 0
    user_rating: float | None
    eval_text: str | None = None
    eval_model: str | None = None
    eval_input_tokens: int = 0
    eval_output_tokens: int = 0
    eval_scores: list[EvalScoreOut] = []
    eval_winner: str | None = None
    neuron_hits: list[NeuronHit]
    refinements: list[RefinementOut] = []
    pending_refine: "RefineResponse | None" = None
    created_at: str | None


class RefineRequest(BaseModel):
    model: str = Field("opus", min_length=1)
    max_tokens: int = Field(4096, ge=512, le=16384)
    user_context: str | None = Field(None, max_length=16000)


class NeuronUpdateSuggestion(BaseModel):
    neuron_id: int
    field: str  # content, summary, label, is_active
    old_value: str
    new_value: str
    reason: str


class NewNeuronSuggestion(BaseModel):
    parent_id: int | None = None
    layer: int
    node_type: str
    label: str
    content: str
    summary: str
    department: str | None = None
    role_key: str | None = None
    reason: str


class RefineResponse(BaseModel):
    query_id: int
    model: str
    input_tokens: int
    output_tokens: int
    reasoning: str
    neuron_vs_raw_verdict: str = ""
    updates: list[NeuronUpdateSuggestion] = []
    new_neurons: list[NewNeuronSuggestion] = []


# Resolve forward reference now that RefineResponse is defined
QueryDetail.model_rebuild()


class ApplyRefineRequest(BaseModel):
    update_ids: list[int] = []
    new_neuron_ids: list[int] = []


class ApplyRefineResponse(BaseModel):
    updated: int
    created: int


class NeuronRefinementOut(BaseModel):
    id: int
    query_id: int | None = None
    neuron_id: int
    action: str
    field: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    reason: str | None = None
    created_at: str | None = None
    neuron_label: str | None = None
    query_snippet: str | None = None


class CheckpointResponse(BaseModel):
    status: str
    filename: str
    neuron_count: int
    commit_sha: str


class HealthResponse(BaseModel):
    status: str
    neuron_count: int
    total_queries: int


class AutopilotConfigOut(BaseModel):
    enabled: bool
    directive: str
    interval_minutes: int
    focus_neuron_id: int | None = None
    focus_neuron_label: str | None = None
    max_layer: int = 5
    eval_model: str = "haiku"
    last_tick_at: str | None = None


class AutopilotConfigUpdate(BaseModel):
    enabled: bool | None = None
    directive: str | None = None
    interval_minutes: int | None = None
    focus_neuron_id: int | None = Field(None, description="Neuron ID to focus on (L0-L5). Set to 0 to clear.")
    max_layer: int | None = Field(None, ge=0, le=5, description="Max layer depth for new neuron creation (0-5)")
    eval_model: str | None = Field(None, min_length=1)


class AutopilotRunOut(BaseModel):
    id: int
    query_id: int | None = None
    proposal_id: int | None = None
    generated_query: str
    directive: str
    focus_neuron_label: str | None = None
    gap_source: str | None = None
    gap_target: str | None = None
    neurons_activated: int
    updates_applied: int
    neurons_created: int
    eval_overall: int
    eval_text: str | None = None
    refine_reasoning: str | None = None
    cost_usd: float
    status: str
    error_message: str | None = None
    created_at: str | None = None
    # Pattern #8: per-stage timing + status from the autopilot-tick runner.
    # Shape: list[{stage, status, duration_ms, detail?, error_message?}]
    stage_telemetry: list[dict] | None = None


class AutopilotTickResponse(BaseModel):
    status: str
    run_id: int | None = None
    message: str | None = None


# ── Proposal schemas ──────────────────────────────────────────────────

class GapEvidenceOut(BaseModel):
    """Gap evidence from autopilot heuristic detection."""
    signal: str
    description: str
    metric_value: float
    threshold: float
    neuron_ids: list[int] = []
    query_ids: list[int] = []


class DocumentEvidenceOut(BaseModel):
    """Gap evidence from document ingestion."""
    source: str
    document: str
    section: str
    section_id: str
    job_id: str


class ProposalItemOut(BaseModel):
    id: int
    action: str
    target_neuron_id: int | None = None
    field: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    neuron_spec_json: str | None = None
    reason: str | None = None
    created_neuron_id: int | None = None
    refinement_id: int | None = None


class ProposalOut(BaseModel):
    id: int
    autopilot_run_id: int | None = None
    query_id: int | None = None
    state: str
    gap_source: str | None = None
    gap_description: str | None = None
    priority_score: float = 0.0
    llm_model: str | None = None
    eval_overall: int = 0
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    applied_at: str | None = None
    applied_by: str | None = None
    item_count: int = 0
    origin: str = "manual"  # autopilot | integrity | document | manual
    is_autopilot: bool = False
    created_at: str | None = None
    # Deep-link identifiers extracted from gap_evidence_json at summary time
    # so the UI can render "from Integrity finding #N" without re-parsing the
    # evidence JSON client-side. Populated only when the relevant origin
    # carries a server-side identifier; None otherwise.
    finding_id: int | None = None
    scan_id: int | None = None


class ProposalDetailOut(BaseModel):
    id: int
    autopilot_run_id: int | None = None
    query_id: int | None = None
    state: str
    gap_source: str | None = None
    gap_description: str | None = None
    gap_evidence: list[GapEvidenceOut | DocumentEvidenceOut | dict] = []
    priority_score: float = 0.0
    llm_reasoning: str | None = None
    llm_model: str | None = None
    prompt_hash: str | None = None
    eval_overall: int = 0
    eval_text: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_notes: str | None = None
    applied_at: str | None = None
    applied_by: str | None = None
    items: list[ProposalItemOut] = []
    created_at: str | None = None
    updated_at: str | None = None


class ProposalReviewRequest(BaseModel):
    action: str = Field(..., pattern="^(approve|reject)$")
    # Advisory only — server uses resolved auth identity for the audit trail.
    # Kept optional for backward compat with older clients.
    reviewer: str = Field(default="", max_length=100)
    notes: str = ""


class ProposalApplyRequest(BaseModel):
    # Advisory only — server uses resolved auth identity.
    applied_by: str = Field(default="", max_length=100)


class ProposalStatsOut(BaseModel):
    proposed: int = 0
    approved: int = 0
    rejected: int = 0
    applied: int = 0
    superseded: int = 0  # terminal: recorded old-state drifted (kernel Phase 4)
    total: int = 0
    # Pending-proposal counts grouped by producer origin. Keys match the
    # values returned by _classify_origin: autopilot | integrity | document |
    # manual. Used by the frontend to render per-producer nav badges.
    proposed_by_origin: dict[str, int] = Field(default_factory=dict)


class ObservationEvalRequest(BaseModel):
    model: str = Field("haiku", min_length=1)


class ObservationBatchEvalRequest(BaseModel):
    observation_ids: list[int] = Field(..., max_length=20)
    model: str = Field("haiku", min_length=1)


class ObservationApplyRequest(BaseModel):
    update_indices: list[int] = []
    new_neuron_indices: list[int] = []


# ── GTM-A external /v1/query contract ─────────────────────────────────

class V1QueryRequest(BaseModel):
    """Request DTO for the hardened external ``POST /v1/query`` endpoint."""

    message: str = Field(..., min_length=1, max_length=5000)
    mode: str = Field("compact", pattern="^(compact|full)$")
    token_budget: int = Field(8000, ge=1000, le=32000)
    top_k: int = Field(60, ge=1, le=500)


class V1ContextFragment(BaseModel):
    """One retrieved neuron as exposed to an external LLM frontend."""

    neuron_id: int
    label: str
    snippet: str
    source: str | None = None  # department / role_key hint
    combined_score: float = 0.0


class V1QueryResponse(BaseModel):
    """Formal contract returned by ``POST /v1/query`` (compact or full).

    ``fragments`` is populated only when ``mode='full'``. ``lineage_id``
    is the underlying ``Query.id`` — every ``NeuronFiring`` with this
    ``query_id`` reconstructs the full provenance trail. ``eval_run_id``
    is reserved for Pattern #3 and is ``None`` today.
    """

    answer: str
    fragment_labels: list[str] = []
    fragments: list[V1ContextFragment] = []
    lineage_id: int
    eval_run_id: int | None = None
    blocked: bool = False
    violations: list[OutputViolationOut] = []


# ── AIP Phase 3 — Query Dossier ──────────────────────────────────────────
#
# Live-projection aggregate of the five per-query governance/measurement
# signals: pipeline telemetry, eval (ad-hoc + eval-run participations),
# output-guard violations, action audit trail, integrity findings.
# On-read aggregation only; no new table. See
# `docs/design/aip-phase-3-query-dossier.md` for scope + caveats.

class DossierActionOut(BaseModel):
    """One Action audit-trail row scoped to a specific query."""
    id: int
    kind: str
    actor_type: str              # user | autopilot | system | external_agent
    actor_id: str | None = None
    state: str                   # pending | applied | rejected | failed
    requires_approval: bool = False
    reason: str | None = None
    parent_action_id: int | None = None
    applied_at: str | None = None
    error_message: str | None = None
    created_at: str | None = None


class DossierOutputViolationOut(BaseModel):
    """One OutputViolation row for this query, with linkage to its triggering Action."""
    id: int
    rule_id: str
    severity: str                # info | warn | error | critical
    action: str                  # flag | redact | block
    matched_span: str | None = None
    redaction: str | None = None
    detail: dict | None = None
    action_id: int | None = None
    created_at: str | None = None


class DossierEvalRunParticipation(BaseModel):
    """Indicates an EvalRun included this query as one of its cases."""
    eval_run_id: int
    eval_run_case_id: int
    case_label: str
    suite_name: str
    suite_hash: str
    certified: bool              # == tenant_config.certified_eval_run_id
    blocked: bool = False
    scores_json: dict | None = None
    violations_json: dict | None = None
    run_status: str
    run_started_at: str | None = None
    run_completed_at: str | None = None


class DossierIntegrityFindingOut(BaseModel):
    """An IntegrityFinding attributed to this query via neuron-id overlap."""
    id: int
    scan_id: int
    finding_type: str
    severity: str
    priority_score: float
    description: str | None = None
    status: str                  # open | resolved | dismissed
    resolution: str | None = None
    attributed_via: str          # "selected_neurons"
    overlapping_neuron_ids: list[int] = []
    created_at: str | None = None


class QueryDossierPipelineSection(BaseModel):
    """Per-stage pipeline telemetry (from queries.stage_telemetry_json)."""
    stage_telemetry: list[dict] = []


class QueryDossierEvalSection(BaseModel):
    """Eval-level signals: ad-hoc scores + eval-run participations."""
    ad_hoc_scores: list[EvalScoreOut] = []
    eval_run_participations: list[DossierEvalRunParticipation] = []


class QueryDossierOutputSection(BaseModel):
    """Output-guard violations (Pattern #7) fired against this query."""
    violations: list[DossierOutputViolationOut] = []


class QueryDossierActionsSection(BaseModel):
    """Full Action audit trail with source_query_id == this query."""
    actions: list[DossierActionOut] = []


class QueryDossierIntegritySection(BaseModel):
    """IntegrityFindings whose neuron_ids overlap with this query's selected neurons."""
    findings: list[DossierIntegrityFindingOut] = []


class QueryDossier(BaseModel):
    """AIP Phase 3 — unified per-query reviewable artifact."""
    query_id: int
    user_message: str
    created_at: str | None = None
    pipeline: QueryDossierPipelineSection
    eval: QueryDossierEvalSection
    output_checks: QueryDossierOutputSection
    actions: QueryDossierActionsSection
    integrity: QueryDossierIntegritySection


# ── Tier C: Follow-up question suggestions ──────────────────────────────

class FollowUpSuggestion(BaseModel):
    """One suggested next question derived from a completed query + answer."""
    text: str


class FollowUpSuggestionsResponse(BaseModel):
    """2-3 follow-up questions generated from a completed query.

    Produced by a post-hoc Haiku call; cached on the Query row so the
    same question doesn't re-trigger LLM work on page refresh.
    """
    query_id: int
    suggestions: list[FollowUpSuggestion]
    cost_usd: float = 0.0
