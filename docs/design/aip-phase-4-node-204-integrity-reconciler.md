# AIP Phase 4 #204 — Integrity Reconciler agent

**Status:** design, 2026-04-22
**Scope:** v1 handles `contradiction` integrity findings. Other finding types (aging, homeostasis, missing_connection) are out of scope — each has a distinct resolution profile and would be a separate agent.

## Context

Phase 4's first co-ship (2026-04-20) delivered the agent registry (`#201`), the dedup agent (`#202`), and tenant agent visibility (`#208`). The dedup agent handles `near_duplicate` findings produced by `pattern_separation.py`. The `conflict_monitor.py` service already emits `contradiction` findings (verified — `finding_type="contradiction"` at `services/integrity/conflict_monitor.py:132`), but there is no agent pre-reviewing them — every contradiction waits for a human.

The integrity reconciler is the agent counterpart to dedup, scoped to contradictions. Per the roadmap node: "Resolves conflicts surfaced by integrity.py scans using regulatory/recency/credibility context."

Pattern #6 (ontology branching) was the originally-declared prereq in the node summary. Now that #6 has shipped, the reconciler is unblocked.

## Non-goals

- **Aging / homeostasis / missing_connection.** Each has a distinct rationale shape; bundling them under one agent muddles its purpose. If we want a unified "graph-health" agent later, that's a separate node.
- **Autonomous contradiction resolution.** The agent never mutates neurons directly. It creates `AutopilotProposal` rows in state=proposed; humans approve; proposal.apply mutates the graph.
- **Automatic contradiction scanning.** The agent consumes findings produced by `conflict_monitor.py`. Running the scan itself stays an explicit admin action. If scheduled scanning becomes useful, that's a separate item.
- **Cross-finding reasoning.** The agent processes one finding at a time. It doesn't look at the graph's overall consistency or propose holistic reorganizations.
- **Resolution beyond the existing enum.** The four resolution options (`a_correct`, `b_correct`, `context_added`, dismissed) are the contract surface with existing proposal workflow. No new resolution types are introduced.

## Agent shape — mirrors dedup

```yaml
name: integrity_reconciler
role: proposal_curator
description: >
  Pre-reviews contradiction IntegrityFinding rows. For each pair of
  contradicting neurons, weighs regulatory authority / recency / credibility
  context and proposes one of four resolutions (a_correct, b_correct,
  context_added, or dismissed). Never mutates neurons directly.
model: haiku
max_tokens: 1024
max_turns: 12
tool_allow_list:
  - list_pending_contradictions
  - get_contradiction_detail
  - propose_contradiction_resolution
  - dismiss_contradiction
trigger:
  manual: true
  schedule:
    enabled: false
```

**Note: no `write_neuron` or `update_neuron` tools are exposed, per acceptance criterion 3.** Attempting to call one raises `ToolNotAllowedError` at the runtime's allow-list check (`runtime.py:174`).

## Tools

Four tools, two read, two mutating (proposal-creating):

### `list_pending_contradictions(limit)`  *(read)*
Returns up to `limit` open contradiction findings ordered by priority_score desc. Shape mirrors `list_pending_duplicates`.

### `get_contradiction_detail(finding_id)`  *(read)*
Returns the two neurons in the finding plus the **resolution signals**:
- `authority_level` (regulatory)
- `updated_at` / `created_at` (recency)
- `invocations`, `avg_utility` (credibility — how often and how well it fires)
- `source_origin` (manual vs. autopilot vs. document_ingest)
- `content`, `summary`, `label`, `department`

This is where "uses regulatory/recency/credibility context" is materialized. The agent reasons against these fields directly — no separate `analyze_context` tool is needed (the context is surfaced in the detail return).

### `propose_contradiction_resolution(finding_id, resolution, notes)`  *(mutating)*
Creates an `AutopilotProposal` in state=proposed. `resolution` must be one of:
- `a_correct` — neuron A (index 0 in neuron_ids) wins; B is the mistake
- `b_correct` — neuron B (index 1) wins; A is the mistake
- `context_added` — both can be correct in different contexts; add disambiguating notes to both

Routes through `services/integrity/proposals.create_integrity_proposal()` — same code path the existing admin UI uses. `reviewer="agent:integrity_reconciler"`. The proposal then flows through the standard `proposed → approved → applied` workflow.

### `dismiss_contradiction(finding_id, notes)`  *(mutating, minimal)*
Closes the finding with `status="resolved", resolution="dismissed"` if the scan was wrong (e.g., the two neurons actually discuss different aspects of the same topic and only superficially contradict). Does not create a proposal — no graph change. Identical pattern to `mark_reviewed_as_unique(resolution="dismissed")` in dedup.

## Agent reasoning (system prompt sketch)

