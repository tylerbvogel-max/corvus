# Corvus AIP Governance Roadmap — Worklog

**Plan source:** `~/.claude/plans/staged-booping-globe.md`
**UI view:** Master Corvus → Roadmaps → ★ AIP Governance
**Dev URL:** http://localhost:5175/

This file is the canonical session handoff for the AIP governance roadmap. At the start of each session, read this first to see where we are and what's next. At the end of each session, update the Current position, Next session starts here, and Session log sections.

## Current position

**Phase:** Phase 1 — Dual-purpose foundations
**Completed item:** #2 Bidirectional lineage / active provenance graph — **backend complete**
**Next item:** #3 Evals as immutable, first-class artifacts

## Next session starts here

> Pattern #2 backend is complete. If frontend lineage visualization is needed, build it as part of the Phase 3 governance view.
>
> Read this worklog, then read `~/.claude/plans/staged-booping-globe.md` pattern #3 for full context. Begin scoping by:
>
> 1. Read `backend/app/models.py` — `EvalScore` exists but slot results are JSON blobs in `Query.results_json`. No frozen eval run artifact.
> 2. Design an `EvalRun` table that snapshots {query set, model versions, scoring-engine version, results, verdicts}. Append-only.
> 3. The `NeuronScoreOverride` system (added in Pattern #2) gives manual tuning levers — eval runs should capture which overrides were active at run time.
>
> Pattern #2 delivered: enriched firing records (full score breakdown per query), 3 lineage endpoints (neuron forward, source impact, query trace), NeuronScoreOverride for manual graph tuning integrated into the scoring engine. End-to-end traceability: answer → firings → neurons → sources → actions.

## Checklist

### Phase 1 — Dual-purpose foundations
- [DONE] #1 Actions as the universal write primitive
- [DONE] #2 Bidirectional lineage / active provenance graph
- [ ] #3 Evals as immutable, first-class artifacts

### Phase 2a — Defense-sale track
- [ ] #4 Row-level markings / classification
- [ ] #7 Runtime output policy gates

### Phase 2b — Correctness / experimentation track
- [ ] #5 Typed pipeline DAG with observable stages
- [ ] #6 Ontology branching for safe experimentation
- [ ] #8 Unified autopilot as a typed agent

### Phase 3 — Converge
- [ ] Governance view in frontend (audit log, approval queue, lineage graph, eval history, policy violations)
- [ ] Pull forward the unused track's highest-value item

## Open questions / blockers

- None yet.

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
