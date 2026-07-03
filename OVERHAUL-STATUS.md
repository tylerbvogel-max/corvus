# Corvus Overhaul — Status & Design Decisions

> Working log for the §9 Generic-Org & Matrix Memory overhaul (OVERHAUL-BRIEF.md).
> Fable 5 session started 2026-07-01. Baseline: 290 tests pass, main @ fc3ff3b.

## Progress

| Workstream | Status | Commit |
|---|---|---|
| Mapping + design decisions | done | — |
| W1 plat-substrate-ontology | done | (see git log) |
| W2a plat-cheap-recall | done | (see git log) |
| W2b plat-write-gate | done | (see git log) |
| W3 plat-region-config | done | (see git log) |
| W4 plat-reconciler | done | (see git log) |
| Final verification + docs | done | (see git log) |

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

## W2b verification evidence (2026-07-01)
- 345 tests pass (18 new in test_write_gate.py); NASA strict clean.
- Live smoke (dev DB, transaction rolled back): informational proposal ->
  auto route -> state=applied, reviewed_by=write_gate:tiered-v1, Action tree
  [proposal.apply root + neuron.create child, actor_type=system], neuron
  created with auto-classified abstraction, NeuronRefinement audit row.
- Consult points: autopilot ProposalCurationStage (confidence =
  eval_overall/5) and ingest Phase-2 post-placement (guardrails_passed=True
  by construction of a verified placement). Gate failure never blocks
  placement — proposal just stays queued.
- Apply dispatch extracted from routers/proposals.py into
  proposal_apply_service (shared by human approval + gate auto route;
  router is now thin).
- Consolidation (decay = the reclamation backstop) was NEVER WIRED — now
  rides the autopilot /tick heartbeat at most every
  consolidation_interval_hours (24h default), independent of autopilot
  enabled state. The existing corvus-autopilot.timer (5min) drives it.
- Policy: tenant.yaml `write_gate:` block; aero set to tiered with
  auto_commit_max_authority=guidance, min_confidence=0.6. Other tenants
  default to manual (pre-gate behavior).
- Tier-0 note: usage-mechanics writes (co-fire weights, synaptic learning,
  decay) intentionally stay off the Action Bus — they are hot-path,
  event-audited (SynapticLearningEvent, firing rows), and routing them
  through the bus would put an Action row on every query.

## W3 verification evidence (2026-07-01)
- 360 tests pass (15 new in test_region_policy.py); NASA strict clean;
  migration 014 applied to corvus_aero.
- Live ACL matrix (dev DB, RegionPolicy marking 'Regulatory' restricted,
  transaction rolled back): unrestricted requester sees it; region member
  sees it; Manufacturing outsider does NOT (still sees open regions);
  privileged reconciler sees across. Enforced in SQL at candidate load,
  spread promotion, and assembly (defense in depth).
- Per-region weights: vectorized scorer switches to per-candidate weight
  arrays only when overrides exist (global-scalar fast path otherwise);
  weight_coldstart_prior participates via rescaling. Unit-tested.
- Per-region loops: AutopilotConfig.region rows (synced from
  RegionPolicy.loop_config via /admin/regions PUT); the tick picks the
  stalest due loop per invocation (round-robin — a fast Manufacturing loop
  never starves behind slow Legal). Region-scoped gap detection for
  thin/zero-hit/stale; coverage/quality-trend/eval-dimension stay global.
- Per-region write-gate overlay: RegionPolicy.write_gate overrides tenant
  policy (route_proposal takes region; both consult points pass it).
- MCP query_graph gains requester_regions (region-bounded recall);
  privileged access is internal-only (never a tool param).

## W4 verification evidence (2026-07-01)
- 373 tests pass (13 new in test_reconciler.py); NASA strict clean;
  migration 015 applied.
- Implemented as integrity-framework scans (maximal reuse of IntegrityScan/
  IntegrityFinding/proposal conversion/audit), NOT a harness:
  reconciler_contradiction, reconciler_staleness, reconciler_homonym,
  reconciler_seam_gap + run_reconciler_sweep orchestrator.
- Live acceptance (seeded rows, cleaned up after):
  * Seeded cross-region contradiction (Engineering 120C max vs Manufacturing
    130C continuous): the real sweep surfaced it as a ROUTED finding —
    region=Engineering, severity=warning, priority=0.8, cross_region=true,
    owning_regions both. Judge rationale precise.
  * Seeded 399d staleness divergence on a pyramidal edge: flagged, routed
    to the STALE side's region (Manufacturing), severity=warning.
  * Notably: the judge correctly ruled the first seeded pair (operating
    limit vs cure temperature) CONSISTENT — different aspects, not a
    contradiction. Judge discrimination works.
