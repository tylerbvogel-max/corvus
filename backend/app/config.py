from pydantic_settings import BaseSettings


def _default_database_url() -> str:
    """Derive database URL from TENANT_ID. DB name = tenant ID with hyphens as underscores."""
    import os
    tid = os.environ.get("TENANT_ID", "corvus-mind")
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
    # Model alias map (JSON). Codex is the default primary provider as of the
    # 2026-07 provider swap-over; Anthropic remains the same-grade fallback
    # while available. This is the single rollback dial: set
    # LLM_MODEL_ALIASES={} to restore direct Anthropic routing, or replace it
    # with an environment-specific map (for example GovCloud Azure).
    llm_model_aliases: str = (
        '{"haiku":"codex-luna","sonnet":"codex-terra","opus":"codex-sol"}'
    )
    # RBAC (disabled by default for backward compat)
    rbac_mode: str = "disabled"  # "disabled" | "header" | "azure_ad"
    rbac_azure_tenant_id: str = ""
    rbac_admin_claim: str = "corvus-admin"
    rbac_reviewer_claim: str = "corvus-reviewer"
    # GTM-A external endpoint rate limit (per-tenant,user token bucket)
    v1_rate_limit_capacity: int = 30      # burst allowance
    v1_rate_limit_refill_per_sec: float = 0.5  # sustained rate (~30 req/min)
    port: int = 8005
    tenant_id: str = "corvus-mind"
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
    # ── Tier-elastic escalation routing (roadmap arch-tier-routing) ──
    # Single-slot queries default to haiku and escalate the primary slot to
    # tier_routing_escalation_model BEFORE execution when prep-time uncertainty
    # signals fire (see services/tier_routing.py). Never downgrades; skipped
    # for multi-slot compares (A/B integrity) and audit-grade slots. A set
    # primary_answer_model wins over routing (explicit beats adaptive).
    tier_routing_enabled: bool = True
    tier_routing_escalation_model: str = "sonnet"
    # Thresholds calibrated 2026-07-09 over 63 live corvus-aero queries + the
    # smoke suite (union escalation rate ≈ 29%); distributions documented in
    # services/tier_routing.py and scripts/eval_tier_routing.py.
    # Coverage floor: escalate when the mean stimulus relevance of the packed
    # slice's top 5 is below this (live p25 ≈ 0.84; direct hits sit 0.95+).
    tier_routing_coverage_floor: float = 0.82
    # Spread density: escalate when at least this fraction of the packed slice
    # arrived via spreading activation (hop-heavy pack = synthesis burden;
    # live p90 = 0.30).
    tier_routing_spread_share: float = 0.30
    # Regulatory stakes: an explicit regulatory citation in the query text
    # (tenant reference patterns) escalates directly.
    tier_routing_regulatory_escalates: bool = True
    # ── Tier-1 compliance: query-telemetry retention (FedRAMP AU-11) ──
    # Days to keep Query rows + per-query telemetry. 0 = keep forever
    # (default: purging history is a tenant-policy decision, not a default).
    # Audit-bearing artifacts (actions, output_violations, proposals,
    # eval_run_cases) are detached, never deleted — see services/retention.py.
    query_retention_days: int = 0
    retention_purge_batch: int = 500  # queries deleted per batch (bounded pass)
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
    # ── Token-bounded memory assembly (mind-token-bounded-neuron-assembly) ──
    # Provisional constants from one fixed-corpus LoCoMo conv0 sweep
    # (2026-07-16). Recalibrate after the repeated confirmation triangle.
    # This is a MEMORY-ONLY ceiling, independent from answer max output tokens
    # and independently observable from the whole-prompt token_budget above.
    memory_context_token_budget: int = 3000
    memory_candidate_limit: int = 150
    # Pathology/estimator guard only; normal delivery is governed by tokens.
    memory_max_delivered_neurons: int = 250
    memory_token_estimator: str = "utf8_bytes_div4_ceil_v1"
    # Conservative rollback default. The corvus-mind deployment enables this
    # only after the fixed-corpus confirmation gates pass.
    token_bounded_assembly_enabled: bool = False
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
    # Modulatory scale on the spread boost before it is added to `combined`.
    # Spread activation is source_score * edge_weight * decay, and it was being
    # added RAW — so once edge weights carried real dynamic range (up to 0.80),
    # a well-connected neighbor could gain ~0.6, rivaling the entire relevance
    # stimulus (0.70) and outranking the direct semantic match. Graph proximity
    # is evidence, not an answer: it should nudge ranking, never rewrite it.
    # Default 1.0 preserves legacy behavior for knowledge tenants; the memory
    # tenant sets this well below 1 (see deploy/corvus-mind.service).
    weight_spread_boost: float = 1.0
    spread_max_neurons: int = 10
    spread_min_edge_weight: float = 0.15
    spread_decay: float = 0.5
    spread_min_activation: float = 0.15
    spread_max_hops: int = 3  # manual fallback when spread_hops_auto is off / cache unloaded
    # Derive the hop cap from graph structure (ceil(log N / log avg-degree))
    # instead of the fixed spread_max_hops. Per-slot spread_hops overrides
    # always win over both.
    spread_hops_auto: bool = True
    # Genesis exuberance: while the corpus is young, loosen the spread gates
    # so a sparse graph can still propagate (synaptic overproduction; decay
    # janitors prune later). Spread thresholds multiply by
    # ln(N_active) / ln(genesis_mature_corpus), clamped to
    # [genesis_floor, 1.0] — liberal at genesis, converging to the
    # configured values as the corpus matures. No cliff, no cron.
    # Dedup is destructive (deactivate + supersede) and not trivially
    # reversible, so the consolidation janitor PROPOSES merges and the human
    # countersigns. Set false to restore silent auto-fusing (not recommended:
    # memory integrity outranks janitor throughput).
    mind_dedup_requires_approval: bool = True
    # Durable memories must be written as evidence frames
    # (mind-neuron-evidence-frame); malformed construction fails closed at
    # the Action Bus. This switch exists to produce the LoCoMo control arm
    # the record requires — old-style vs framed construction on a fixed
    # subset. It is NOT an operator convenience: turning it off on a real
    # tenant reopens the unstructured-content failure mode Step 06 traced.
    mind_evidence_frame_enforced: bool = True
    genesis_mode: bool = True
    genesis_mature_corpus: int = 2000  # guessed constant — revisit with data
    genesis_floor: float = 0.5  # never loosen a gate below 50% of configured
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
    # Persisted-chat CLI transcripts (~/.claude/projects/-tmp) are pruned at
    # startup when older than this. 0 disables pruning.
    chat_session_transcript_ttl_days: int = 14
    # asyncpg connection pool (was NullPool — see database.py)
    db_pool_size: int = 10
    db_max_overflow: int = 10
    # Layer-2 citation grounding: embedding-cosine relevance of each cited
    # claim vs its cited sources. LOG-ONLY (advisory calibration data on every
    # response + queries.citation_relevance_json); no flagging threshold until
    # the real-usage distribution is measured.
    citation_relevance_enabled: bool = True
    citation_relevance_max_claims: int = 10
    # Three-band routing, calibrated 2026-07 (131 true claims vs 126
    # shuffle-negatives): below the floor almost no true citations exist
    # (lowest verified-true prose: 0.263) -> advisory flag; above the pass
    # threshold only 4% of wrong-source pairs survive -> pass; the band
    # between routes to the entailment judge (~25-30% of claims), which is
    # what lets entailment run default-on at a fraction of always-on cost.
    citation_relevance_flag_floor: float = 0.25
    citation_relevance_pass_threshold: float = 0.45
    citation_relevance_escalate: bool = True
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
    # Hybrid-recall retrieval lanes (mind-hybrid-recall, 2026-07-14): tsvector
    # keyword lane + write-time-entity lane fused with the embedding lane by
    # RRF. Enabled after the fixed-corpus LoCoMo A/B (2026-07-15): hybrid +
    # strict refusal scored 69.9% overall / 89.4% adversarial versus the
    # embed-only arm's 63.8% / 78.7%; warm p95 recall remained 84.9 ms.
    keyword_lane_enabled: bool = True
    entity_lane_enabled: bool = True
    recall_lane_top_n: int = 50
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
    # 0.45 lets ~21% of stellate edges conduct at a median-scoring seed
    # (0.3 left them nearly inert at ~4%), while staying below pyramidal
    # decay so co-fire evidence still outranks similarity wiring.
    spread_stellate_decay: float = 0.45
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

    # Reconsolidation auditor (mind-reconsolidation-auditor): scheduled doubt.
    # ALL numeric values below are PROVISIONAL guessed constants — the node
    # mandate is to report observed candidate volume/precision per run and
    # recalibrate from data. Cadence is evidence time (distilled sessions
    # since the last run), never wall time.
    auditor_light_pass_sessions: int = 5     # light pass at >= N fresh sessions
    auditor_deep_pass_sessions: int = 50     # deep pass at >= N fresh sessions
    auditor_max_candidates_per_run: int = 40      # scored candidates kept
    auditor_max_critic_calls_light: int = 4       # bounded critic batch (light)
    auditor_max_critic_calls_deep: int = 16       # bounded critic batch (deep)
    auditor_max_proposals_per_run: int = 10
    auditor_control_sample_size: int = 3     # healthy controls per deep run
    # Deep runs also probe N sub-threshold neurons (rotated by run date):
    # subtle semantic defects fire NO deterministic signal — the manual
    # sweep's Pareto-probe insight, made recurring. Golden replay
    # 2026-07-18 measured 0.0 deterministic recall on enrich-class
    # defects; only probing gets them in front of the critic.
    auditor_deep_probe_sample: int = 6
    # Hard cap 2: concurrent `claude -p` subprocesses OOM this 6.5GB machine
    # at 3+ (silent SIGKILL, zero output) — see env note; do not raise.
    auditor_critic_concurrency: int = 2
    auditor_risk_threshold: float = 0.35     # min risk score to reach critic

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    def model_post_init(self, __context) -> None:
        """Fill database_url from tenant ID if not explicitly provided."""
        if not self.database_url:
            object.__setattr__(self, "database_url", _default_database_url())


settings = Settings()
