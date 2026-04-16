# Corvus AIP Governance Roadmap — Worklog

**Plan source:** `~/.claude/plans/staged-booping-globe.md`
**UI view:** Master Corvus → Roadmaps → ★ AIP Governance
**Dev URL:** http://localhost:5175/

This file is the canonical session handoff for the AIP governance roadmap. At the start of each session, read this first to see where we are and what's next. At the end of each session, update the Current position, Next session starts here, and Session log sections.

## Current position

**Phase:** Phase 1.5 code shipped — verification pending on each sub-item
**Completed items:** #1 Action bus, #2 Bidirectional lineage + score overrides
**Active items (code written, walking verification checklists):** #7 Runtime output policy gates, GTM-A external `/v1/query`, GTM-B remote MCP HTTP+SSE. Checklists live on the `gov-aip-p1_5` node in `master-corvus/public/roadmap-state.json` and on each pattern card in `AIPGovernanceRoadmap.tsx` — walk them before flipping to `done`.
**Next item:** after Phase 1.5 verification closes, #3 Evals as immutable, first-class artifacts (returning to Phase 1 sequence).
**Revised sequence active as of 2026-04-12** — see "Revised sequencing" below. Phase 1.5 was pulled ahead of #3 in this session on explicit user direction ("we have to get all of it eventually, ready to go").

## Revised sequencing (2026-04-12)

The three-leg GTM pivot (tool-call endpoint + remote MCP + admin UI, detailed in Master Corvus → Strategy → Positioning) changes priority ordering downstream of Pattern #3. Rationale: once Corvus sits behind a customer's LLM frontend (Fluent, Claude Enterprise, MCP clients), every answer leaves the API into a third-party context. That surface needs runtime output gates *before* anything else ships.

**Changes vs. original:**
1. **Pattern #7 (runtime output policy gates) moved from Phase 2a to a new Phase 1.5.** It's now a GTM blocker, not a defense-track item. Small surface, sibling of `input_guard.py`, unblocks the tool-call and MCP surfaces being safe to ship externally.
2. **Two new non-AIP items added:** external tool-call endpoint hardening and remote MCP server (HTTP+SSE + OAuth2). These are infrastructure, not governance patterns, but they're gated by #2 (lineage_id in responses) and #7 (output gates). Tracked as "GTM Surfaces" alongside the AIP patterns.
3. **Patterns #4, #5, #6, #8 unchanged.** Phase 2a still defense-track (now just #4), Phase 2b still correctness (#5, #6, #8).

## Next session starts here

