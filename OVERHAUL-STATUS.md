# Corvus Overhaul — Status & Design Decisions

> Working log for the §9 Generic-Org & Matrix Memory overhaul (OVERHAUL-BRIEF.md).
> Fable 5 session started 2026-07-01. Baseline: 290 tests pass, main @ fc3ff3b.

## Progress

| Workstream | Status | Commit |
|---|---|---|
| Mapping + design decisions | done | — |
| W1 plat-substrate-ontology | done | (see git log) |
| W2a plat-cheap-recall | done | (see git log) |
| W2b plat-write-gate | pending | — |
| W3 plat-region-config | pending | — |
| W4 plat-reconciler | pending | — |
| Final verification + docs | pending | — |

## Design decisions (resolving the brief's open questions)

### D1 — Region dimension: SQLAlchemy synonym, no column rename
`Neuron.department` stays as the physical column (indexed, populated, used by raw SQL,
frontend, agents). New engine code speaks `region` via `Neuron.region = synonym("department")`.
`InhibitoryRegulator.region_type/region_value` already uses region vocabulary. No data
migration, no drift risk, and the product story ("silos = labeled regions") lives at the
API/UI layer where it matters. A hard rename is deferred until a real second-org deployment
forces it.

### D2 — Abstraction axis: new `abstraction_type` column; `layer` demoted to projection depth
Do NOT repurpose numeric `layer` (it feeds propagation decay, consolidation, gap heuristics,
ingest validation, tree/stats/UI simultaneously — repurposing breaks all consumers at once).
Add `abstraction_type: String(20)` with six values:

| value | meaning | backfill from node_type |
|---|---|---|
| `structural` | navigational container, not knowledge | department, role |
| `concept` | cross-cutting definitional anchor | concept |
| `principle` | the why — policy, decision rationale | decision |
| `process` | the flow | task |
| `procedure` | the how | system |
| `artifact` | the evidence — records, outputs | output |

Engine predicates migrate from `layer >= 2` to `abstraction_type IN (principle, process,
procedure, artifact)` with layer fallback when NULL. `layer` survives as depth-in-projection
metadata. Backfill mapping lives in `app/models.py` as `ABSTRACTION_BY_NODE_TYPE`.

### D3 — Cold-start prior: Bayesian shrinkage on invocations, gated like other modulatory signals
`prior = w_auth*authority + w_fresh*freshness + w_central*centrality` (defaults 0.5/0.3/0.2)
- authority from `authority_level` (binding_standard=1.0 … informational=0.3, None=0.4)
- freshness = exp(-age_days/halflife), age from COALESCE(last_verified, effective_date, created_at)
- centrality = normalized degree, denormalized onto `Neuron.centrality`, refreshed at consolidation
Blend: `combined = stimulus + (modulatory + w_coldstart * (prior - 0.5) * shrink) * gate`
where `shrink = prior_strength / (prior_strength + invocations)` (prior_strength default 10).
Story: authority+freshness+centrality is the prior; firing history is the posterior. The term
is relevance-gated like every other modulatory signal (no stimulus → no boost).

### D4 — Cheap recall: neighbor-vote classification, adaptive escalation
`recall_mode ∈ {cheap, full, adaptive}`. Cheap = embed locally + tokenizer keywords +
region/role via similarity-weighted vote over top-k semantic neighbors + neutral intent.
Adaptive = cheap first; if top-1 neighbor cosine < `cheap_recall_confidence_threshold`
(default 0.35) fall through to LLM classify. Defaults: HTTP/API keeps `full` (no behavior
change for existing UI/evals); MCP `query_graph` defaults `adaptive` (the seamless layer).

### D5 — Write gate: auto-approve through the EXISTING proposal-apply path, not a second write path
Proposals remain the universal record. The gate decides route at persistence time:
`evaluate_write(write_class, authority_level, guardrail_result, confidence, policy)` →
auto | queue. Auto = the proposal is approved by `write_gate:v1` and applied immediately
via the same Action Bus `proposal.apply` action (full audit, NeuronRefinement reversibility,
provenance). Queue = current human queue. Policy in tenant.yaml `write_gate:` block,
per-region overridable via RegionPolicy (W3). Decay/consolidation reclaims unreinforced
auto-commits (already exists).

### D6 — Region config: RegionPolicy table over one shared graph
`RegionPolicy(region unique, scoring_weights JSONB, loop_config JSONB, acl JSONB,
projection JSONB, write_gate JSONB)`. Seeded from tenant dir `regions.yaml` (optional).
Scoring: per-region weight arrays in the vectorized scorer (global-settings fast path
when no policies exist). ACL: `Neuron.visibility` (nullable; falls back to region policy
default) + `RequesterContext(regions, privileged)` filtering at candidate load, spread
promotion, and assembly. `requester=None` = full access (local single-user back-compat).
AutopilotConfig gains nullable `region` column; the tick iterates per-region configs.

### D7 — Reconciler: specialized scans in the EXISTING integrity framework
Cross-region detection lands as new integrity scan types (reuses IntegrityScan /
IntegrityFinding / proposal conversion / Action Bus audit):
1. `cross_region_contradiction` — embedding-similar pairs in different regions, bounded
   LLM judge verdict {is_contradiction, severity, rationale} (model: opus by default,
   configurable — rare, quality-first).
