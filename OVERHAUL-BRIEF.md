# Corvus Overhaul Brief

> Single-file kickoff for a Fable 5 (claude-fable-5) overhaul pass on Corvus.
> Generated 2026-07-01 from the Master Corvus roadmap §9 (Generic-Org & Matrix Memory).
> Canonical spec source: `~/Projects/master-corvus/public/roadmap-state.json` (nodes `plat-*`).
> This file is a convenience bundle — if it drifts from the roadmap, the roadmap wins.

## 0. How to use this brief

- **Working directory:** open Fable 5 with the repo ROOT as cwd: `~/Projects/corvus/` (NOT `backend/`). The root holds `CLAUDE.md` (LLM-provider policy, NASA linter rules, multi-tenant conventions, dev commands) and `.mcp.json`, and contains both `backend/` and `frontend/`. `CLAUDE.md` auto-loads and keeps the model on-convention.
- **Read the spine (§2) before changing anything.** Map the system first.
- **Do NOT attempt a single-pass rewrite of the whole codebase.** Work the §9 workstreams (§3) in the dependency order in §4, verifying each against the NASA linter + `pytest` before moving on.
- **Hard conventions (from `CLAUDE.md`):** all LLM calls route through the Claude CLI via subprocess (never the Anthropic SDK); strip `CLAUDECODE*`/`CLAUDE_CODE_*` from child env when spawning the CLI; NASA linter is enforced (no recursion, bounded loops, `MappingProxyType` for module dicts, no bare except, functions <100 lines hard / <60 guideline). Fix strict violations immediately.

## 1. Repo root & layout

Repo root: `~/Projects/corvus/`

```
corvus/
  CLAUDE.md                 <- conventions; auto-loaded. READ FIRST.
  .mcp.json                 <- MCP server config (stdio: python -m app.mcp_server)
  docker-compose.yml
  backend/
    app/                    <- FastAPI app (routers, services, models, pipeline)
    tenants/{tenant_id}/    <- ALL domain-specific content (prompts, patterns, seed, concepts)
    alembic/versions/       <- migrations
    tests/
    venv/
  frontend/
    src/components/         <- React + Vite + TS UI (D3 / Sigma.js viz)
```

Multi-tenant: `TENANT_ID` env selects tenant (`corvus-aero`, `corvus-flow`, `corvus-roost`, `corvus-hedge`, `corvus-apex`). Service code is domain-agnostic; everything domain-specific lives under `backend/tenants/`.

Dev / verify commands:
```bash
cd ~/Projects/corvus/backend && source venv/bin/activate
TENANT_ID=corvus-aero PORT=8002 uvicorn app.main:app --port 8002 --reload
TENANT_ID=corvus-aero pytest tests/ -v
# frontend
cd ~/Projects/corvus/frontend && npm run dev   # (configured port 8004)
```

## 2. The spine — read these first (orientation before overhaul)

