from pydantic_settings import BaseSettings


def _default_database_url() -> str:
    """Derive database URL from TENANT_ID. DB name = tenant ID with hyphens as underscores."""
    import os
    tid = os.environ.get("TENANT_ID", "corvus-aero")
    db_name = tid.replace("-", "_")
    return f"postgresql+asyncpg://yggdrasil:yggdrasil@localhost:5432/{db_name}"


class Settings(BaseSettings):
    database_url: str = ""  # Auto-derived from TENANT_ID if not set
    anthropic_api_key: str = ""
    google_api_key: str = ""
    groq_api_key: str = ""
    # Azure OpenAI (GovCloud deployment)
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""  # e.g. https://<resource>.openai.azure.com/
    azure_openai_api_version: str = "2024-10-21"
    azure_openai_deployment_gpt4o: str = ""
    azure_openai_deployment_gpt4o_mini: str = ""
    azure_openai_deployment_o1: str = ""
    # Model alias map (JSON): e.g. {"haiku":"azure-gpt4o-mini","sonnet":"azure-gpt4o"}
    llm_model_aliases: str = ""
    # RBAC (disabled by default for backward compat)
    rbac_mode: str = "disabled"  # "disabled" | "header" | "azure_ad"
    rbac_azure_tenant_id: str = ""
    rbac_admin_claim: str = "corvus-admin"
    rbac_reviewer_claim: str = "corvus-reviewer"
    # GTM-A external endpoint rate limit (per-tenant,user token bucket)
    v1_rate_limit_capacity: int = 30      # burst allowance
    v1_rate_limit_refill_per_sec: float = 0.5  # sustained rate (~30 req/min)
    port: int = 8002
    tenant_id: str = "corvus-aero"
    cors_origins: str = ""  # Comma-separated; empty = auto from port
    haiku_model: str = "claude-haiku-4-5-20251001"
    # Reasoning effort for Claude CLI calls (low|medium|high). Default low: the
    # answer LLM's extended-thinking is the dominant query latency, and most
    # answers don't need deep deliberation. Per-request overridable via the UI.
    default_effort: str = "low"
    # Primary-answer quality floor (grounding backlog §6.5). Slot 0 is the
    # primary answer — the one persisted to query.response_text and audited by
    # the citation-exit layer. Weak models at low effort invent references more
    # freely, so the primary may run at higher effort and/or a stronger model
    # while compare slots stay cheap.
    # primary_answer_effort: minimum effort for the primary slot ("" = inherit
    # the request effort unchanged). Acts as a FLOOR — never lowers an
    # explicitly higher per-request effort.
    primary_answer_effort: str = "medium"
    # primary_answer_model: MODEL_REGISTRY key to swap the primary slot's model
    # to ("" = keep the slot's own model). Invalid keys are ignored.
    primary_answer_model: str = ""
    # Entailment / claim-grounding check (grounding backlog §6.4). A valid [FQ]
    # key proves the source was in context, NOT that the claim is entailed by
    # it. This opt-in pass judges each cited claim of the PRIMARY answer
    # against its cited source content — one extra batched LLM call per query,
    # advisory only (never blocks or mutates the answer).
    entailment_check_enabled: bool = False
    entailment_check_model: str = "haiku"
    entailment_max_claims: int = 10       # cap on judged claims per answer
    entailment_source_chars: int = 1500   # per-source excerpt cap in the judge prompt
    token_budget: int = 8000
    propagation_decay: float = 0.6
    top_k_neurons: int = 60
    # Scoring weights (6 signals, sum = 1.0)
    # Relevance = stimulus specificity (primary driver)
    # Impact = long-term potentiation (proven utility)
    # Burst/Recency = modulatory signals (priming/attention)
    # Precision/Novelty = contextual modifiers
    weight_burst: float = 0.08
    weight_impact: float = 0.15
    weight_precision: float = 0.07
    weight_novelty: float = 0.05
    weight_recency: float = 0.15
    weight_relevance: float = 0.50
    # Relevance gating: modulatory signals attenuated without stimulus
    # relevance_gate_threshold: relevance level for full modulation (soft gate)
    # relevance_gate_floor: minimum gate factor (spontaneous background rate)
    relevance_gate_threshold: float = 0.3
    relevance_gate_floor: float = 0.05
    # Scoring parameters (query-count based)
    burst_window_queries: int = 50
    neuron_index_enabled: bool = True  # serve scoring inputs from the in-memory NeuronIndex
    burst_threshold: int = 15
    novelty_halflife_queries: int = 200
    recency_decay_queries: int = 500
    impact_ema_alpha: float = 0.3
    # Diversity floor: minimum neurons per cross-referenced department
    diversity_floor_min: int = 2
    # Spreading activation via NeuronEdge graph
    spread_enabled: bool = True
    spread_max_neurons: int = 10
    spread_min_edge_weight: float = 0.15
    spread_decay: float = 0.5
    spread_min_activation: float = 0.15
    spread_max_hops: int = 3  # manual fallback when spread_hops_auto is off / cache unloaded
    # Derive the hop cap from graph structure (ceil(log N / log avg-degree))
    # instead of the fixed spread_max_hops. Per-slot spread_hops overrides
    # always win over both.
    spread_hops_auto: bool = True
    # Warm BERT + semantic/adjacency caches in lifespan so the first query
    # after a restart doesn't pay the 17-39s lazy-load chain. Set false for
    # fast dev-reload cycles.
    preload_on_startup: bool = True
    # Honor persist_session requests (hero chat CLI session persistence —
    # conversation prefix served from the prompt cache). Kill switch.
    chat_session_persistence: bool = True
    # Drift-gated recall for persisted sessions: skip re-sending the packed
    # context block when the fresh pack overlaps the session's active block
    # by at least this fraction (near-duplicate). Conservative default —
    # measured same-topic follow-ups overlap 0.28-0.47, so 0.6 fires only on
    # repeats/rephrasings; lower after calibration to reuse more aggressively.
    chat_context_drift_gate: bool = True
    chat_context_reuse_overlap: float = 0.6
    # Layer-2 citation grounding: embedding-cosine relevance of each cited
    # claim vs its cited sources. LOG-ONLY (advisory calibration data on every
    # response + queries.citation_relevance_json); no flagging threshold until
    # the real-usage distribution is measured.
    citation_relevance_enabled: bool = True
    citation_relevance_max_claims: int = 10
    spread_vectorized: bool = True  # numpy scatter-max spread (equivalent to the BFS reference)
    # Candidate selection limits
    candidate_limit: int = 500
    # Co-firing edge management
    edge_promote_min_weight: float = 0.10
    edge_promote_min_cofires: int = 2
    min_cofire_score: float = 0.3
    edge_prune_min_cofires: int = 2
    edge_prune_stale_queries: int = 100
    # Query-prep classification is embed-only (neighbor-vote). The per-query LLM
    # classify (the old full/adaptive recall modes) was DELETED (2026-07): ~18s of
    # variable extended-thinking, occasional empty output, and the nested-session
    # failure mode, for a task the free neighbor-vote handles as well or better.
    # recall_mode is retained for forward-compat but only "cheap" is registered.
    recall_mode: str = "cheap"
    cheap_recall_neighbor_k: int = 8
    # Semantic pre-filter (replaces org-chart filtering)
    semantic_prefilter_enabled: bool = True
    semantic_prefilter_top_n: int = 100_000
    semantic_prefilter_min_similarity: float = 0.10
    # Hybrid search: fuse keyword + semantic relevance via Reciprocal Rank Fusion
    hybrid_relevance_enabled: bool = True
    rrf_k: int = 60
    # Impact analysis blast radius (graph traversal)
    impact_max_hops: int = 3
    impact_min_edge_weight: float = 0.15
    impact_seed_count: int = 5
    # Inhibitory regulation (replaces diversity floor)
    inhibition_enabled: bool = True
    inhibition_default_threshold: int = 15
    inhibition_default_max_survivors: int = 8
    inhibition_redundancy_cosine: float = 0.92
    inhibition_learning_alpha: float = 0.2
    # Typed edge spread thresholds
    spread_stellate_decay: float = 0.3
    spread_pyramidal_min_weight: float = 0.20
    # Concept neuron (instantiation edge) spread thresholds
    spread_instantiate_decay: float = 0.6
    spread_instantiate_min_weight: float = 0.10
    concept_activation_boost: float = 1.3
    # Cold-start prior (substrate/ontology split): authority + freshness +
    # centrality stand in for usage signals until firing history accrues.
    # weight_coldstart_prior is the modulatory scale of the (prior - 0.5)
    # term; component weights below must sum to 1.0.
    weight_coldstart_prior: float = 0.15
    coldstart_prior_strength: float = 10.0  # shrinkage: strength/(strength+invocations)
    coldstart_freshness_halflife_days: float = 365.0
    coldstart_authority_weight: float = 0.5
    coldstart_freshness_weight: float = 0.3
    coldstart_centrality_weight: float = 0.2
    # Consolidation (decay/prune/deactivate) — formerly module constants
    consolidation_retention_queries: int = 2000
    consolidation_decay_rate: float = 0.95
    consolidation_deactivation_threshold: float = 0.05
    # Consolidation rides the autopilot tick heartbeat at most this often
    consolidation_interval_hours: float = 24.0
    # Hierarchy-aware selection: include ancestor chains so graph shows trees
    hierarchy_selection_enabled: bool = True
    # Per-project neuron subgraph caching
    project_cache_enabled: bool = True
    project_cache_boost_max: float = 1.3
    project_cache_min_queries: int = 3
    # Session and security headers (AC-12, SC-10, CMMC 3.1.11/3.13.9)
    session_timeout_minutes: int = 30
    # System use notification banner (AC-8, CMMC 3.1.9)
    # Banner text is loaded from tenant config (tenant.system_use_banner)
    system_use_banner_enabled: bool = True
    # Synaptic learning (eval-driven automatic weight adjustment)
    synaptic_learning_enabled: bool = True
    outcome_learning_alpha: float = 0.05
    outcome_loss_penalty: float = 0.3
    outcome_win_cofire_multiplier: int = 2
    outcome_loss_cofire_multiplier: int = 0
    # Engram / regulatory resolve settings
    engram_resolve_enabled: bool = True
    engram_cache_ttl_hours: int = 24
    engram_max_concurrent_fetches: int = 5
    engram_token_budget_fraction: float = 0.25
    # Engram<->neuron association (EngramEdge). Recording grows the edges when
    # regulations fire alongside neurons (fixes a gap — cofiring was never
    # recorded); the boost lets a neuron pull in the regulations it co-fires
    # with. Recording is cheap (one batched upsert); the boost is a scoring
    # change, so it is opt-in (default off) pending eval.
    engram_cofire_recording_enabled: bool = True
    engram_cofire_max_neurons: int = 15
    engram_edge_boost_enabled: bool = False
    engram_edge_boost_scale: float = 0.3
    # Regulatory coverage: queue CFR refs cited in an answer but not resolved
    # from an engram this query (un-grounded authority) into EmergentQueue for
    # review/engram creation. Observational — nothing auto-applies.
    regulatory_coverage_detection_enabled: bool = True
    # Graph integrity (neurological self-correcting processes)
    integrity_homeostasis_default_scale: float = 0.8
    integrity_homeostasis_floor_threshold: float = 0.05
    integrity_duplicate_threshold: float = 0.92
    integrity_completion_threshold: float = 0.65
    integrity_conflict_sim_min: float = 0.60
    integrity_conflict_sim_max: float = 0.85
    integrity_aging_regulatory_days: int = 1095
    integrity_aging_operational_days: int = 548
    integrity_aging_default_days: int = 730
    integrity_max_scan_neurons: int = 10_000
    # Horizontal reconciler (cross-region loop) — plat-reconciler
    # Judge model default is opus: sweeps are rare and bounded; judgment
    # quality on contradictions/homonyms matters more than per-call cost.
    reconciler_judge_model: str = "opus"
    reconciler_max_pairs: int = 12
    reconciler_staleness_divergence_days: int = 180
    reconciler_homonym_sim_threshold: float = 0.80
    # 0 = manual only; > 0 = the sweep rides the tick heartbeat at this cadence
    reconciler_interval_hours: float = 0.0
    # Frequency-hopped citation grounding (anti-hallucination exit layer).
    # When enabled, each neuron in the assembled prompt gets a random per-query
    # ephemeral key; the exit layer verifies the answer cited only real keys.
    # ON by default: citations render as [FQ-XXXXXX] (per-query ephemeral) rather
    # than numeric [N], so any fabricated neuron/regulation reference is caught
    # deterministically. The frontend resolves hop tokens to clean numbered
    # superscripts via the /query response citation_map.
    citation_hopping_enabled: bool = True
    # Prefix must NOT collide with domain tokens (e.g. aircraft F-16/F-35), so
    # the default is FQ- (frequency) rather than the F- from the design note.
    citation_hop_prefix: str = "FQ-"
    citation_hop_hex_width: int = 6
    # detect (record only) | strip (remove fabricated keys from the answer) |
    # repair (one bounded LLM retry with the valid-key list, then strip).
    # Applied PER-SLOT (every compare slot, not just the primary answer), so a
    # weaker model can't slip fabricated citations through the Query Lab grid.
    citation_hop_failure_mode: str = "strip"
    # require_all: also fail when an allowed key is never cited (allowed ⊆ used).
    # Off by default — forcing every source to appear hurts open-ended answers.
    citation_hop_require_all: bool = False

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    def model_post_init(self, __context) -> None:
        """Fill database_url from tenant ID if not explicitly provided."""
        if not self.database_url:
            object.__setattr__(self, "database_url", _default_database_url())


settings = Settings()