- Incremental sweeps: already-judged pairs are excluded, so successive
  sweeps cover the similarity zone instead of re-judging the same top-N;
  LLM judging batched (6 pairs/call), shortlist capped
  (reconciler_max_pairs=12/sweep).
- FIX: llm_provider passed the user prompt via argv — prompts starting
  with "-" (the pair-judging format) crashed the CLI arg parser. Now via
  stdin (also removes argv size limits).
- Governance: privileged internal read; output = routed findings only;
  resolutions convert to proposals via the existing integrity path which
  obeys the tiered write gate; reconciler_interval_hours=0 default
  (manual sweeps; set >0 to ride the tick heartbeat).
- MCP: new reconciliation_report tool (8 tools now); /admin/integrity/
  reconciler/sweep + /report endpoints; findings filterable by region.

## Frequency-hopped citation grounding (anti-hallucination exit layer) — 2026-07-03

Goal (per `frequency-hopping.md`): reduce hallucinated NEURON references by
wrapping the analysis (execute) layer with an entry + exit pair that share a
secret per-query key map. Entry already existed (numeric `[N]` citations);
this adds the exit and swaps the guessable integers for unguessable keys.

- **Mechanism.** At assembly each selected neuron gets a random per-query
  ephemeral key (`[FQ-XXXXXX]`, `secrets`-minted, uppercase hex). The LLM sees
  only the keys — never real neuron IDs or the map (secret to the analysis
  layer). Exit: extract cited keys, check `used ⊆ allowed` (any extra key =
  fabricated reference, caught deterministically) and optional `allowed ⊆ used`
  (`require_all`). Keys rotate every query ("frequency hopping"), so a key
  memorised/guessed from training or another query never validates.
- **Prefix is `FQ-` not the design note's `F-`** — deliberate, so it never
  collides with aircraft designations (F-16 / F-35 / F/A-18). Test asserts this.
- **Scope honesty:** catches fabricated *references*, NOT misinterpretation of a
  correctly-keyed source (entailment is a separate, future concern).
- **Files.** New `services/citation_hopping.py` (pure: mint / extract / verify /
  strip / repair_instruction). Entry: `prompt_assembler.assemble_prompt` gains
  `citation_tokens` (labels generalised int→str via `_build_citation_labels`);
  `_assemble_top_slice` mints the map, carried on `PipelineState.hop_map` →
  `PreparedContext.hop_map`. Exit: `executor._apply_citation_hop_exit` (detect |
  strip | repair — one bounded LLM retry) runs in `execute_query`. Config: 5
  flags (`citation_hopping_enabled` default **False**, prefix, hex width,
  `failure_mode`, `require_all`). Model: `CitationHopSession` table +
  `queries.citation_hop_session_id` (migration `016_citation_hopping`, idempotent).
- **MCP (external analysis layer).** `query_graph` persists the secret map and
  returns an opaque `hop_session_id`; new `verify_citations(hop_session_id,
  answer)` tool grades the agent's answer server-side (9 tools now). The map
  never leaves Corvus.
- **Default OFF** → numeric `[N]` citations and the frontend superscripts are
  unchanged until enabled. Frontend token→superscript mapping for the hop path
  is a noted follow-up.

Verification evidence:
- `tests/test_citation_hopping.py` — 13 hermetic tests (mint uniqueness +
  per-query rotation + format, extraction incl. F-16 non-collision, verify
  clean/fabricated/require_all, strip, repair instruction, assembler entry both
  modes). `pytest test_citation_hopping.py test_prompt_assembler.py` = **17
  passed** (4 existing assembler tests still green → backward compatible).
- NASA lint **clean** on all touched files (no strict violations; the two
  functions I grew past the 60-line guideline were refactored back under).
- Migration applied to all 5 tenant DBs (aero/flow/roost/hedge/apex); `Query`
  loads verified (required because the model now SELECTs the new column).
- End-to-end smoke on **real aero neurons**: entry rendered the three secret
  tokens into a prompt built from live neuron content; a simulated answer citing
  2 real keys + fabricated `FQ-FADEDD` was caught (`hallucinated=['FQ-FADEDD']`,
  grounded neurons 1003 & 2805); `CitationHopSession` persist round-tripped.

## Verification protocol (per workstream)
1. `TENANT_ID=corvus-aero pytest tests/ -v` — full suite green
2. NASA lint clean on touched files (hook enforces on edit)
3. New behavior covered by hermetic tests
4. Commit per workstream, descriptive message, NO push