> Phase 1.5 complete — Pattern #7 + GTM-A + GTM-B shipped together. Next: Pattern #3 (immutable eval artifacts). After #3, the sequencing returns to Phase 2a/2b per the roadmap tree; re-evaluate when customer traffic lands because real-world data from #7 + GTM-A may reshape priorities.
>
> Read this worklog, then read `~/.claude/plans/staged-booping-globe.md` for full context. Begin scoping Pattern #3 by:
>
> 1. Read `backend/app/models.py` — `EvalScore` exists but slot results are JSON blobs in `Query.results_json`. No frozen eval run artifact.
> 2. Design an `EvalRun` table that snapshots {query set, model versions, scoring-engine version, results, verdicts}. Append-only.
> 3. The `NeuronScoreOverride` system (added in Pattern #2) gives manual tuning levers — eval runs should capture which overrides were active at run time.
> 4. Every eval run gets an immutable ID — this becomes the `eval_run_id` that pairs with `lineage_id` in `/v1/query` responses (`V1QueryResponse.eval_run_id` is already reserved as `None` — just needs populating), making "here is what certified this answer's pipeline" an auditable claim.
> 5. `OutputViolation` rows produced by Pattern #7 are a natural input for eval-run scoring — a run that produced N `block`-severity violations on a canary set should be visible in the EvalRun summary.

## Checklist

### Phase 1 — Dual-purpose foundations
- [DONE] #1 Actions as the universal write primitive
- [DONE] #2 Bidirectional lineage / active provenance graph
- [ ] #3 Evals as immutable, first-class artifacts ← **NEXT**

### Phase 1.5 — GTM gate (NEW, 2026-04-12; code shipped 2026-04-13, verification pending)
These unlock shipping tool-call and MCP surfaces externally. Ordered. Code is written; each sub-item flips to `DONE` only after its verification checklist on the roadmap flowchart node (`gov-aip-p1_5` in `master-corvus/public/roadmap-state.json`) passes end-to-end.
- [ACTIVE] #7 Runtime output policy gates (moved up from Phase 2a) — code shipped, verification checklist queued
- [ACTIVE] GTM-A: External tool-call endpoint hardening (`/v1/query` with auth, rate limiting, `{answer, fragment_labels, fragments, lineage_id, eval_run_id, blocked, violations}` response contract) — code shipped, verification checklist queued
- [ACTIVE] GTM-B: Remote MCP server (HTTP+SSE transport via `StreamableHTTPSessionManager`; RBAC gate reused from `/v1/query`) — code shipped, verification checklist queued

### Phase 2a — Defense-sale track
Pull forward if customer conversation / ATO question / classified-data requirement surfaces.
- [ ] #4 Row-level markings / classification

### Phase 2b — Correctness / experimentation track
Pull forward if pipeline iteration speed is the near-term pain.
- [ ] #5 Typed pipeline DAG with observable stages
- [ ] #6 Ontology branching for safe experimentation
- [ ] #8 Unified autopilot as a typed agent

### Phase 3 — Converge
- [ ] Governance view in frontend (audit log, approval queue, lineage graph, eval history, policy violations)
- [ ] Pull forward the unused track's highest-value item

## Open questions / blockers

- **JSONB action queries may need denormalization.** The lineage trace queries `input_json->>'target_neuron_id'` on the actions table. No index on this expression yet. If action volume grows past ~10k rows, add a materialized `source_neuron_id` column or a GIN index.

## Lessons learned

1. **Enrich existing tables before creating new ones.** Plan called for a new `FiringRecord` table, but `NeuronFiring` already had the right relationships. Adding columns preserved existing infrastructure (consolidation, synaptic learning, burst calcs) without migration risk.
2. **Observability without control levers is incomplete.** Pure lineage (see what happened) is less valuable than lineage + tuning (change what happens). Pairing enriched firings with `NeuronScoreOverride` in the same pattern made it actionable, not just auditable.
3. **The forward chain gap was data quality, not data absence.** `NeuronFiring` existed but only stored IDs and offsets. The 8-signal score breakdown was computed every query and discarded into JSON blobs. Fix was populating fields that should have been there from the start.
4. **Alembic revision IDs must be under 32 chars.** `alembic_version.version_num` is varchar(32). Descriptive IDs fail at migration time.
5. **JPL-4 extractions pay forward.** Extracting `_assemble_top_slice` from `prepare_context` isolated the assembly step — exactly what Pattern #5 (typed pipeline DAG) will refactor into a named stage.
6. **Post-scoring override application keeps the hot path clean.** Overrides apply after vectorized numpy scoring. Only neurons with active overrides take the Python recomputation path — preserves 10-50x speedup for the common case.
7. **Pattern sequencing validated — #2 depends on #1.** Lineage trace joins firing records with action counts. Without the action bus, you couldn't answer "how many times was this neuron modified and by whom?"

## Session log

### 2026-04-09 — Roadmap authored
- Evaluated AIP migration and deferred it; extracted design principles instead.
- Wrote strategic plan at `~/.claude/plans/staged-booping-globe.md`.
- Created visual roadmap page in Master Corvus (`Roadmaps → ★ AIP Governance`) at `~/Projects/master-corvus/src/components/system-docs/AIPGovernanceRoadmap.tsx`.
- Established this worklog and pointer in `~/Projects/corvus/CLAUDE.md`.
- No implementation work started yet.

### 2026-04-09 — Pattern #1, Step 1: action bus + first migrated write path
- Added `Action` ORM model in `backend/app/models.py` (root + child via `parent_action_id`, source links to query/proposal, idempotency key, approval state machine, audit fields).
- Created Alembic migration `backend/alembic/versions/007_add_actions_table.py` (applied cleanly to corvus_aero).
- Built `backend/app/services/action_bus.py`:
  - Class-based `_ActionRegistry` (lowercase singleton — avoids JPL-6 mutable-global trigger).
  - `submit()` validates input via the registered Pydantic schema, persists the audit row first, then runs the handler inside a SAVEPOINT (`db.begin_nested()`) so handler exceptions roll back only handler writes — the audit row survives with `state="failed"`.
  - `approve()` / `reject()` for the deferred-execution path (not exercised yet — `eval.score.set` is auto-apply).
  - Idempotency short-circuit via `_check_idempotency` — repeat submits with the same key return the prior result.
- Added the actions package: `backend/app/services/actions/{__init__.py, eval_score_set.py, init_registry.py}`.
- `eval.score.set` is the first action handler — replaces existing EvalScore rows for a query and inserts new ones.
- Wired `init_actions_registry()` into `backend/app/main.py` lifespan.
- Migrated both EvalScore write sites:
  - `backend/app/routers/query.py:_save_eval_scores` now routes through the bus (`actor_type="user"`, identity from `Depends(resolve_identity)` on the endpoint).
  - `backend/app/routers/autopilot.py:_self_evaluate` routes through the bus with a synthetic `UserIdentity(user_id="autopilot", role="admin", source="system")` and `actor_type="autopilot"`.
- NASA linter: strict checks pass on every touched file. JPL-4 guideline warnings on `submit()` triggered an extraction of `_check_idempotency`; remaining warnings are pre-existing functions left for a dedicated cleanup pass.
- Full pytest suite: 107/107 passing.

**Step 1 limitations (intentional, will revisit):**
- Failed handlers persist a `state="failed"` audit row but only inside the same transaction the caller commits — if the caller rolls back, the audit row is also lost. A separate "audit session" pattern can be added in Step 3 if needed.
- No NASA lint rule yet preventing direct `db.add(...)` outside `services/actions/`. Add in Step 4 once enough write paths are migrated that the rule is enforceable.

### 2026-04-09 — Pattern #1, Step 2: proposal apply + create/refine through the bus
- Added three new action handlers under `backend/app/services/actions/`:
  - `proposal_apply.py` — root container action; no-op handler that records `{proposal_id, item_count, applied_by}` in the audit row. Acts as the parent for all per-item child actions in the apply.
  - `neuron_create.py` — wraps the previous `_apply_create_item` logic. Builds the Neuron from spec, populates external references, writes a `NeuronRefinement(action="create")`, and back-fills `created_neuron_id` + `refinement_id` on the source ProposalItem. Helper `_build_neuron_from_spec` keeps the handler under the JPL-4 guideline.
  - `neuron_refine.py` — wraps the previous `_apply_update_item` logic. Mutates one of `{content, summary, label, is_active}`, runs `populate_external_references` for content/summary, writes a `NeuronRefinement(action="update")`, and back-fills `refinement_id`. Helpers `_apply_field_to_neuron` and `_skipped_audit` keep the handler under JPL-4.
- All three registered in `init_actions_registry()` alongside `eval.score.set`. Registry now reports: `['eval.score.set', 'neuron.create', 'neuron.refine', 'proposal.apply']`.
- Refactored `backend/app/routers/proposals.py:apply_proposal`:
  - Added `Depends(resolve_identity)` so the apply has a real `UserIdentity` to attribute actions to.
  - Submits a `proposal.apply` root action first; uses its `action_id` as `parent_action_id` for every child.
  - Per-item dispatch lives in extracted `_dispatch_proposal_items()` helper.
  - `create` items → `_submit_create_child` → `neuron.create` action.
  - `update` and `merge` items → `_submit_refine_child` → `neuron.refine` action (merge no longer needs its own helper — it was always implemented as a delegated update).
  - `rescale` and `link` items still call `_apply_rescale_item` / `_apply_link_item` directly. **Deferred to Step 3** (edge mutations + tiered-edge promotion logic).
- Deleted the now-unused `_apply_update_item`, `_apply_create_item`, and `_apply_merge_item` helpers from `proposals.py`. Git is the rollback.
- NASA linter: strict checks pass on every touched file. All Step 2 functions sit under the JPL-4 guideline (60 lines) after extracting helpers.
- Full pytest suite: 107/107 passing.
- Smoke-tested registry import + every Step 2 symbol can be imported cleanly under `TENANT_ID=corvus-aero`.

**Step 2 limitations (intentional):**
- `rescale` / `link` ProposalItem types still bypass the bus. They're the entire scope of Step 3, slice A.
- `review_proposal` (approve/reject the proposal *itself*) still mutates state directly — the approval state machine on `AutopilotProposal` predates the action bus. A future step can wrap that as a `proposal.review` action, but it's not essential for write-path coverage.
- Direct `db.add(Neuron(...))` calls in other routers (admin/corvus/ingest seed and ingestion paths) are not yet migrated — Step 3, slice B.

### 2026-04-09 — Pattern #1, Step 3: remaining bypass sites + edge handlers + AIP-1 lint
- Created two new action handlers under `backend/app/services/actions/`:
  - `edge_rescale.py` — wraps old `_apply_rescale_item` logic; checks if weight drops below tier promotion threshold and demotes from `neuron_edges` table if needed.
  - `edge_link.py` — wraps edge creation with tiered storage (promoted to `neuron_edges` table if weight ≥ threshold, otherwise stored in JSONB `weak_edges` column on the source neuron).
- Generalized `neuron_create.py` and `neuron_refine.py` handlers — `proposal_id` and `item_id` are now `Optional` so handlers work in both proposal and non-proposal contexts (e.g. observation approval, admin ingest, query-time refinement).
- Migrated all 9 remaining bypass sites across 4 routers:
  - `backend/app/routers/proposals.py` — `rescale` and `link` items now dispatch through `_submit_rescale_child` / `_submit_link_child` → `edge.rescale` / `edge.link` actions. Deleted the old `_apply_rescale_item` and `_apply_link_item` helpers.
  - `backend/app/routers/query.py` — `apply_refinements` routes updates through `neuron.refine` and creates through `neuron.create`. Added `Depends(resolve_identity)` to `evaluate_query` and `apply_refinements`.
  - `backend/app/routers/autopilot.py` — `_apply_neuron_update` rewritten to use `neuron.refine` action; `_create_neuron_from_spec` rewritten to use `neuron.create` action. Both dramatically shorter. Consolidated `_AUTOPILOT_ACTOR` at module level.
  - `backend/app/routers/ingest.py` — `approve_observation`, `_apply_selected_updates`, `_apply_selected_new_neurons`, and `_apply_merge` all route through appropriate actions. Module-level `_SYSTEM_ACTOR` for corvus observation paths.
  - `backend/app/routers/admin.py` — `_create_neurons_from_proposals` uses `neuron.create` action; `_create_referencing_edges` uses `edge.link` action. Module-level `_ADMIN_ACTOR` for admin ingest.
- Added AIP-1 enforcement rule in `scripts/nasa_lint.py`:
  - Regex detects `db.add(Neuron|NeuronRefinement|NeuronEdge|EvalScore(...))` outside action handlers.
  - Exempt paths: `services/actions/`, `seed/loader.py`, `services/concept_service.py`, `services/edge_tier.py`.
  - Guideline-level (warns, does not block commit) — prevents regression.
- Registry now reports 6 action kinds: `eval.score.set`, `proposal.apply`, `neuron.create`, `neuron.refine`, `edge.rescale`, `edge.link`.
- NASA linter: strict checks pass on all touched files; AIP-1 rule correctly fires on violations and exempts approved paths.
- Full pytest suite: 107/107 passing.
- Zero `db.add()` bypasses for governed types remaining in any router (grep verified).

**Step 3 limitations (intentional):**
- `review_proposal` (approve/reject the proposal itself) still mutates `AutopilotProposal.status` directly — wrapping this as a `proposal.review` action is deferred since it's metadata state, not governed model writes.
- Exempt paths (`seed/loader.py`, `concept_service.py`, `edge_tier.py`) are system infrastructure that operate at bootstrap or below the action layer.
- AIP-1 lint rule is guideline-tier, not strict — gives teams time to address any edge cases before hard-blocking.

**Pattern #1 complete.** All governed model types (Neuron, NeuronRefinement, NeuronEdge, EvalScore) now flow through the action bus. 6 registered action kinds, zero direct-write bypasses in routers, AIP-1 lint enforcement active.

### 2026-04-10 — Pattern #2: Bidirectional lineage + score overrides
- Enriched `NeuronFiring` table with 11 new columns: `rank`, `combined_score`, `burst`, `impact`, `precision`, `novelty`, `recency`, `relevance`, `spread_boost`, `prompt_position`, `was_included`. Every firing now captures the full score breakdown at query time — queryable, not buried in JSON blobs.
- Added `NeuronScoreOverride` table for manual graph tuning: per-neuron, per-signal floor/ceiling/multiplier. Unique constraint on (neuron_id, signal). Integrated into the scoring engine via `apply_score_overrides()` — applied after vectorized scoring, before sort. Enables direct manipulation of neuron behavior without touching neuron content.
- Modified `_update_counters_and_fire` in `executor.py` to pass `NeuronScoreBreakdown` data + rank + prompt position into `record_firing`. The `was_included` flag marks whether the neuron made it into the assembled prompt vs just being scored.
- Created `backend/app/routers/lineage.py` with 6 routes:
  - `GET /lineage/neuron/{id}/forward` — which queries used this neuron (with full score history)
  - `GET /lineage/source/{id}/impact` — which queries were influenced by a source document (end-to-end: SourceDoc → NeuronSourceLink → Neuron → NeuronFiring → Query)
  - `GET /lineage/query/{id}/trace` — full provenance trace for a query answer (fired neurons + scores + source documents + action counts)
  - `GET /lineage/neuron/{id}/overrides` — list score overrides
  - `POST /lineage/neuron/{id}/overrides` — create/upsert score override
  - `DELETE /lineage/overrides/{id}` — soft-delete override
- Created Alembic migration `008_enrich_firings_and_score_overrides.py` (applied to corvus_aero).
- Extracted `_assemble_top_slice` from `prepare_context` in executor.py to bring it under the JPL-4 100-line hard limit.
- Extracted `_load_trace_context` and `_override_to_out` helpers in lineage.py for JPL-4 compliance.
- NASA linter: zero strict violations on all touched files. Pre-existing guideline warnings unchanged.
- Full pytest suite: 107/107 passing.
- Smoke-tested lineage router import: 6 routes registered.

**Pattern #2 complete.** Forward chain is now queryable: for any query, you can trace back to fired neurons (with scores), their source documents, and the actions that created/refined them. Score overrides give direct tuning levers for the graph without touching neuron content.

### 2026-04-12 — Re-sequenced from three-leg GTM pivot
- **Context:** Strategic conversation clarified that Corvus sits behind customer LLM frontends (Fluent, Claude Enterprise, MCP clients) — three integration legs: (1) OpenAI-compatible tool-call endpoint, (2) remote MCP server, (3) Corvus admin UI. Captured in Master Corvus → Strategy → Positioning.
- **No code changes this entry.** Sequencing update only.
- **Decisions:**
  - Pattern #7 (runtime output policy gates) moved from Phase 2a → new Phase 1.5. Once external LLM frontends call Corvus, every response leaves the API into third-party context. Output gates must precede external surface hardening.
  - Added two non-AIP items as "GTM Surfaces": GTM-A (external tool-call endpoint `/v1/query`) and GTM-B (remote MCP transport + OAuth2). Both are infrastructure, gated by Patterns #2 and #7.
  - Pattern #3 remains next. After #3, sequence is #7 → GTM-A → GTM-B → Phase 2a/2b.
- **Files updated:** `ROADMAP-WORKLOG.md` (this file, checklist + next-session block), `~/.claude/plans/staged-booping-globe.md` (appended re-sequencing note), `master-corvus/src/components/system-docs/AIPGovernanceRoadmap.tsx` (new Phase 1.5 panel, GTM-Surfaces section, status indicators on every step).

### 2026-04-13 — Phase 1.5 delivered: #7 + GTM-A + GTM-B in one session

User directive: "Lets kick off with all in the priority you deem to result in the best quality output. We have to get all of it eventually, ready to go." Three items shipped together in the planned order (#7 → GTM-A → GTM-B) so the external surfaces land with gates already in place.

**Pattern #7 — Runtime output policy gates.** New `OutputViolation` model + Alembic migration `009_add_output_violations.py`. New `app.governance` package:
- `policies/__init__.py` with `PolicyContext`, `OutputRuleDraft`, and `OutputPolicy` protocol
- `policies/citation.py` — flags answers with zero `NeuronFiring.was_included=True` rows
- `policies/pii.py` — regex-based email/SSN/phone detection with redaction spans
- `policies/export_control.py` — tenant-configurable ITAR/EAR keyword block list
- `output_guard.py::run_guards(db, query_id, response_text, firings, actor)` — orchestrator that runs all enabled policies, applies redactions in-order, persists `OutputViolation` rows, and submits one `output.policy.check` action per invocation
- Wired into `routers/query.py` via `_run_output_gate(db, result, identity)` called right after `execute_query` — legacy `OutputCheckOut` eval-only path preserved alongside as `_legacy_output_checks`
- Per-tenant config via new `tenant.output_policies` property that reads `output_policies:` block from `tenant.yaml` (empty = all disabled)
- New `output.policy.check` action kind registered in `init_actions_registry()` — every guard run produces an audit row regardless of outcome

**GTM-A — External `/v1/query`.** New `routers/v1.py` behind `require_role("reader")`:
- `V1QueryRequest` / `V1ContextFragment` / `V1QueryResponse` schemas in `schemas.py`
- In-memory token-bucket rate limiter at `middleware/rate_limit.py` (`_MAX_KEYS=4096` JPL-2 bound, OrderedDict LRU, `asyncio.Lock`, `time.monotonic`)
- Per-tenant+user rate key `f"{tenant_id}|{user_id}"`; 429 on exhaustion
- `compact` mode returns `{answer, fragment_labels, lineage_id, eval_run_id, blocked, violations}`; `full` mode adds `fragments[]` with `neuron_id`, `label`, `snippet`, `source`, `combined_score`
- `lineage_id` = `NeuronFiring`-backed `query_id` from the executor; `eval_run_id` reserved as `None` until Pattern #3
- Reuses `_apply_output_guards` from `routers/query.py` via lazy import; blocking violations surface as HTTP 422 with `blocking_violations[]` so callers can treat them as hard failures
- New settings: `v1_rate_limit_capacity=30`, `v1_rate_limit_refill_per_sec=0.5`
- `_run_v1_pipeline` extracted as helper to stay under the JPL-4 60-line guideline

**GTM-B — Remote MCP HTTP+SSE.** New `app/mcp_http.py`:
- `session_manager = StreamableHTTPSessionManager(app=mcp._mcp_server, stateless=True, json_response=False)` — stateless mode so multi-worker deploys don't need sticky sessions
- `mcp_lifespan()` asynccontextmanager slotted into `app.main.lifespan` (SDK requires `session_manager.run()` be called exactly once)
- `MCPAsgiEndpoint` class with `async __call__(scope, receive, send)` — implemented as a class (not a function) so Starlette's `Route` detection treats it as a raw ASGI app rather than wrapping it via `request_response()`
- Mounted via `app.mount("/mcp", mcp_asgi_endpoint)` in `main.py`; RBAC gate via `_reader_identity_or_none(request)` before delegating to `session_manager.handle_request`
- `/mcp` (no trailing slash) → `RedirectResponse` 307 to `/mcp/` so both URLs work (Starlette `Mount` only matches the trailing-slash form)
- `_api_prefixes` in `main.py` extended with `"/v1"` and `"/mcp"` so the SPA catch-all doesn't intercept them

**Verification.**
- `backend/tests/test_output_guard.py` — 8 tests covering citation/PII/export_control policies plus the `run_guards` orchestrator (redact mutation, audit submission). All pass in 0.71s.
- Live smoke: `POST /v1/query` with a prompt matching the tenant's export-control term list returns HTTP 422 with `blocking_violations`; `POST /mcp/` `initialize` returns server capabilities; `tools/list` returns all 7 Corvus tools; bare `/mcp` returns 307 to `/mcp/`.
- NASA linter: strict checks pass on every new file. Guideline warnings only on pre-existing `query.py` functions untouched in this session.

**Phase 1.5 limitations (intentional).**
- `rate_limit.py` is in-memory per-process — fine for single-worker dev, will need Redis when we scale horizontally.
- OAuth2/JWT validation in `middleware/rbac.py` still depends on existing `azure_ad` mode; no new identity-provider code was added (deferred until customer #1's IdP is known — likely Azure AD GovCloud).
- Progressive tool disclosure for GTM-B (`corvus_catalog` as single entry tool) is deferred — all 7 tools are exposed in the HTTP transport just as they are in stdio.
- `eval_run_id` in `V1QueryResponse` is always `None` until Pattern #3 lands.

**Files updated.** `backend/app/models.py`, `backend/app/config.py`, `backend/app/schemas.py`, `backend/app/main.py`, `backend/app/tenant.py`, `backend/app/routers/query.py`, `backend/app/services/actions/__init__.py`, `backend/tenants/corvus-aero/tenant.yaml`, `master-corvus/src/components/system-docs/AIPGovernanceRoadmap.tsx` (#7, GTM-A, GTM-B statuses flipped `pending` → `done`).
**Files added.** `backend/app/governance/` (package), `backend/app/routers/v1.py`, `backend/app/middleware/rate_limit.py`, `backend/app/mcp_http.py`, `backend/app/services/actions/output_policy_check.py`, `backend/alembic/versions/009_add_output_violations.py`, `backend/tests/test_output_guard.py`.
