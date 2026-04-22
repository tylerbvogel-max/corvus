# Corvus AIP Governance Roadmap — Worklog

**Plan source:** `~/.claude/plans/staged-booping-globe.md`
**UI view:** Master Corvus → Roadmaps → ★ AIP Governance
**Dev URL:** http://localhost:5175/

This file is the canonical session handoff for the AIP governance roadmap. At the start of each session, read this first to see where we are and what's next. At the end of each session, update the Current position, Next session starts here, and Session log sections.

## Current position

**Phase:** Phase 1 complete (Patterns #1, #2, #3). Phase 1.5 shipped. Phase 4 first co-ship closed 2026-04-20; UI fold + polish shipped 2026-04-21. Phase 2b started 2026-04-21 with Pattern #5 (typed pipeline DAG with observable stages) landing same day. Second agent wave (#203/#204/#205) remains gated on Pattern #6 (ontology branching). Phase 2a deferred to tail (customer-triggered).
**Completed items:** #1 Action bus, #2 Lineage + score overrides, #3 Immutable eval artifacts, Phase 1.5 (#7 output gates, GTM-A /v1/query, GTM-B remote MCP), Phase 4 first co-ship (#201 agent registry, #202 dedup agent, #208 agent visibility — folded into Integrity 2026-04-21), #5 Typed pipeline DAG (2026-04-21).
**Active items:** none — between work units. Next action is to choose the next Phase 2b item (#6 or #8) or pivot to a different track (see "Next session starts here").
**Next item:** a branching choice — (A) Pattern #6 ontology branching (unblocks second agent wave #203/#204/#205 and is Phase 2b's next correctness item); (B) Pattern #8 autopilot-as-typed-agent (builds directly on #5's stage primitive); (C) Phase 2a #4 row-level markings, only if a customer conversation forces defense-sale / CUI handling.
**Revised sequence active as of 2026-04-12** — see "Revised sequencing" below. Phase 1.5 was pulled ahead of #3 in the 2026-04-13 session on explicit user direction; Pattern #3 closed 2026-04-16. Phase 2a pushed to tail 2026-04-20; Phase 4 brought up as parallel track off 1.5 (rationale: agents have long-running behavioral impacts, need them in the continuous-eval loop early so the corpus accumulates with them in scope). First Phase 4 co-ship closed 2026-04-20; UI fold + cross-surface polish landed 2026-04-21.

### Pre-agent eval baseline (locked 2026-04-20)

Frozen comparison point before Phase 4 agents land. Any post-agent drift is measured against this row.

- **Tenant:** corvus-aero
- **certified_eval_run_id:** 3
- **suite_name:** smoke
- **suite_hash:** 5212806f355e0ed05f2b… (see `eval_runs.id=3`)
- **scoring_engine_version:** 1.0.0
- **certified_at:** 2026-04-17 03:56:22 UTC
- **certified_by:** tester
- **status:** completed

How to use: when re-certifying a post-agent run, compare `eval_runs.summary_json` deltas (pass_rate, severity_counts) against run #3. Any regression on pass_rate or increase in high-severity violations is attributable (at least partially) to Phase 4 agent behavior and warrants rollback flag flip or targeted investigation.

## Revised sequencing (2026-04-12)

The three-leg GTM pivot (tool-call endpoint + remote MCP + admin UI, detailed in Master Corvus → Strategy → Positioning) changes priority ordering downstream of Pattern #3. Rationale: once Corvus sits behind a customer's LLM frontend (Fluent, Claude Enterprise, MCP clients), every answer leaves the API into a third-party context. That surface needs runtime output gates *before* anything else ships.

**Changes vs. original:**
1. **Pattern #7 (runtime output policy gates) moved from Phase 2a to a new Phase 1.5.** It's now a GTM blocker, not a defense-track item. Small surface, sibling of `input_guard.py`, unblocks the tool-call and MCP surfaces being safe to ship externally.
2. **Two new non-AIP items added:** external tool-call endpoint hardening and remote MCP server (HTTP+SSE + OAuth2). These are infrastructure, not governance patterns, but they're gated by #2 (lineage_id in responses) and #7 (output gates). Tracked as "GTM Surfaces" alongside the AIP patterns.
3. **Patterns #4, #5, #6, #8 unchanged.** Phase 2a still defense-track (now just #4), Phase 2b still correctness (#5, #6, #8).

## Next session starts here

> Pattern #5 (typed pipeline DAG with observable stages) shipped 2026-04-21. `prepare_context` in `backend/app/services/executor.py` now delegates to a new `app/services/pipeline/` package: 8 named stages (`structural_resolve → classify → prefilter_score → continuity_boost → spread_activation → inhibitory → regulatory_resolve → assemble_prompt`) threaded through a mutable `PipelineState`, wired to a runner that times each stage with `time.perf_counter`, records a `StageTelemetry` row into `PipelineContext`, and hard-fails any stage exception as `PipelineStageError(stage_name, original)`. Telemetry is persisted to a new JSONB column `queries.stage_telemetry_json` (alembic `011_stage_telemetry`) and returned on `QueryResponse.stage_telemetry` + a `failed_stage` field on error. `/query`, `/query/stream` (SSE error event includes `failed_stage`), and `/v1/query` all catch `PipelineStageError` cleanly. Query Lab renders server-authoritative per-stage durations, a `StageTelemetryTable` (score-table styled) under a new "Pipeline Telemetry" step, and a failed-stage banner above the error message. Unit tests for the runner: `tests/test_pipeline_runner.py` (5 tests). Full suite: 153 pre-existing + 5 new, all green. NASA lint strict-clean.
>
> Next session picks one of three tracks:
>
> - **Track A — Pattern #6 ontology branching → second agent wave (#203/#204/#205).** The plan explicitly marks #6 as a soft prereq for the second wave so agent mutations can be A/B tested against a no-agent control branch. Highest-leverage path if the goal is to keep the agent initiative moving. Cost: introduces branch-scoped identifiers across neurons/edges/proposals and a merge semantics decision (additive vs. overwrite on merge).
> - **Track B (continued) — Phase 2b correctness: #8 autopilot-as-typed-agent.** Pattern #5's stage primitive is now the natural building block. Autopilot currently operates as a set of cron-fired procedures; rewriting it as typed stages + a `Pipeline` reuses the same telemetry surface the Query Lab just gained.
> - **Track C — Phase 2a #4 row-level markings / classification.** Only if a customer conversation surfaces ATO / classified / CUI requirements. Without that signal, speculative hardening.
>
> Defaults: recommend Track A next — #6 is the last remaining soft prereq for the second agent wave, and #8 can follow it without friction since both benefit from branch-scoped IDs. Avoid Track C absent customer signal.
>
> Suggested prep: `git log --since='2026-04-17' --oneline` inside ~/Projects/corvus to see the exact Phase 4 co-ship + follow-up commits. Read `~/.claude/plans/staged-booping-globe.md` Pattern #6 section for the ontology-branching design sketch before starting Track A.
>
> For the historical context of why Pattern #3 chose append-only-at-three-layers and snapshot-in-the-row over the simpler alternatives, read the `2026-04-16 — Pattern #3` session-log entry above. Same for the Phase 1.5 philosophy (`2026-04-13`) and Phase 4 sequencing (`2026-04-20`, `2026-04-21`).

## Checklist

### Phase 1 — Dual-purpose foundations
- [DONE] #1 Actions as the universal write primitive
- [DONE] #2 Bidirectional lineage / active provenance graph
- [DONE] #3 Evals as immutable, first-class artifacts

### Phase 4 — Agentic Maintainers (first co-ship closed 2026-04-20; UI fold 2026-04-21)
- [DONE] #201 A-Base — agent registry + tool allow-list + tool_base
- [DONE] #202 A-Dedup — dedup / proposal curator agent
- [DONE] #208 A-UI — agent visibility (folded into IntegrityPage 2026-04-21)
- [ ] #203 A-Autopilot — gated on Pattern #6
- [ ] #204 A-Integrity — gated on Pattern #6
- [ ] #205 A-Ingest — gated on Pattern #6
- [proposed] #206 A-Screen / #207 A-GapGen / #209 A-Ops — deferred per gates in plan

### Phase 1.5 — GTM gate (NEW, 2026-04-12; shipped 2026-04-13)
These unlock shipping tool-call and MCP surfaces externally. Ordered. Closed.
- [DONE] #7 Runtime output policy gates (moved up from Phase 2a)
- [DONE] GTM-A: External tool-call endpoint hardening (`/v1/query` with auth, rate limiting, `{answer, fragment_labels, fragments, lineage_id, eval_run_id, blocked, violations}` response contract; `eval_run_id` populated once Pattern #3 landed)
- [DONE] GTM-B: Remote MCP server (HTTP+SSE transport via `StreamableHTTPSessionManager`; RBAC gate reused from `/v1/query`)

### Phase 2a — Defense-sale track
Pull forward if customer conversation / ATO question / classified-data requirement surfaces.
- [ ] #4 Row-level markings / classification

### Phase 2b — Correctness / experimentation track
Pull forward if pipeline iteration speed is the near-term pain.
- [DONE] #5 Typed pipeline DAG with observable stages (shipped 2026-04-21)
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
8. **SAVEPOINTs and self-committing subroutines don't compose.** Wrapping `execute_query` in `db.begin_nested()` errored because `execute_query` commits internally, closing the savepoint before the `async with` block exits. Lesson: per-item isolation can be `try/except` around a boundary function when that function owns its own transaction — reach for SAVEPOINTs only when you own the whole transaction scope.
9. **Append-only at the router and test layer, not just the schema.** Pattern #3's certification claim is only as strong as its weakest mutation surface. Adding `test_eval_runs_router_has_no_mutation_routes` catches "someone adds a PATCH endpoint later" as a test failure rather than a vigilance-dependent review. Mix schema invariants with test invariants when append-only semantics matter.
10. **Snapshot-in-the-row beats FK-to-mutable-source for frozen artifacts.** `NeuronScoreOverride` rows are editable; `tenant.yaml` suites are editable. If `EvalRun` had FK'd those, "what did this run actually test?" would silently decay. Storing denormalized snapshots (model_versions, overrides_snapshot, suite_hash) as JSONB costs a few KB per row and buys permanent answerability.

## Session log

### 2026-04-21 — Pattern #5: typed pipeline DAG with observable stages
- User directive: "lets go with just 5; consider all UI components in scope; hard fail for v1."
- New package `backend/app/services/pipeline/`:
  - `stage.py` — `Stage(Protocol, Generic[IN, OUT])` with `name: str`, `run()`, `describe()`; `StageTelemetry` dataclass (stage / status / duration_ms / detail / error_message); `PipelineStageError(stage_name, original)` and `ShortCircuit(final_value)` control-flow exceptions.
  - `context.py` — `PipelineContext` holds `db`, `on_stage` callback (reused by `/query/stream`), `telemetry` list, and `metadata` scratch space.
  - `runner.py` — `run_pipeline(stages, initial_input, ctx)` iterates stages under `time.perf_counter`, appends a `StageTelemetry` row per stage, emits an `on_stage` event, catches `ShortCircuit` (returns `sc.final_value`), wraps any other exception as `PipelineStageError` with original as `__cause__`. JPL-2 bounded loop, JPL-5 precondition assertions.
  - `state.py` — single mutable `PipelineState` dataclass threading fields across stages (pragmatic, documented in docstring vs. pure functional dataflow).
  - `stages/` — 8 concrete stages extracted from the old `prepare_context`: `structural_resolve` (raises `ShortCircuit(PreparedContext)` on a structural hit, zero-cost path), `classify`, `prefilter_score`, `continuity_boost` (1.3× multiplier for `prior_neuron_ids`, re-sort), `spread_activation` (guards `ensure_adjacency_loaded`), `inhibitory`, `regulatory_resolve`, `assemble_prompt`.
- `services/executor.py` — `prepare_context` now ~40 lines; builds `PipelineState`, calls `run_pipeline(build_default_pipeline(), state, ctx)`, maps back to `PreparedContext`. `stage_telemetry` threaded through `_create_query_record` (persisted) and `_build_response` (returned).
- Schema: `models.py` gains `Query.stage_telemetry_json: JSONB | None`. Alembic `011_stage_telemetry` (applied to both corvus-aero and corvus-flow DBs). `schemas.QueryResponse` gains `stage_telemetry: list[StageTelemetryOut]` + `failed_stage: str | None`.
- Hard-fail wiring — all three external surfaces catch `PipelineStageError` and format failures with a `failed_stage` field:
  - `routers/query.py::post_query` → HTTP 500 body `{message, failed_stage, cause}`.
  - `routers/query.py::post_query_stream` → SSE `error` event `{message, failed_stage, cause}`.
  - `routers/v1.py::_run_v1_pipeline` → HTTP 500 identical shape.
- Frontend:
  - `types.ts` — new `StageTelemetry` interface; `QueryResponse` gains `stage_telemetry` + `failed_stage`.
  - `api.ts::submitQueryStream` — SSE error handler now attaches `failed_stage` to the thrown Error.
  - `components/QueryLab.tsx` — tracks `failedStage` state, merges server-authoritative `stage_telemetry[].duration_ms` into `stageTimes` (replacing SSE-gap estimation), renders a failed-stage banner above the error message, adds a new "Pipeline Telemetry" pipeline step with `StageTelemetryTable` (score-table styled, IntegrityPage design language: stage name / status badge / duration / per-stage detail summary).
- Verification:
  - `python3 scripts/nasa_lint.py backend/app/services/pipeline/ backend/app/services/executor.py backend/app/routers/query.py backend/app/routers/v1.py backend/app/models.py backend/app/schemas.py` → strict clean; pre-existing JPL-4 guideline warnings only.
  - `npx tsc --noEmit -p tsconfig.app.json` → no new errors (two pre-existing unchanged).
  - `tests/test_pipeline_runner.py` — 5 unit tests (chain + telemetry, ShortCircuit skip, PipelineStageError wrapping, on_stage event emission, JSON serialization). All green.
  - Full backend suite: 153 pre-existing + 5 new = 158 tests, all green.
  - Live smoke on `:8002`:
    - `POST /query` `"How should I organize a production scheduling team?"` → 200; response envelope includes all 8 stages in `stage_telemetry`.
    - `POST /v1/query` same prompt → 200 with full answer.
    - DB readback (`queries.stage_telemetry_json`) — 8 rows persisted for query_id 449, timings matching the runner's per-stage `perf_counter` deltas.
- Decisions worth preserving:
  - **Mutable `PipelineState` over pure functional dataflow.** Each stage mutates state fields relevant to its step. The alternative — each stage returning a new immutable state — would have required every stage to reconstruct a ~20-field dataclass and produced trivial hot-path copies. Pipeline correctness is asserted by JPL-5 preconditions in the runner + per-stage type hints, not by immutability.
  - **Hard-fail over graded-degrade for v1 (per user).** `PipelineStageError(stage_name, original)` surfaces the exact failed stage to the caller. Graded-degrade (e.g., skip regulatory_resolve and proceed) was considered and deferred — it's a Pattern #5.5 enhancement that requires per-stage "criticality" annotations and a downstream assembler that tolerates missing inputs.
  - **JSONB snapshot on `queries` row vs. a new `StageTelemetryRow` table.** ~8 stages × ~100 bytes per row is well within JSONB's ergonomic range, queries are always row-scoped ("show me the telemetry for query X"), and no cross-query analytics justified the extra table + FK. If dashboards later want per-stage aggregates, a materialized view over the JSONB is straightforward.
- Deferred:
  - Per-stage "criticality" annotations for graded-degrade behavior.
  - Pipeline builder DSL / dynamic stage insertion (current `build_default_pipeline()` is hard-coded; fine for v1).
  - Prometheus histograms for stage durations (the JSONB row + `/admin/query/{id}` readback is enough for now; observability tooling lands with Pattern #6's experimentation harness).

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

### 2026-04-16 — Pattern #3: Evals as immutable, first-class artifacts

Ships the missing link between Pattern #1 (action-bus audit trail) and Pattern #2 (lineage). Patterns #1 + #2 answer "what happened to the graph and which answer came from it?" Pattern #3 answers the *quality* question: "was the pipeline certified to produce that answer?"

**Core design philosophy — an eval run is a frozen certificate, never a mutable scoreboard.**

Four design decisions drove the implementation; each has a deliberate rejection of a simpler but weaker alternative.

1. **Append-only at three layers, belt-and-suspenders.**
   A certified run must be unforgeable after the fact. Enforcement is triplicate:
   - **Schema layer** — the `EvalRun` row's `status` only ever transitions `running → completed|failed` once. No route writes back to it afterwards.
   - **Router layer** — `routers/eval_runs.py` deliberately exposes only `POST /runs`, `GET /runs`, `GET /runs/{id}`, `POST /runs/{id}/certify`. No `PUT`, `PATCH`, or `DELETE`. A pytest assertion (`test_eval_runs_router_has_no_mutation_routes`) makes regressions a test failure, not a code-review catch.
   - **Action layer** — `eval.run.start` and `eval.run.complete` are the only registered action kinds for this domain. Neither handler mutates the eval run; they are pure audit markers. The `complete` action's `parent_action_id` points at the `start` action, so the audit trail reads as a single bracketed lifecycle without needing a dedicated state machine.
   Alternative rejected: a single `eval.run.update` action. Simpler, but it would let an operator revise a run after the fact, destroying the certification claim.

2. **Snapshot *into* the row, don't dereference *through* it.**
   When a run completes, the following are frozen inline on the `EvalRun` row as JSONB: `model_versions` (every entry in `MODEL_REGISTRY` at run time), `overrides_snapshot` (every active `NeuronScoreOverride` row), `suite_hash` (sha256 over normalized case list), and `scoring_engine_version` (module constant, bump-on-change). Any of these could instead be a foreign key — but foreign keys point at *mutable* rows. A `NeuronScoreOverride` row is `is_active`-flagged and editable; the hash of the suite YAML changes the moment someone edits the file. If the eval run stored FKs, "what did this run actually test" would decay the instant anything upstream moved. Storing the denormalized values makes the row self-describing forever.
   Cost: storage (~1–5 KB per row of JSONB). Accepted — eval runs are low-volume.

3. **Certification is a tenant-level pointer, not per-model or per-query.**
   `TenantConfig.certified_eval_run_id` is a singleton (id=1) with a single FK to the currently certified `EvalRun`. `/v1/query` reads it once per request and stamps the id on the response. Two alternatives were considered and rejected:
   - **Per-model certification** — separate pointers for Haiku vs Sonnet vs provider tiers. Rejected because the eval already snapshots every model version; if the certified run tested the active model set, it covers the pipeline. Reintroduce only if model routing starts bypassing the measured stack.
   - **Per-query matching** — `/v1/query` looks up the latest eval run whose `suite_hash` matches some query taxonomy. Rejected as overengineered — Corvus isn't yet a multi-suite shop; one smoke suite certifies the whole pipeline. Revisit when suite count > 1 per tenant.
   Current model's virtue: the attestation is trivially readable ("at time T, customer X was served pipeline with certified_eval_run_id = N"). Easy to audit, easy to revoke (point at `null` to decertify without deleting history).

4. **Per-case failure isolation via try/except, not SAVEPOINTs.**
   First iteration wrapped `_execute_case` in `async with db.begin_nested():` so one failing case couldn't roll back the whole run. This collided with the fact that `execute_query` manages its own transaction and commits internally via `_finalize_query_row`. The commit closed the savepoint, then `__aexit__` errored with `"Can't operate on closed transaction inside context manager"` — every case failed. Fix: remove the SAVEPOINT, rely on try/except around the case body. A per-case exception records `error_message` on the `EvalRunCase` row without affecting siblings. The run-level outcome (`completed` vs `failed`) is still partition-safe: `failed` is only raised when *every* case errored — otherwise a partial-failure run remains a reviewable artifact with a lower `pass_rate`. See `eval_runs.py:131-182` for the current shape; the rationale is in the function docstring for the next person who reaches for `begin_nested`.

**Verification — end-to-end against live pipeline.**
- Live run: `POST /admin/eval/runs {"suite_name":"smoke"}` against corvus-aero ran 5 cases in 213s end-to-end, returned HTTP 201. Run id=3, status=`completed`, `summary={total:5, blocked:1, errors:0, violation_count:2, severity_counts:{error:1, critical:1}, pass_rate:0.8}`. The one blocked case (`itar-subcontract`) was the output guard correctly firing — expected signal, not a regression.
- DB state verified: every case row has `query_id`, `lineage_id`, `response_text` (1330–7137 chars), and `blocked` flag populated. No null rows, no orphans.
- Action trail verified: `eval.run.start` (id=57) and `eval.run.complete` (id=60, `parent_action_id=57`). Lifecycle brackets the run cleanly.
- Certify flow: `POST /admin/eval/runs/3/certify` returned `{eval_run_id: 3, certified_at, certified_by: "tester"}`. Subsequent `POST /v1/query` returned `eval_run_id: 3` in the response envelope — the certification pointer is live in the external contract.
- Full pytest suite: **126/126 passing** (11 new tests in `test_eval_runs.py`). NASA linter: clean on all touched files.

**Pattern #3 limitations (intentional).**
- No automatic re-certification — when the codebase changes, the certified run doesn't invalidate itself. Operators are expected to re-run + re-certify. A stretch goal: compare active `scoring_engine_version` against certified run's version and flag mismatch in `/v1/query` metadata.
- Suite YAML format is intentionally minimal (`label` + `text|query`). No expected-output field yet; pass/fail is derived from the output guard + error state. When we have more graded criteria (citations present, neuron count in range, latency bound), extend the schema rather than bolt on external scoring.
- No scheduled re-runs. Certification is fully operator-driven. Worth adding a nightly autopilot job once the suite size warrants it.
- `scoring_engine_version` exists in the payload but is a manually-bumped module constant. Tying it to a git-derived hash would be stronger; deferred until we have CI infrastructure to drive it.

**Files updated.** `backend/app/models.py` (3 new ORM classes — `EvalRun`, `EvalRunCase`, `TenantConfig`), `backend/app/services/actions/__init__.py`, `backend/app/services/actions/init_registry.py` (2 new action kinds registered), `backend/app/routers/v1.py` (stamps certified `eval_run_id`), `backend/app/main.py` (router include), `frontend/src/api.ts` (4 new client functions), `frontend/src/App.tsx` (new nav item), `master-corvus/public/roadmap-state.json` (24-step verification checklist on `gov-aip-pattern3` node), `master-corvus/src/components/system-docs/AIPGovernanceRoadmap.tsx` (status flipped `next` → `done`).
**Files added.** `backend/app/services/eval_runs.py`, `backend/app/routers/eval_runs.py`, `backend/app/services/actions/eval_run_lifecycle.py`, `backend/alembic/versions/010_add_eval_runs.py`, `backend/tenants/corvus-aero/eval_suites/smoke.yaml`, `backend/tenants/corvus-flow/eval_suites/smoke.yaml`, `backend/tests/test_eval_runs.py`, `frontend/src/components/EvalRunsPage.tsx`.

**Phase 1 complete.** Patterns #1, #2, #3 shipped. The full governance loop is now: every write is an action → every firing is traceable to a query → every answer carries a certified eval_run_id proving which pipeline produced it. Next: re-evaluate sequencing (Phase 2a vs 2b) once customer traffic through Phase 1.5 surfaces lands real-world signal.

### 2026-04-20 — Phase 4 first co-ship: #201 A-Base + #202 A-Dedup + #208 A-UI

Deliberate co-ship per the plan — the agent registry (#201) is infrastructure that needs at least one agent (#202) and customer-facing visibility (#208) landing alongside it, or the registry sits unused and the feature is invisible.

**What shipped.**

- **#201 A-Base — agent registry + tool allow-list.** New package `backend/app/agents/` with `registry.py` (loads `tenants/{id}/agents/*.yaml` at startup, validates schema, rejects malformed files with a clear error), `runtime.py` (drives a bounded agent-loop via the Claude CLI subprocess, scrubbing `CLAUDECODE*` / `CLAUDE_CODE_*` env per the nested-session gotcha), `tool_base.py` (abstract tool protocol — `name`, `schema`, `invoke`), and `tools/dedup_tools.py` (first concrete tool pair). Tool allow-list is enforced *before* the LLM sees a tool call: an agent with `tools: [mark_duplicate]` attempting `write_neuron` raises `ToolNotAllowedError` from the runtime, never reaching the model. Synthetic actor identity: every action the agent emits carries `actor_id="corvus-agent-{name}"` (e.g. `corvus-agent-dedup`), so the audit trail cleanly separates agent writes from human writes without a schema change.
- **#202 A-Dedup — dedup / proposal curator agent.** `backend/tenants/corvus-aero/agents/dedup.yaml` defines the first agent: goal (cluster near-duplicate integrity-proposed findings), allow-listed tools (`mark_duplicate`, `mark_reviewed_as_unique`), model (Haiku), turn limit (bounded loop). The two tools record `Action` rows whose `input_json` carries `finding_id` and whose `parent_action_id` points at the agent-run root action — that parent-child relationship is the join key the UI uses to reverse-link from a proposed finding back to the run that auto-proposed it. Closes the open TODO at `proposals.py:152`.
- **#208 A-UI — agent visibility.** `/v1/agents`, `/v1/agents/{name}/run` (trigger), `/v1/agents/runs/{id}` (detail with tool-call trace) endpoints. Initial shape was a standalone `AgentsPage.tsx` under the Autopilot nav group — worked, but visually inconsistent with the rest of the graph-quality surfaces, and conceptually redundant with IntegrityPage (which already surfaced near-duplicate / contradiction / missing-connection findings). See the 2026-04-21 entry for the fold.

**Design decisions locked in.**

- **Agents are never on the query hot path.** Pattern #201's runtime refuses to wire into `/query` or `/v1/query`. Agents run on explicit trigger (or, later, scheduled) and their writes go through the proposal queue, same as autopilot.
- **Synthetic actor identity over a new column.** Considered adding `is_agent: bool` to `actions`. Rejected — the `actor_id` string is already denormalized enough. `corvus-agent-*` prefix is the convention; no schema change needed to add agents 3–N.
- **Tool allow-list enforced outside the LLM loop.** A tempting alternative is to let the model call whatever, then reject at the DB boundary. Rejected — that leaks capability surface into LLM context and spends tokens on forbidden calls. Allow-list is a pre-flight check in `runtime.py` before the tool call reaches the model's view.

**Verification.**

- Registry load: `pytest tests/test_agent_registry.py -v` — 6 tests green (load, missing fields, bad tool name, reload).
- Allow-list: unit test confirms `ToolNotAllowedError` before any LLM call when agent attempts non-allowed tool.
- Dedup accuracy: 50-item hand-labeled gold set (stored at `backend/tests/fixtures/dedup_gold.json`) — agreement 94% vs labels.
- Audit trail: `SELECT user_id, COUNT(*) FROM actions WHERE user_id LIKE 'corvus-agent-%' GROUP BY user_id;` shows agent actions cleanly partitioned.
- Pre-agent eval baseline locked at `eval_runs.id=3` on corvus-aero (see "Current position" above) so post-agent drift has a comparison point.

**Pattern #201 + #202 + #208 complete.** First agent is live, visible, and auditable.

### 2026-04-21 — Phase 4 follow-up: Agents folded into Integrity + cross-surface polish bundle

The standalone `AgentsPage` shipped the day before worked but sat awkwardly next to Integrity — both surfaces fundamentally do the same thing (detect graph defects → review → propose → approve). The fold collapsed them into one workbench, then a second pass addressed cross-surface navigation friction the unified view exposed.

**Agents → Integrity fold.**

- Deleted `frontend/src/components/AgentsPage.tsx` and removed the Autopilot → Agents nav entry + `'agents'` tab state.
- IntegrityPage now has three panels (unchanged names) with expanded roles:
  - **Dashboard** carries the unified "Recent runs" table — rows are a chronological union of `IntegrityScan` and `Action(kind='agent.run')`, sorted by completion time. Click a row → detail modal with scan parameters (for scan rows) or tool-call trace (for agent rows). New `GET /admin/integrity/runs` backend endpoint merges the two sources into a single `RunRowOut` shape.
  - **Scan** gained an "Available agents" card above the existing scan tiles. "Run now" triggers `/v1/agents/{name}/run`, toasts, auto-switches to Dashboard so the new row is visible.
  - **Findings Queue** detail pane gained a reverse-link banner: any finding whose proposal was created by an agent shows "Auto-proposed by dedup (run #X)" → click opens the same run-detail modal. Implemented via reverse Action-Bus lookup on `agent.tool.mark_duplicate` / `agent.tool.mark_reviewed_as_unique` rows filtered by `finding_id` — exposed on the finding-detail response as `created_by_agent_run_id` + `created_by_agent_name`.

**Cross-surface polish bundle.** Five producer pages (Autopilot, Emergent, Document Ingest, Integrity, Query Lab) all feed the Proposal Queue. The unified view made the lack of navigation feedback between them obvious. Rather than reorganize the nav hierarchy (which the user explicitly rejected as over-kill), the lighter alternative: teach existing pages + the queue to talk to each other.

- **Per-origin pending count badges on nav items.** `/stats` now returns `proposed_by_origin: {autopilot: N, integrity: M, document_ingest: K, emergent: J, query_lab: L}` computed at request time via a projection over `AutopilotProposal(state='proposed')` rows. App.tsx polls `/stats` every 30s (gated on auth) and renders a chip on each producer nav item — clicking the chip deep-links to a pre-filtered Proposal Queue (forward navigation).
- **Origin filter in Proposal Queue.** The queue already grouped by origin client-side; upgraded to a server-recognized filter parameter so deep-links land pre-filtered without a second client render.
- **Reverse deep-links from Proposal Queue back to producer pages.** `ProposalOut` now carries `finding_id` and `scan_id` (extracted from `gap_evidence_json[0]` server-side — not Python post-processing — so the values are queryable). The queue detail pane renders a "from integrity (finding #123) →" chip that routes back to IntegrityPage → Findings Queue with the finding pre-selected.
- **Shared j/k keyboard navigation hook.** Extracted the j/k handler previously inline in ProposalQueuePage into `frontend/src/hooks/useListKeyboardNav.ts`. Generic over any `Identifiable` list, guards against typing targets (input/textarea/contentEditable), skips when `nextIdx === curIdx` (so pressing j on the last row doesn't accidentally collapse a toggle-style selection handler — bug caught during the AutopilotPage wiring). Now used by ProposalQueuePage, IntegrityPage FindingsPanel, and AutopilotPage, giving all three surfaces parity navigation without duplicating code.

**Backend changes.**

- `routers/integrity.py` — new `GET /admin/integrity/runs` endpoint; `GET /admin/integrity/findings/{id}` enriched with `created_by_agent_run_id` + `created_by_agent_name`.
- `routers/proposals.py` — `_classify_origin` precedence fix (source-string prefix now beats `autopilot_run_id` when both present, matching the actual origin semantics); new `emergent` origin classifier; `_extract_source_ids()` helper projects `finding_id`/`scan_id` onto `ProposalOut`; `_apply_origin_filter()` used by the list endpoint; `/stats` computes `proposed_by_origin`.
- `schemas.py` — `ProposalOut` gains `finding_id: int | None`, `scan_id: int | None`; `ProposalStatsOut` gains `proposed_by_origin: dict[str, int]`.

**Frontend changes.**

- New `frontend/src/hooks/useListKeyboardNav.ts` (j/k parity hook).
- `components/IntegrityPage.tsx` — three-panel fold (Dashboard run-union table + run-detail modal, Scan available-agents card, Findings Queue reverse-link banner) + j/k hook wiring.
- `components/ProposalQueuePage.tsx` — exported `OriginFilter` type; new `initialOriginFilter` + `onNavigateToProducer` props; producer-chip rendering; j/k hook replaces inline handler; `emergent` added to `OriginFilter` / `ORIGIN_COLORS` / `SOURCE_OPTIONS`.
- `components/AutopilotPage.tsx` — j/k hook wiring with a non-toggling `navigateToRun` callback (since `handleExpandRun` toggles selection, using it as the hook callback would collapse on j-on-selected).
- `App.tsx` — deleted AgentsPage import/route; added `ORIGIN_TO_TAB` + `TAB_TO_ORIGIN` maps; new `proposedByOrigin` / `totalProposed` / `queueInitialOrigin` state; 30s `/stats` polling; `navigateToProducer` and `navigateToFilteredQueue` callbacks; extended nav-item render with pending-count badge chip.
- `api.ts` — `ProposalSummary` gains `finding_id` / `scan_id`; `ProposalStats` gains `proposed_by_origin`.

**Lessons.**

1. **Co-shipped visibility beats sequential shipping.** If #208 (customer-facing agent visibility) had been deferred to a later session, #202's audit trail would have sat behind a SQL prompt for days and no one would have validated the visibility story end-to-end. The 2026-04-21 fold was easier *because* the standalone AgentsPage existed for a day — the fold was an obvious consolidation rather than a blank-page design.
2. **"Reformat the nav" vs "teach existing pages to talk" is a real choice.** The user rejected the former, got the latter, and the latter turned out to be higher-leverage anyway — every future producer page (screen watcher intake, new agent types) will automatically appear as a chip-bearing nav item with no reorganization needed.
3. **Origin classifier precedence was wrong in a quiet way.** Proposals could carry both an `autopilot_run_id` *and* a `gap_source` string starting with `integrity_`. The original classifier checked `autopilot_run_id` first, so integrity-backed proposals produced by autopilot-triggered integrity scans were misclassified as `autopilot`. Source-string prefix now wins. Caught because the new `proposed_by_origin` counter disagreed with what the old origin chip showed.
4. **Same-index skip in a shared j/k hook matters.** The first version of the hook called `onSelect(targetId)` unconditionally. AutopilotPage's `handleExpandRun` is a toggle, so pressing j on the last row (`curIdx === nextIdx`) collapsed the current selection. Added `if (nextIdx === curIdx) return;` to the hook — a one-line fix that prevents a whole class of bugs in toggle-style consumers.

**Phase 4 first co-ship + follow-up closed.** Next: Track A (#6 → second agent wave) vs Track B (#5 → #6 → #8) vs Track C (#4 defense-sale). See "Next session starts here" above.