2. `staleness_divergence` — pyramidal-edge endpoints whose last_verified diverge.
3. `homonym_synonym` — cross-region near-duplicates: judge distinguishes same-concept
   (propose link/merge) from false-friend (propose namespace/keep-separate).
4. `seam_gap` — Leiden clusters spanning regions with no shared coordination neuron.
Reconciler runs with privileged internal read; outputs are findings/proposals routed to the
owning region; never auto-edits authoritative content (obeys write gate).

## Fresh-look findings (beyond the brief)

1. **BUG (fixed in W1): `mcp_server.impact_analysis` crashes on results** — unpacks
   semantic_prefilter 3-tuples `(id, type, sim)` as 2-tuples. Broken since engrams were
   added to the prefilter.
2. **BUG (fixed in W1): organic co-fire edges hardcode `edge_type='pyramidal'`**
   (`executor._cofire_weak`, `_batch_update_edges`) — the stellate/pyramidal distinction
   only exists for bootstrap edges today. Spread decay is therefore wrong for
   intra-region organic edges (0.5 instead of 0.3). Fix: derive from region equality at
   write time.
3. `consolidation.py` constants are module-level, not settings — lifted into settings so
   per-region loop config (W3) can own them.
4. `prompt_assembler.CLOSING_INSTRUCTION_MAP` is aero-flavored domain content inside
   service code (violates the tenant convention). Moved to tenant config with generic
   fallback. (deferred if time-boxed)
5. `bootstrap_service.DEPT_AFFINITY` is aero-specific data inside service code — same
   violation. Referenced for W1 seeding work. (deferred if time-boxed)
6. ~99% of dev neurons lack provenance + the layer=-1 bug are symptoms tracked by the
   write-gate workstream (curation debt), not separately fixed here — the graph is a
   test bed; known-bad rows stay as regression evidence.

## W1 verification evidence (2026-07-01)
- 314 tests pass (24 new in test_substrate_ontology.py); NASA strict clean.
- Migration 013 applied to corvus_aero: abstraction backfill covered all 2263
  neurons (procedure 1050 / process 478 / principle 327 / artifact 281 /
  structural 65 / concept 62); centrality refreshed (1991 rows).
- Retrieval non-regression (5 smoke queries, embed-only, top-10 Jaccard vs
  pre-W1 baseline): 1.00 / 0.82 / 1.00 / 0.82 / 0.82, top-5 stable — the
  cold-start prior reorders a mature graph only marginally (shrinkage
  suppresses it once usage history exists).
- Behavior notes: consolidation deactivation now keys on abstraction
  'artifact' (was layer==5) — widens reclaim to evidence-tier nodes at any
  depth; emergent-cluster gap check now counts any process-abstraction
  member as coordination (was layer==2 only). Both intended.
- NOTE: run_consolidation has NO caller anywhere (decay loop never runs
  today) — wired in W2b where decay is the soft write gate.

## W2a verification evidence (2026-07-01)
- 327 tests pass (13 new in test_cheap_recall.py); NASA strict clean.
- **CRITICAL FIX FOUND DURING MEASUREMENT:** the production LLM classifier
  has been silently broken — `llm_provider._anthropic_chat` ran the Claude
  CLI with the repo as cwd and `--append-system-prompt`, so the CLI loaded
  the project's .mcp.json/CLAUDE.md and answered "I need permission to
  access the Corvus neuron graph" instead of classifying. Every classify
  call burned ~$0.006-0.024 and fell back to empty classification
  (departments=[], intent=general_query). Fixed: cwd=/tmp,
  --strict-mcp-config, --no-session-persistence, --system-prompt (full
  replace). Verified: classifier returns real intents/departments again.
- Measurement (5 smoke queries, live aero DB, top_k=10, post-fix):
  | metric | cheap | full |
  |---|---|---|
  | warm latency | 284-558 ms | 11.4-17.0 s |
  | classify cost | $0 | ~$0.024/query |
  | LLM calls | 0 | 1 (Haiku via CLI) |
  top-10 Jaccard cheap-vs-full: 0.33/0.50/0.43/0.33/0.00 — the classify
  boost (×1.25/×1.5) + LLM phrase keywords genuinely reshape ranking.
  Neighbor-vote regions matched LLM-classified departments on 4/5 queries
  (DCAA query voted Executive Leadership+Finance vs LLM's Finance-first).
- Adaptive mode: 0/5 escalations (all top-neighbor sims >= 0.35 threshold);
  returns cheap results at cheap latency. Escalation verified in unit tests.
- Answer-quality (LLM-judge) delta remains measurable via the existing
  EvalRun machinery now that recall_mode plumbs through prepare_context;
  not run here (cost/wall-time) — retrieval-level delta documented instead.

## Verification protocol (per workstream)
1. `TENANT_ID=corvus-aero pytest tests/ -v` — full suite green
2. NASA lint clean on touched files (hook enforces on edit)
3. New behavior covered by hermetic tests
4. Commit per workstream, descriptive message, NO push