1. Call `list_pending_contradictions` (limit 5).
2. For each finding:
   a. Call `get_contradiction_detail(finding_id)`.
   b. Inspect the two neurons' resolution signals.
   c. Decide:
      - If one neuron has materially higher `authority_level` AND cites a more authoritative source → `propose_contradiction_resolution(resolution="a_correct")` (or `b_correct`).
      - If both are comparably authoritative but one is significantly more recent (newer content reflects a policy change) → propose the newer one as correct.
      - If both remain plausible in different contexts (different materials, processes, jurisdictions) → `propose_contradiction_resolution(resolution="context_added")` with notes describing the disambiguating context.
      - If the contradiction is spurious (scan misfired; neurons are about different topics) → `dismiss_contradiction`.
3. When all fetched findings are processed, emit `{"done": true, "summary": "..."}`.

**Hard rules** (same shape as dedup):
- Cannot mutate neurons. Only proposals + finding-status closes.
- If uncertain between a_correct/b_correct, prefer `context_added` — humans can still choose on review.
- Never call a tool not in the allow-list.
- One JSON envelope per turn.

## Acceptance criteria (from roadmap node)

1. **Synthetic fixture: 5-case conflict suite — agent resolves correctly in ≥ 4/5.**
   - Fixture = 5 `IntegrityFinding(finding_type="contradiction")` rows with constructed neuron pairs covering: clear a_correct, clear b_correct, context_added, dismissed, and one borderline. Scoring: the agent's proposal resolution must match the gold label for ≥4.
   - Running this requires Postgres + LLM calls. Following the existing convention (`tests/test_agents_runtime.py` line 3), the fixture + assertions live in a **manual verification script** at `backend/scripts/integrity_reconciler_fixture.py`, not pytest. `docs/design/...` documents how to run it.
2. **Reasoning persisted: every action row has non-empty `rationale` in input_json.**
   - Every `agent.tool.*` action row must have `input_json.rationale` set to the agent's stated reason for calling the tool. This is enforced by requiring a `rationale` string in every tool's input schema (maxLength 500, minLength 20).
3. **No autonomous write: write_neuron not in allow-list → ToolNotAllowedError on attempt.**
   - Covered by the fact that no neuron-write tool is in the allow-list. A unit test verifies that if the agent attempted a disallowed tool name, the runtime rejects it.

## Test plan

- **Unit tests** in `backend/tests/test_integrity_reconciler_tools.py` (new):
  - Each tool function tested with an in-memory SQLAlchemy session (mirrors how dedup tests would work — but dedup tools currently have no unit tests; this agent introduces the pattern).
  - Test: `list_pending_contradictions` returns only contradiction rows with status=open.
  - Test: `get_contradiction_detail` raises if the finding_type is wrong (defensive check).
  - Test: `propose_contradiction_resolution` rejects resolution values not in the valid set.
  - Test: `dismiss_contradiction` refuses to close a resolved finding.
- **Allow-list test** (can live in `test_agents_registry.py` or a new file):
  - Load the integrity_reconciler YAML; assert no neuron-mutation tool names in `tool_allow_list`.
  - Assert `tool_allow_list` exactly matches the 4 tools the design declares.
- **Manual fixture** at `backend/scripts/integrity_reconciler_fixture.py`:
  - Seeds 5 findings + runs the agent + asserts ≥4 correct.
  - Documented as the acceptance check.

## Phased ship plan

| Ship | What | Commit |
|---|---|---|
| 1 | This design doc | One commit. |
| 2 | YAML + tools + tools/__init__ registration + unit tests + manual fixture script | One commit. pytest + nasa_lint green; live `GET /v1/agents` shows the new agent. |

## Caveats

1. **Rationale enforcement is schema-side, not runtime-side.** We add `rationale` as a required field on every tool's input schema so the agent must provide it. If the agent emits an empty string, schema validation still passes. That's acceptable v1 — schema validators can tighten later if agents cheat.
2. **No contradiction findings exist yet in production.** Live smoke will show "count=0, no work" until `conflict_monitor.py::scan_contradictions()` runs on a neuron set with actual contradictions. This is fine — the agent is ready.
3. **Authority level is only as good as the input data.** If tenants don't set `authority_level` on their neurons, the reconciler falls back to recency + credibility, which are weaker signals. Document as a data-quality note; not an agent design flaw.

## Related files

- `~/Projects/corvus/backend/app/agents/definitions/dedup.yaml` — template for the YAML
- `~/Projects/corvus/backend/app/agents/tools/dedup_tools.py` — tool-shape template
- `~/Projects/corvus/backend/app/services/integrity/conflict_monitor.py` — producer of contradiction findings
- `~/Projects/corvus/backend/app/services/integrity/proposals.py` — `create_integrity_proposal` function
- `~/Projects/master-corvus/public/roadmap-state.json::gov-aip-p4-204` — scope / verification