| Concern | File | What lives here |
|---|---|---|
| App wiring / entry | `backend/app/main.py` | FastAPI app, router mounts, startup (adjacency cache load, MCP session mgr) |
| Data model | `backend/app/models.py` | `Neuron`, `NeuronEdge` (stellate/pyramidal), `NeuronFiring`, `NeuronScoreOverride`; authority_level, weak_edges JSONB, embedding |
| Query pipeline | `backend/app/services/pipeline/stages/__init__.py` | `build_default_pipeline()` — the 8 stages in order |
| Scoring | `backend/app/services/scoring_engine.py` | 5-signal calc (burst/impact/precision/novelty/recency) + relevance, `_compute_gated_combined`, weights |
| Spread / candidates | `backend/app/services/neuron_service.py` | `spread_activation()`, `_compute_edge_activation()`, vectorized scoring |
| Adjacency cache | `backend/app/services/adjacency_cache.py` | in-memory edge graph for spread |
| Classify (LLM on read) | `backend/app/services/pipeline/stages/classify_stage.py` -> `executor._embed_and_classify()` | the per-query Haiku call (cheap-recall target) |
| Structural fast path | `backend/app/services/structural_resolver.py` | zero-cost regex/keyword resolve |
| Inhibition | `backend/app/services/inhibitory_service.py` | 3-pass GABAergic regulation |
| Prompt assembly | `backend/app/services/prompt_assembler.py` | top-K packing into system prompt |
| Memory maintenance | `backend/app/services/consolidation.py`, `synaptic_learning.py` | decay/prune/deactivate; utility EMA |
| Growth loop | `backend/app/services/autopilot_pipeline/` + `backend/app/routers/autopilot.py` | gap-detect -> query-gen -> exec -> eval -> refine -> propose |
| Gap detection | `backend/app/services/gap_detector.py` | `detect_gaps_scored()` heuristics (reconciler extends this) |
| Clustering | `backend/app/services/clustering.py` | Leiden community detection (seeding + reconciler) |
| Write primitive | Action Bus (AIP Pattern #1) | universal governed write (write-gate routes through this) |
| MCP surface | `backend/app/mcp_server.py` | 7 tools: query_graph, impact_analysis, neuron_detail, browse_departments, graph_stats, cost_report, discover_clusters |
| Domain config | `backend/tenants/{tenant_id}/` | tenant.yaml + prompts/patterns/seed/concepts |
| Ingest guardrails | `backend/app/services/document_extractor.py`, `ingest_reviewer_tools.py` | whole-doc extract + 3 guardrail layers (write-gate reuses) |
| Frontend | `frontend/src/components/` | UI + graph viz |

## 3. The overhaul spec (§9 workstreams)

Verbatim from the roadmap `plat-*` node prompts. `plat-epic` is the framing; the five workstreams follow.

---

## §9 · plat-epic — Generic-org & matrix memory (epic)  _(status: proposed)_

**Summary:** Epic: generalize Corvus from the aero tenant into a domain-agnostic, enterprise-sellable governed-memory MCP, and model matrix orgs as one shared substrate + per-region loops + a horizontal reconciler. Captures the 2026-06-28 design session.

Generic-org & matrix-org memory platform -- epic. Captures the 2026-06-28 design conversation that resolved how Corvus generalizes from the aerospace tenant into a domain-agnostic, enterprise-sellable governed-memory product. READ THIS NODE FIRST; the five child nodes carry the per-workstream implementation detail.

=== THE FRAMING DECISION (do not relitigate) ===
Corvus is a deliberately powerful, GOVERNED memory MCP. It is NOT a harness and must never become one. The agent runtime (tool dispatch, file edit, shell, permissions, model loop, TUI) lives in Claude Code / opencode; Corvus is the memory substrate those runtimes call into via MCP. This is consistent with the prior decision to build and then DELETE agent orchestration (~1,564 LOC) on the rationale "the neuron graph IS the coordination layer; expose via MCP to genuine external agents." Re-introducing harnesses (one per silo, plus a reconciliation harness) or an inter-agent message bus would re-make exactly that deleted mistake.

=== PRODUCT POSITIONING (the sell) ===
Category: governed enterprise memory for LLMs -- competes with opaque enterprise RAG (Glean et al.) and vendor-controlled chat memory. Differentiation: control + organization + measurability + full audit trail. Precise pitch (defensible in a skeptical engineering room): your proprietary, current, access-controlled knowledge GROUNDS and OVERRIDES the model's stale/general priors, with provenance and an audit trail. We control WHAT the model is allowed to see and cite, not the base weights. Two-layer product that the codebase already cleaves along:
  - Seamless end-user layer  = the MCP recall path (see plat-cheap-recall).
  - Tunable controller layer  = the governance surface (authority levels, tiered write gate, pruning diagnostics, query lab, engrams, eval metrics) -- the admin console IT actually buys.
Ties to existing GTM memory: domain-agnostic platform, aerospace-first wedge, mid-market accessibility a hard constraint.

=== THE MENTAL MODEL: ONE CORTEX, MANY COLUMNS ===
A matrix organization (Design Eng, Manufacturing, QA/Audit, Legal, Contracts, Program Mgmt, HR ...) maps onto cortex, and Corvus already built the bones:
  - Vertical silos          = cortical columns (deep, specialized, local) -> stellate (intra-region) edges, already exist.
  - Horizontal integration  = association fibers / corpus callosum -> pyramidal (cross-region) edges, already exist.
  - Conflict monitor        = a privileged loop reading the fibers (see plat-reconciler).
The matrix is NOT a storage decision. It is ONE shared substrate (silos = labeled regions, never separate graphs -- separation kills cross-silo edges at exactly the seams that matter) + N vertical autopilot loops (one per silo, tuned to its OODA cadence) + 1 horizontal reconciler loop. All loops are autopilot-style deterministic pipelines, not harnesses.

=== THE FIVE WORKSTREAMS (child nodes) ===
1. plat-substrate-ontology -- demote the 6-layer org chart from spine to projection; ingest-first emergent seeding for any blank org; authority-based cold-start weighting.
2. plat-cheap-recall       -- embed-only recall path that removes the per-query LLM classify call from the hot path; adaptive escalation.
3. plat-write-gate         -- turn the binary human-approval gate into a tiered, policy-driven control surface (the product, not a bug).
4. plat-region-config      -- per-silo ontology projection / scoring weights / loop config / ACL, all over ONE shared substrate.
5. plat-reconciler         -- the horizontal cross-region contradiction/divergence/homonym/seam-gap loop; the differentiator.

=== DEPENDENCY ORDER ===
plat-substrate-ontology -> plat-region-config -> plat-reconciler (region tags must be decoupled from hardcoded org layers before regions can be configured, and regions must exist before they can be reconciled). plat-cheap-recall and plat-write-gate are largely independent and can land in parallel. Both plat-substrate-ontology and plat-write-gate are mostly RECOMBINATIONS of machinery that already exists (Leiden clustering, authority_level, two-phase guardrail layers, NeuronRefinement audit, consolidation/decay), not net-new systems.

Provenance: design session 2026-06-28 (Tyler + Claude). Source review covered models.py, scoring_engine.py, neuron_service.py, the 8-stage pipeline, consolidation.py, gap_detector.py, the autopilot pipeline, and mcp_server.py.

---

## §9 · plat-substrate-ontology — Substrate / ontology split + generic-org seeding  _(status: proposed)_

**Summary:** Demote the 6-layer org chart from engine spine to navigational projection. Ingest-first emergent seeding (whole-doc extract -> Leiden cluster -> human-approved labels -> bootstrap edges) for any blank org. Authority + freshness + centrality as cold-start prior.

Substrate / ontology split + generic-org seeding. Make the biomimetic engine domain-agnostic so any organization can load a blank canvas without hand-authoring an org taxonomy.

=== PROBLEM ===
The 6-layer org ontology (L0 Department -> L1 Role -> L2 Task -> L3 System -> L4 Decision -> L5 Output) is treated as a vestigial navigational convenience by the owner, but it is currently LOAD-BEARING in the engine:
  - Spread decay distinguishes stellate (intra-department) vs pyramidal (cross-department) -- app/services/neuron_service.py spread_activation() / _compute_edge_activation() (stellate decay 0.3, pyramidal 0.5, instantiate 0.6).
  - Inhibition does regional density suppression keyed on department/role_key -- app/services/inhibitory_service.py (three-pass: basket/chandelier/martinotti).
  - Gap detection's sparse-subtree / coverage-gap / emergent-cluster heuristics assume the hierarchy -- app/services/gap_detector.py detect_gaps_scored().
  - Classification boost keys on dept_match (x1.25) / role_match (x1.5) -- app/services/scoring_engine.py _apply_classification_boost().

=== GOAL ===
Demote the ontology from spine to PROJECTION. Substrate = flat neurons + emergent edges + an abstraction TYPE. The org chart (department/role) survives as tags/metadata used for navigation and as ONE possible projection, not as the engine's coordinate system. Different orgs (or different silos within one org -- see plat-region-config) can then choose different projections without touching the engine.

=== REFRAME THE LAYER AXIS ===
Change layer semantics from "where in the org" to "what kind of knowledge" -- a universal abstraction gradient that holds for any organization and is classifiable from content alone at ingest:
    Principle/Policy (the why) -> Process (the flow) -> Procedure (the how) -> Artifact/Record (the evidence)
Decide: keep numeric `layer` repurposed as the abstraction gradient, or add an `abstraction_type` enum and free `layer` from org meaning. Region (the generalization of `department`) becomes a separate tag dimension.

=== GENERIC-ORG SEEDING: ingest-first, structure-emergent ===
Do NOT ask a new org to model a taxonomy upfront.
  1. Point Corvus at their existing corpus (wiki/Confluence/SharePoint/Drive/tickets/repos). Run the two-phase whole-doc extraction to mint FLAT neurons + embeddings -- app/services/document_extractor.py extract_whole_document() (one Opus call up to ~150k tokens, emits artifact-shape rows; existing Phase 1).
  2. Auto-discover top-level structure: cluster the initial embeddings with Leiden -- app/services/clustering.py find_clusters() (exposed as MCP discover_clusters). LLM-label each cluster; human approves/renames. The discovered taxonomy IS the seed, replacing a whiteboard org chart.
  3. Bootstrap edges so spread activation is not dead on day one: same-source-document co-occurrence -> weak edge; embedding kNN -> weak edge; explicit links/citations -> typed edge. Use the existing NeuronEdge.source values ('bootstrap','concept_seed') and the weak_edges JSONB tier on Neuron.

=== COLD-START WEIGHTING (asked-for explicitly) ===
With zero firing history, burst/recency/impact are neutral. Provide a Bayesian PRIOR that hands off to usage signals as the posterior:
  - authority_level (ALREADY EXISTS on Neuron: binding_standard > regulatory > industry_practice > organizational > informational). An org declares "handbook is binding, this Slack export is informational" before any usage data.
  - source recency / provenance (doc edited last week > doc from 2019).
  - structural centrality: degree / PageRank over the bootstrapped edges.
Implement as a prior term in app/services/scoring_engine.py applied while invocations==0 (or age below a threshold), decaying out as real firings accrue. Story: authority + freshness are the prior; firing history is the posterior.

=== FILES TO TOUCH ===
  - app/models.py            -- add abstraction_type (or repurpose layer); generalize `department` -> `region` tag dimension; ensure projection metadata is separable.
  - app/services/scoring_engine.py    -- cold-start prior term; classification boost keyed on generic region tag.
  - app/services/neuron_service.py    -- spread decay keyed on edge_type that is derived from generic region membership, not hardcoded department.
  - app/services/inhibitory_service.py-- regional density keyed on configurable region field.
  - app/services/gap_detector.py      -- sparse-subtree / coverage / emergent-cluster heuristics keyed on generic regions.
  - app/services/clustering.py        -- seeding-time cluster -> label -> approve flow.
  - backend/tenants/{tenant_id}/      -- per-tenant projection config + seed entrypoint.

=== ACCEPTANCE ===
  - A brand-new tenant can be seeded from a raw corpus with ZERO hand-authored taxonomy and produce a navigable, weighted, edge-bootstrapped graph.
  - Spread activation, inhibition, and gap detection all run on generic region tags, not hardcoded org layers.
  - Cold-start ranking is sane (authority + freshness + centrality) before any queries, and converges to usage-driven ranking after N queries.
  - Eval-corpus quality for a re-seeded aero tenant >= current aero baseline (no regression from the generalization).

=== OPEN QUESTIONS ===
  - Numeric layer repurposed vs new abstraction_type enum.
  - Multi-projection: one neuron carrying several tag systems (org chart AND capability map AND product line) simultaneously.
  - How aggressively the cold-start prior should decay vs invocation count.

---

## §9 · plat-cheap-recall — Cheap recall mode (embed-only, no LLM classify)  _(status: proposed)_

**Summary:** Embed-only recall path that removes the per-query Haiku classify call from the hot path; departments via neighbor-vote, keywords via tokenizer; adaptive escalation to full classify only when neighbor similarity is low. Default for the seamless end-user layer.

Cheap recall mode -- remove the per-query LLM classify call from the recall hot path so memory recall can run on every agent turn at ~0 cost and ~tens of ms.

=== PROBLEM ===
Every read currently pays a Haiku classify call. The classify stage runs the LLM -- app/services/pipeline/stages/classify_stage.py -> app/services/executor._embed_and_classify() -- producing intent / departments / role_keys / keywords in parallel with the embedding. Fine for occasional domain Q&A; a latency + cost tax when an external agent recalls memory every turn.

=== DESIGN: embed-only recall, no LLM ===
Embed the query locally (existing 384-dim model, ~ms), then run the rest of the existing pipeline with NO LLM call:
  semantic prefilter -> 5-signal score (relevance from embedding similarity; RRF fusion already in scoring_engine.calc_relevance hybrid path) -> spread activation -> inhibition -> assemble.
Reproduce the classify-stage outputs WITHOUT a model, by letting the graph classify itself:
  - keywords      : existing stopword-filtered tokenizer (already in scoring_engine).
  - departments / role_keys (regions) : NEIGHBOR VOTE -- embed query, pull top-k nearest neurons, their region/role tags ARE the predicted regions. The graph already encodes the classification.
  - intent        : default neutral voice, or a tiny local classifier. (Intent mostly drives the closing-format instruction in prompt assembly; a neutral default is acceptable for the agent-memory use case.)

=== ADAPTIVE ESCALATION ===
If top-neighbor cosine similarity < confidence threshold (ambiguous query), fall through to the full LLM classify. Recognition (fast, automatic, System 1) handles the common case; deliberation (LLM, System 2) only fires when recognition is uncertain. recall_mode in {cheap, full, adaptive}; adaptive is the recommended production default for the seamless end-user layer.

=== IMPLEMENTATION ===
  - app/services/pipeline/stages/classify_stage.py  -- add CheapClassifyStage (embed + tokenizer + neighbor-vote; no LLM).
  - app/services/pipeline/stages/__init__.py        -- build_cheap_pipeline() / parametrize build_default_pipeline() to swap the classify stage.
  - app/services/executor.py                         -- neighbor-vote helper (top-k -> region tally).
  - app/mcp_server.py                                -- query_graph gains a `mode` param (cheap|full|adaptive).
  - settings                                         -- recall_mode default + cheap_recall_confidence_threshold.

=== MEASUREMENT (this is also the justification) ===
Run cheap vs full over the eval corpus and compare EvalScore dimensions (accuracy/completeness/clarity/faithfulness/overall) + cost + latency. This quantifies EXACTLY what the LLM classify buys per dollar. Hypothesis: for most agent-memory recalls it buys little, making cheap the right default. Use app/services/metrics_aggregator.py for the comparison.

=== ACCEPTANCE ===
  - Cheap mode answers a query with zero LLM calls, $0 marginal cost, latency under target (set during impl, e.g. < ~150 ms warm).
  - Adaptive mode escalates to LLM classify ONLY when top-neighbor similarity is below threshold.
  - Eval delta (cheap vs full) is measured and documented; cheap mode is within an agreed quality band of full on the eval corpus.

---

## §9 · plat-write-gate — Tiered policy-driven write gate  _(status: proposed)_

**Summary:** Turn the binary human-approval queue into a tiered control surface the controller configures: authoritative writes gated, observational writes auto-commit; guardrails as the auto-gate; provenance + decay as the safety net; everything through the Action Bus.

Tiered, policy-driven write gate -- turn the binary human-approval gate into the controller's configurable control surface. For enterprise controlled memory the gate is the PRODUCT, not a bug; the problem is that it is ONE binary gate over everything.

=== PROBLEM ===
Today autopilot/ingest writes go through a single human-approval proposal queue (AutopilotProposal / ProposalItem; staged in app/services/autopilot_pipeline/stages/persistence_stage.py, applied via the proposal-apply path). Review does not scale linearly with capture. Symptoms already visible: ~99% of dev neurons lack provenance metadata, and the layer = -1 bug -- both fingerprints of curation falling behind capture. Meanwhile co-firing edge-weight updates ALREADY auto-commit (ungated); the gate today is specifically on neuron CONTENT mutations. So the architecture already half-believes the split.

=== TWO WRITE CLASSES ===
  - Authoritative writes (policies, standards, source of truth) -- MUST stay human-gated. High scrutiny. This is the control surface and a selling point.
  - Observational / working writes (session learnings, usage-derived associations, edge updates) -- should AUTO-COMMIT. Too high-volume to gate, low stakes, and decay/pruning already cleans them up.
Conflating these under one gate is the actual problem.

=== DESIGN: tiered policy-as-config (the "infinitely tunable for the controller" knob) ===
  - Policy declares which classes gate. Example default: authority_level >= regulatory -> mandatory pre-approval; informational -> auto-commit with audit. The controller dials the threshold per tenant / per region.
  - Confidence-based routing reusing EXISTING guardrails: the two-phase ingest already runs three layers -- Layer 1 fail-closed _validate_placement_updates() in ingest_reviewer_tools.py (rejects invented parents/departments/roles), Layer 2 read-back verification, Layer 3 derived-summary match. Make "passes all three guardrails" the auto-approval criterion; tripping any one drops the write to the human queue. The guardrails BECOME the auto-gate.
  - Provenance + reversibility as the safety net: every auto-committed write carries provenance and is cheaply reversible -- NeuronRefinement audit records + is_active logical delete already exist. "Auto-commit then audit/rollback" is therefore safe for the low-stakes tier.
  - Decay as the soft gate: app/services/consolidation.py run_consolidation() (utility decay 0.95/tick, firing prune at 2000 queries, Layer-5 deactivate below 0.05 utility) reclaims auto-committed writes that never get reinforced. Forgetting substitutes for up-front gating. Biomimetic: form memories freely, prune what isn't reinforced.
  - Route ALL writes through the Action Bus (AIP Pattern #1, the universal governed write primitive) so every write -- auto or queued -- is auditable regardless of tier.

=== IMPLEMENTATION ===
  - app/services/write_gate.py (new)  -- WriteGatePolicy evaluation: (write_class, authority_level, guardrail_result, confidence) -> {auto | queue}.
  - app/services/autopilot_pipeline/stages/persistence_stage.py + proposal-apply path -- consult policy; auto path applies via Action Bus with provenance; queue path is the current proposal queue.
  - AutopilotConfig / tenant (+ per-region, see plat-region-config) -- where the controller stores the policy.
  - app/models.py -- persist WriteGatePolicy if it needs to be queryable/audited.

=== ACCEPTANCE ===
  - A controller can configure a policy where informational writes auto-commit and regulatory writes queue, per tenant/region.
  - Guardrail-passing ingest auto-commits; guardrail-failing ingest queues.
  - Every auto-committed write has provenance and is reversible (audit record + logical delete).
  - Unreinforced auto-committed writes are reclaimed by consolidation/decay.
  - All writes, auto or queued, appear in the Action Bus audit trail.

---

## §9 · plat-region-config — Per-region (silo) config over shared substrate  _(status: proposed)_

**Summary:** Matrix org as ONE shared graph with silos as labeled regions (never separate graphs). Per-region ontology projection, scoring weights, autopilot loop config, and ACL/need-to-know. Stellate=intra-region, pyramidal=cross-region already exist.

Per-region (silo) configuration over ONE shared substrate. Model the matrix organization correctly: silos are deep vertical knowledge bases that still coordinate as one org.

=== CORE CALL (do not separate graphs) ===
ONE shared substrate; silos = labeled REGIONS (the generalization of `department`). Do NOT give each silo its own tenant/graph: edges are intra-graph, so separation would kill cross-silo spread activation at exactly the inter-silo seams where coordination and discrepancies live -- you'd be siloed RAG with extra steps, and the horizontal reconciler (plat-reconciler) would have nothing to traverse. The stellate (intra-region) vs pyramidal (cross-region) edge distinction already in the engine IS the matrix structure: cortical columns + association fibers.

=== PER-REGION KNOBS (all over one graph) ===
Each silo feels like its own deep vertical system to its users while physically sharing one substrate:
  (a) Navigational ontology PROJECTION -- Design organizes by system/subsystem, Legal by contract/clause, HR by policy/process. Same substrate, different overlay (depends on plat-substrate-ontology's substrate/ontology split).
  (b) Regionalized SCORING WEIGHTS -- the sharp insight: the 5-signal weights should differ per silo because their epistemics differ. Manufacturing weights recency high (shop floor changes daily); Legal weights authority/impact high (precedent matters, recency barely does). Weights are currently global settings in app/services/scoring_engine.py (_compute_gated_combined uses weight_relevance 0.50, weight_impact 0.15, weight_recency 0.15, weight_burst 0.08, weight_precision 0.07, weight_novelty 0.05). Make them per-region overridable. Defensible pitch: "your QA knowledge and your shop-floor knowledge don't decay the same way, so we don't score them the same way."
  (c) Loop CONFIG -- per-region AutopilotConfig: interval, directives, gap heuristics, model. Manufacturing's loop ticks fast; Legal's ticks slow and conservative.
  (d) ACL / need-to-know -- per-neuron / per-region visibility. Legal/HR confidential neurons must not be recallable by Manufacturing users. Builds on existing access-gate authentication; extend authority/provenance metadata with a visibility/ACL dimension. (Note: the reconciler in plat-reconciler needs a PRIVILEGED cross-region read role that bypasses ACL for DETECTION ONLY, never leaking raw content to end users.)

=== DEPENDS ON ===
plat-substrate-ontology (region tag must be decoupled from hardcoded org layers first).

=== IMPLEMENTATION ===
  - backend/tenants/{tenant_id}/  -- region registry: list of regions with {ontology projection, weight overrides, loop config, acl}.
  - app/services/scoring_engine.py -- per-region weight lookup (extend NeuronScoreOverride or add a RegionPolicy table).
  - autopilot config / router      -- AutopilotConfig keyed by region.
  - app/services/neuron_service.py -- candidate-load ACL filter keyed on requester identity/role.
  - app/mcp_server.py              -- requester scoping on query_graph (region-bounded recall).
  - app/models.py                  -- RegionPolicy and/or per-neuron visibility/ACL fields.

=== ACCEPTANCE ===
  - The same graph serves a Manufacturing user and a Legal user with DIFFERENT ontology views, DIFFERENT scoring weights, and DIFFERENT loop cadences.
  - A Manufacturing user cannot recall a Legal-confidential neuron.
  - Cross-region pyramidal edges remain intact (recall across regions still works where permitted).
  - A privileged reconciler role can read across all regions for detection.

---

## §9 · plat-reconciler — Horizontal reconciler loop (cross-region)  _(status: proposed)_

**Summary:** The differentiator: a specialized autopilot loop that detects cross-silo contradictions, staleness divergence, homonyms/synonyms, and seam coverage gaps over pyramidal edges, then routes flags to owning controllers. Privileged read; never auto-edits authoritative knowledge.

Horizontal reconciler loop -- the differentiator. Detect and route cross-silo discrepancies in a matrix org. A specialized AUTOPILOT loop whose gap-detector runs cross-region heuristics over the pyramidal seams. NOT a harness, NOT an inter-agent message bus (a matrix of silo-agents passing messages to a reconciliation-agent is exactly the agent orchestration already built and DELETED ~1,564 LOC; the shared graph IS the coordination medium).

=== WHAT IT HUNTS (cross-region heuristics over pyramidal seams) ===
  1. Contradictions -- neuron pairs with high embedding similarity but in DIFFERENT regions whose content conflicts (e.g. Design "max op temp 120C" vs Mfg process sheet "cure at 130C"). Candidate-find = cheap graph query (high cross-region similarity + conflicting assertions). Verdict = BOUNDED LLM judgment on the shortlist (structured: is_contradiction + severity + rationale) -- not free-roaming agency.
  2. Staleness divergence -- a cross-region edge where one endpoint's last_verified jumped recently and the other did not (Legal updated a clause; the Program Mgmt plan still cites old terms).
  3. Homonyms vs synonyms -- the subtle failure mode of a SHARED embedding space. Cross-region near-duplicates: "tolerance" means three different things to Design / Mfg / HR. Disambiguate: same concept -> link/merge (good coordination); false friend -> keep separate / namespace (else spread activation pollutes recall). Uniquely a horizontal job.
  4. Seam coverage gaps -- two regions co-fire constantly but no shared Task/Decision neuron coordinates them. The existing emergent-cluster gap heuristic in app/services/gap_detector.py, pointed ACROSS regions, proposes a coordination neuron.

=== REUSES EVERYTHING ===
app/services/gap_detector.py (add cross-region heuristics), the proposal queue, eval, the Action Bus, embeddings, pyramidal edges, and Leiden clustering (app/services/clustering.py). It is a recombination, not a new system.

=== GOVERNANCE-SAFE BY CONSTRUCTION ===
The reconciler has PRIVILEGED cross-silo READ (it must see across columns to find conflicts -- depends on plat-region-config's privileged role) BUT:
  - its output is FLAGS / PROPOSALS routed to the owning silo's human controller;
  - it NEVER auto-edits authoritative knowledge (obeys the tiered write gate, plat-write-gate -- authoritative writes stay human-gated);
  - it NEVER leaks raw cross-silo content to end users.
It detects and ROUTES; the silos RESOLVE. Optionally it writes a neutral "coordination" neuron recording the resolution.

=== DEPENDS ON ===
plat-region-config (needs region tags + ACL + a privileged reader role) and plat-substrate-ontology (generic region semantics).

=== IMPLEMENTATION ===
  - app/services/reconciler.py (new) or an autopilot_pipeline variant -- the horizontal loop.
  - app/services/gap_detector.py -- cross-region contradiction / staleness-divergence / homonym / seam-gap heuristics.
  - contradiction LLM judge -- bounded, structured verdict (is_contradiction, severity, rationale) over candidate pairs.
  - proposal type 'reconciliation' + routing to the owning region's controller.
  - privileged reader role bypassing ACL for detection only (from plat-region-config).
  - optional MCP tool: reconciliation report / open-discrepancies for the controller console.

=== ACCEPTANCE ===
  - Given two regions with a SEEDED contradiction, the reconciler surfaces it as a routed proposal to the correct owner WITH severity.
  - A homonym pair across regions is correctly NOT linked; a true synonym pair IS linked/merged.
  - A seam coverage gap proposes a shared coordination Task neuron.
  - No raw confidential content crosses regions to end users; all reconciler actions are in the Action Bus audit trail.

---

## 4. Recommended sequencing (do NOT do it all at once)

Dependency order from the epic:

```
plat-substrate-ontology  ──enables──▶  plat-region-config  ──enables──▶  plat-reconciler
plat-cheap-recall     (independent, parallel)
plat-write-gate       (independent, parallel)
```

1. **plat-substrate-ontology** — decouple the engine from the hardcoded org layers first; nothing else is clean until region tags exist independent of the 6-layer chart. Mostly a recombination of existing machinery (Leiden, authority_level, weak_edges, bootstrap edges).
2. **plat-cheap-recall** and **plat-write-gate** — land in parallel; both are self-contained and reuse existing pieces (classify stage / guardrail layers + Action Bus + consolidation).
3. **plat-region-config** — needs the substrate/ontology split done.
4. **plat-reconciler** — the differentiator; needs regions + ACL from region-config. Extends `gap_detector.py`, reuses proposal queue + Action Bus.

**Per-workstream loop:** read the relevant spine files → make the change → `TENANT_ID=corvus-aero pytest tests/ -v` → confirm NASA linter clean → only then advance. Prefer additive/feature-flagged changes over rewrites of working paths.
