# AIP Phase 3 — Query Dossier

**Status:** design, 2026-04-22
**Scope:** Ship v1 as an on-read aggregation (not a new table). Accept revisit cost when new signals are added.

## Context

Corvus already records five distinct governance/measurement signals per query, each in its own table with its own UI affordance:

| Signal | Source | Exposed today |
|---|---|---|
| Pipeline execution | `queries.stage_telemetry_json` | Query Lab "Pipeline Telemetry" section |
| Eval scores | `eval_scores` + `eval_run_cases` | Query Lab "Evaluation" section, Eval Runs admin page |
| Output-guard hits | `output_violations` | Proposal queue (output.policy.check actions), not surfaced on the query directly |
| Mutations triggered | `actions` where `source_query_id=<q>` | Only refinements surfaced via `neuron_refinements` passthrough; full action trail is invisible |
| Integrity defects | `integrity_findings` | Integrity admin page; no query attribution |

A reviewer asking "tell me everything about query #450" has to mentally cross-join five tables. The Dossier is the reviewable unit that consolidates them.

## Non-goals

- **Immutable snapshot / dossier freeze.** The v1 dossier is a live projection — if a downstream action fires against the query later, the next dossier read reflects it. Freezing into a snapshot table is a clean additive enhancement once compliance workflows need it (sign-off, tamper-evident archive, legal hold). Flag as a caveat; don't build now.
- **Dedicated `/review/:query_id` route.** Today the Dossier lives inside Query Lab's historical-query detail. If auditors later want a standalone page, the endpoint already supports it — rebuild UI only.
- **Lifecycle states.** No open/closed/acknowledged. If acknowledgement is ever needed (compliance sign-off), that's Phase 3.5.
- **Export to PDF / compliance bundle.** Future.
- **Change-tracking diffs** (which specific actions mutated which neurons on this query). Visual affordance — deferred.
- **Cross-query joins** (e.g., "all queries that triggered an integrity finding last week"). Dossier is per-query; cross-query analytics is a different surface.

## Data model

```python
# backend/app/schemas.py

class DossierActionOut(BaseModel):
    id: int
    kind: str
    actor_type: str
    actor_id: str | None
    state: str                   # pending | applied | rejected | failed
    requires_approval: bool
    reason: str | None
    parent_action_id: int | None
    applied_at: str | None
    error_message: str | None
    created_at: str | None

class DossierOutputViolationOut(BaseModel):
    id: int
    rule_id: str
    severity: str                # info | warn | error | critical
    action: str                  # flag | redact | block
    matched_span: str | None
    redaction: str | None
    detail: dict | None
    action_id: int | None
    created_at: str | None

class DossierEvalRunParticipation(BaseModel):
    eval_run_id: int
    eval_run_case_id: int
    case_label: str
    suite_name: str
    suite_hash: str
    certified: bool              # matches tenant_config.certified_eval_run_id
    blocked: bool
    scores_json: dict | None     # snapshotted per-case scores
    violations_json: dict | None # snapshotted per-case violations
    run_status: str              # running | completed | failed
    run_started_at: str | None
    run_completed_at: str | None

class DossierIntegrityFindingOut(BaseModel):
    id: int
    scan_id: int
    finding_type: str
    severity: str
    priority_score: float
    description: str | None
    status: str                  # open | resolved | dismissed
    resolution: str | None
    attributed_via: str          # "selected_neurons" | "emergent_queue" | ...
    overlapping_neuron_ids: list[int]
    created_at: str | None

class QueryDossierPipelineSection(BaseModel):
    stage_telemetry: list[StageTelemetry]

class QueryDossierEvalSection(BaseModel):
    ad_hoc_scores: list[EvalScoreOut]            # scores written against this query outside any EvalRun
    eval_run_participations: list[DossierEvalRunParticipation]

class QueryDossierOutputSection(BaseModel):
    violations: list[DossierOutputViolationOut]

class QueryDossierActionsSection(BaseModel):
    actions: list[DossierActionOut]

class QueryDossierIntegritySection(BaseModel):
    findings: list[DossierIntegrityFindingOut]

class QueryDossier(BaseModel):
    query_id: int
    user_message: str
    created_at: str | None
    pipeline: QueryDossierPipelineSection
    eval: QueryDossierEvalSection
    output_checks: QueryDossierOutputSection
    actions: QueryDossierActionsSection
    integrity: QueryDossierIntegritySection
```

## Aggregation logic

Five parallel queries composed into one response:

1. **Pipeline**: `SELECT stage_telemetry_json FROM queries WHERE id=$q` → deserialize.
2. **Eval**:
   - `SELECT * FROM eval_scores WHERE query_id=$q` → `ad_hoc_scores`
   - `SELECT erc.*, er.suite_name, er.suite_hash, er.status, er.started_at, er.completed_at, tc.certified_eval_run_id FROM eval_run_cases erc JOIN eval_runs er ON erc.eval_run_id = er.id LEFT JOIN tenant_config tc ON tc.id = 1 WHERE erc.query_id=$q` → `eval_run_participations` (with `certified = (er.id == tc.certified_eval_run_id)`).
