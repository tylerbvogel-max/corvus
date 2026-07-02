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
    spread_max_hops: int = 3
    # Candidate selection limits
    candidate_limit: int = 500
    # Co-firing edge management
    edge_promote_min_weight: float = 0.10
    edge_promote_min_cofires: int = 2
    min_cofire_score: float = 0.3
    edge_prune_min_cofires: int = 2
    edge_prune_stale_queries: int = 100
    # Recall mode for the query-prep pipeline (plat-cheap-recall):
    #   full     - LLM classify on every read (legacy default; HTTP/UI path)
    #   cheap    - embed-only, zero LLM: tokenizer keywords + neighbor-vote regions
    #   adaptive - cheap first, escalate to LLM classify when the top semantic
    #              neighbor similarity is below the confidence threshold
    # The MCP query_graph tool defaults to adaptive (the seamless agent layer).
    recall_mode: str = "full"
    cheap_recall_confidence_threshold: float = 0.35
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
    engram_fallback_on_api_failure: bool = True
    engram_haiku_extract_threshold: int = 2000
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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    def model_post_init(self, __context) -> None:
        """Fill database_url from tenant ID if not explicitly provided."""
        if not self.database_url:
            object.__setattr__(self, "database_url", _default_database_url())


settings = Settings()