3. **Output**: `SELECT * FROM output_violations WHERE query_id=$q ORDER BY severity DESC, created_at ASC`.
4. **Actions**: `SELECT * FROM actions WHERE source_query_id=$q ORDER BY id ASC` — full tree; each row is self-describing via `parent_action_id` so the frontend can build the hierarchy if useful.
5. **Integrity** (overlap-based attribution): for each `IntegrityFinding`, compute the intersection of `finding.neuron_ids_json` and `query.selected_neuron_ids`. If non-empty, include the finding with `attributed_via="selected_neurons"` and the overlapping IDs. Query:
   ```sql
   SELECT f.*
   FROM integrity_findings f, queries q
   WHERE q.id = $q
     AND q.selected_neuron_ids IS NOT NULL
     AND f.neuron_ids_json IS NOT NULL
     AND (q.selected_neuron_ids::jsonb) ?| ARRAY[ ... ]  -- see note
   ```
   Implementation note: `selected_neuron_ids` and `neuron_ids_json` are both stored as JSON text; the cleanest approach is to deserialize in Python and do set-intersection, not a SQL JSONB path expression (the columns are TEXT, not JSONB). Load all findings scoped to a reasonable window (e.g., last 180 days) and intersect in-memory.

No new tables, no migrations, no new indices. The query shape is bounded by existing indexes (all the FKs used are already indexed).

## API

```
GET /queries/{query_id}/dossier
```

Returns `QueryDossier`. 404 if the query does not exist. No RBAC gating for v1 (matches `/queries/{id}` today). Lives next to `get_query_detail` in `app/routers/query.py`.

## Frontend placement

**Inside Query Lab's historical-query detail panel**, below the existing Refinements section. A new collapsible "Dossier" section renders on demand — lazy-fetches the endpoint when first expanded to avoid fetching for users who only glance at the detail.

Five sub-cards matching the five sections. Each reuses existing primitives:
- **Pipeline** — uses the existing `StageTelemetryTable` already on the page; passed the dossier's `pipeline.stage_telemetry`. No rendering change.
- **Eval** — two lists: ad-hoc scores (existing `EvalScoreOut` rendering) + eval-run participations (new mini-row: `suite_name · suite_hash[:8] · certified? · scored_at` with a chip for blocked/not-blocked).
- **Output Checks** — severity-colored list. Each row: severity pill, rule_id, action verb, matched span excerpt, redaction if any.
- **Actions** — tabular list: kind, actor, state, applied_at, reason. Reuses the `meta-chip` + `StatusPill` primitives. State pill colors done/error/pending.
- **Integrity** — list of findings with severity pills, finding_type, overlapping neuron IDs (as dim chips), description.

Empty sections render as "—" rather than empty cards so the structure stays visible (five headers always present). Auditors infer "no violations" ≠ "forgot to check."

## Materialization trade-off

**v1 = on-read**: every dossier request does the 5 queries in a single FastAPI handler. For a query touched by 5 actions + 3 violations + 2 integrity findings, the round-trip is under ~50ms (all indexed lookups). Acceptable for an interactive UI surface; not acceptable for bulk scan (e.g., "generate dossiers for all 10,000 queries") — that's a future concern.

**When to revisit materialization**:
- If any dossier read becomes slow (> 500ms) because one of the source surfaces grows unexpectedly, add a backing query_dossiers table with scheduled rebuild.
- If compliance requires tamper-evident archive, add an explicit `freeze` operation that snapshots the current projection into an immutable row.
- If auditors want dossiers as a queryable dataset (not per-query), materialization unlocks that.

Both paths are additive — the endpoint shape doesn't change, only its implementation.

## Caveats / revisit triggers

1. **Integrity attribution is heuristic.** We attribute a finding to a query if their neuron-id sets overlap. Misses: findings about query-generation patterns (no neurons involved) and findings that emerge from queries we don't know about yet. False positives: findings about popular neurons that dozens of queries touched. If integrity becomes load-bearing for review, revisit with a direct `source_query_id` FK on findings (a one-column migration).

2. **Action hierarchy is flattened.** Actions have a `parent_action_id` tree structure, but the v1 API returns them as a flat list ordered by id. Frontend builds the tree if it wants; backend doesn't pre-compute. If the action tree becomes the focal point, move the tree-building to the backend.

3. **No aggregation summary.** The dossier doesn't say "3 violations, 1 critical; 2 integrity findings open; 5 actions applied." Frontend can compute totals from the list data. If a textual summary becomes useful (e.g., for email notifications), add a `summary: DossierSummary` top-level field.

4. **Dossier is not tenant-scoped in its API shape.** The endpoint relies on the database being tenant-scoped at the connection level (which it already is via `TENANT_ID`). If multi-tenancy ever flows through the API layer instead of env-var routing, add an explicit tenant check.

5. **EvalScore-as-text vs. EvalScore-as-number.** `EvalScore` already exposes structured 1-5 scores. The dossier returns those verbatim; we don't compute per-query pass/fail rollup. A future `DossierSummary` could.

## Phased ship plan

| Ship | What | Commit |
|---|---|---|
| 1 | This design doc | One commit — pure documentation. |
| 2 | Pydantic schemas + service function + `GET /queries/{id}/dossier` endpoint + tests | One commit. All green; lint clean. |
| 3 | Dossier UI section in Query Lab historical detail (lazy-fetch, 5 sub-cards) | One commit. `npm run build` clean; visual check on a live query. |

Total: three commits, one short session.

## Related files

- `backend/app/routers/query.py` — new endpoint lives here next to `get_query_detail`
- `backend/app/schemas.py` — new Pydantic models (see Data model)
- `backend/app/services/query_dossier.py` — new service module (aggregation logic)
- `backend/tests/test_query_dossier.py` — new unit tests (aggregation, 404, integrity overlap)
- `frontend/src/components/QueryLab.tsx` — new Dossier section in the historical detail
- `frontend/src/api.ts` — new `fetchQueryDossier(id)` helper
- `frontend/src/types.ts` — TS types mirroring the Pydantic models
- `~/Projects/master-corvus/public/roadmap-state.json::gov-aip-p3` — flip to `done` with v1-note when shipped
