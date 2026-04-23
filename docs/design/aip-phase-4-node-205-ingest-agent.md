# AIP Phase 4 #205 — Document Ingestion agent

**Status:** design, 2026-04-23
**Scope:** v1 reviews existing `gap_source='document_ingest'` `AutopilotProposal` rows post-extraction and refines their classification with an explicit confidence score or flags them as uncertain. Out of scope v1: inline classification during extraction (today's Sonnet extractor stays untouched), multi-proposal fan-out per section, and the 20-doc held-out eval (acceptance-criterion #1 lives in a manual fixture).

## Context

Phase 4 second-wave agent. `#205` has shipped-sibling patterns in `#202` (dedup) and `#204` (integrity reconciler), both of which review findings and stage proposals through the standard human-approval workflow. Pattern #6 (ontology branching) is the declared prereq — shipped 2026-04-22 as the BranchStage primitive. This agent doesn't *require* BranchStage v1; it may use it in a future extension where classification forks by document type.

### What's actually "brittle" that #205 fixes

The roadmap summary says the agent "replaces brittle single-shot Haiku classify pass with reasoning + uncertainty surfacing." Important clarifying finding from the pre-implementation survey: **no such classify pass exists today.** Current doc-ingest (`backend/app/services/document_extractor.py`) uses the **user-supplied** `department` / `role_key` / `authority_level` / `source_type` from the upload form and bakes them verbatim into every neuron proposal extracted from that document. If the user picks the wrong department at upload time, every extracted neuron is silently filed in the wrong place.

The "brittle pattern to avoid" is the forward-looking one: bolt a naive one-shot Haiku classify onto extraction and trust it silently. #205 is the non-brittle version of that same step: post-extraction review with confidence + explicit uncertainty.

## Non-goals

- **Inline classification during extraction.** Sonnet-based extraction in `document_extractor.py` is fine and stays untouched. The agent reads ALREADY-CREATED proposals.
- **Multi-proposal per section.** The design in some earlier notes imagined emitting *two* proposals per ambiguous section (layer 2 + layer 3 at different confidences). v1 keeps one proposal per section; uncertainty surfaces as a flag on that single proposal. Fan-out can be v2 if human reviewers ask for it.
- **Schema changes.** No new columns on `autopilot_proposals`. v1 uses the existing `gap_source` string and stashes structured confidence + candidates in `gap_evidence_json`. If later we want proper uncertainty columns, that's an additive migration.
- **Mutating the original user-supplied classification.** The agent refines the `neuron_spec_json` on `ProposalItem` rows for the target proposal. The original upload job metadata stays intact for audit.

## Agent shape — mirrors #204

```yaml
name: document_ingest_reviewer
role: proposal_curator
description: >
  Reviews document-ingest AutopilotProposals (gap_source='document_ingest')
  after extraction. For each proposal, weighs the extracted neuron's
  content against ontology context and either refines the classification
  (layer/department/role_key) with a confidence score or flags the
  proposal as uncertain for explicit human review. Never mutates
  neurons directly — changes flow through the standard approve → apply
  workflow.
model: haiku
max_tokens: 1024
max_turns: 16
tool_allow_list:
  - list_pending_ingest_proposals
  - get_ingest_proposal_detail
  - refine_ingest_classification
  - flag_ingest_uncertain
trigger:
  manual: true
  schedule:
    enabled: false
```

**Note: no `write_neuron` / `update_neuron` / `create_neuron` tool in the allow-list.** The agent can only refine-in-place via `refine_ingest_classification` (which updates the pending proposal's `neuron_spec_json`) or `flag_ingest_uncertain` (which changes the proposal's `gap_source` string). All graph mutation stays gated on human approval downstream.

## Tools

Four tools, two read, two mutating.

### `list_pending_ingest_proposals(limit, rationale)` — read
Returns up to `limit` open `AutopilotProposal` rows with `gap_source='document_ingest'` and `state='proposed'` that haven't been reviewed (no `reviewed_at`). Ordered by `id` ascending. Shape mirrors `list_pending_duplicates`/`list_pending_contradictions`.

### `get_ingest_proposal_detail(proposal_id, rationale)` — read
Returns:
- Proposal metadata (gap_source, description, priority_score, llm_reasoning, created_at)
- `ProposalItem` neuron specs (one per neuron the proposal would create). Each spec has `parent_id`, `layer`, `node_type`, `label`, `content`, `summary`, `department`, `role_key`.
- A small sample of sibling neurons under the target parent (so the agent has ontology context without flooding the prompt). Cap ~8.

This is where the agent reads signal to make a classification judgment. Enough context to form an opinion without budget bloat.

### `refine_ingest_classification(proposal_id, item_updates, confidence, rationale)` — mutating
`item_updates` is a list of `{item_id, layer, department, role_key}` dicts — one per `ProposalItem` the agent wants to refine. Updates each listed item's `neuron_spec_json` with the agent's chosen values.

Updates the proposal row:
- `llm_reasoning` → concise reasoning for the chosen classification (agent fills)
- `reviewed_by = 'agent:document_ingest_reviewer'`
- `reviewed_at = NOW()`
- `gap_evidence_json` → appends `{"agent_confidence": <0..1>, "agent_model": "haiku", "reviewed_at": "..."}` as a structured object (keeps any existing evidence chain).

Proposal state **stays `"proposed"`** — the human still needs to approve before neurons are created. The agent just sharpens the classification inside.

### `flag_ingest_uncertain(proposal_id, candidates, rationale)` — mutating
Used when the agent genuinely can't pick between placements. `candidates` is a list of 2–3 `{layer, department, role_key, confidence, rationale}` dicts — the agent's top possibilities, each with a confidence score.

Updates the proposal:
- `gap_source` → `'document_ingest/uncertain'` (satisfies acceptance criterion #2 literally; the review UI can filter on this string)
- `llm_reasoning` → combined rationale explaining why the agent flagged it
- `gap_evidence_json` → adds a `{"uncertain": true, "candidates": [...]}` structured block
- `reviewed_by` / `reviewed_at` set so the proposal is marked touched
- `priority_score` → kept or lowered (these need explicit reviewer attention)

Again, state stays `"proposed"`. Humans can approve, reject, or manually edit the classification from the uncertain bucket.

## Agent reasoning (system prompt sketch)

1. `list_pending_ingest_proposals(limit=5, rationale=...)` — one batch per run.
2. For each proposal:
   a. `get_ingest_proposal_detail(proposal_id, rationale=...)` — fetch full content + ontology context.
   b. Decide:
      - If the user-supplied classification is clearly wrong given the content (e.g. a procurement-policy excerpt tagged as "Engineering/Design"), `refine_ingest_classification` with confidence ≥0.8.
      - If the classification is correct (agent agrees), `refine_ingest_classification` with the same fields + confidence ≥0.9 (no content change, but marks it reviewed).
      - If the content is ambiguous (could fit 2–3 placements, e.g. "could be Procurement or Legal depending on interpretation"), `flag_ingest_uncertain` with the top candidates.
3. Emit `{"done": true, "summary": "..."}` when all fetched proposals are processed.

**Hard rules** (same shape as the other agents):
- Cannot mutate neurons. Only proposal-level classification refinement.
- If genuinely uncertain between refine-vs-flag, prefer `flag_ingest_uncertain` — humans can still approve the flagged proposal manually.
- Never call a tool not in the allow-list.
- Every tool call requires a non-empty `rationale` (input_schema enforces min 20 chars).
- One JSON envelope per turn.

## Acceptance criteria (from roadmap node)

1. **Held-out 20-doc set: layer-classification agreement ≥85% vs. labels.**
   Manual fixture: 20 real documents with gold-labeled layer/dept/role per section. Script: `backend/scripts/ingest_reviewer_fixture.py`. Runs the full agent loop on each doc's proposals, compares agent's chosen classification to the gold labels, reports agreement %. **Not pytest** — requires Postgres + LLM + real docs. Documented in this plan as the acceptance check.

2. **Uncertainty surfacing: ambiguous segments appear as `origin='ingestion_uncertain'` proposals, not silent commits.**
   Satisfied by `flag_ingest_uncertain` setting `gap_source='document_ingest/uncertain'`. Existing review UI (`ProposalQueuePage`) filters by `gap_source` and will show these as a distinct bucket. If the review UI doesn't currently support a "/" in gap_source, that's a small frontend follow-up (add the substring match filter).

3. **Latency: 20-page PDF stays under 2 minutes (comparable to current ±30%).**
   Agent adds one Haiku call per proposal (~2–5s each, parallelizable). A 20-page PDF typically yields ~30–80 proposals. At sequential 5s/proposal that's 2.5–7 minutes — above the 2min target. **Mitigation:** process in `max_turns=16` batches so the agent handles 5–10 proposals per run; multiple runs concurrent. Or: rate-limit to N parallel Haiku calls inside a single tool. Initial ship keeps it sequential; if the 20-page target is missed, the mitigation is to swap `refine_ingest_classification` to accept a list of proposals in one call (bulk classify). Flagged as v1.1 if latency fails.

## Test plan

- **Unit tests** in `backend/tests/test_ingest_reviewer_tools.py` (new):
  - Each tool tested with the `_FakeSession` stand-in pattern from `test_integrity_reconciler_tools.py`.
  - `list_pending_ingest_proposals` filters to `gap_source='document_ingest'` + `state='proposed'` + unreviewed.
  - `get_ingest_proposal_detail` returns items + ontology sample; 404s on missing.
  - `refine_ingest_classification` rejects invalid confidence (outside 0..1), rejects wrong gap_source; updates item specs + llm_reasoning + reviewed_at.
  - `flag_ingest_uncertain` sets gap_source='document_ingest/uncertain' + preserves existing gap_evidence; rejects wrong source; rejects empty candidates.
- **Allow-list check** (can piggyback on the existing agent-registry test pattern): YAML loads, allow-list matches the 4 declared tools, no neuron-write tool names present.

## Phased ship plan

| Ship | What | Commit |
|---|---|---|
| 1 | This design doc | One commit. |
| 2 | YAML + 4 tools + unit tests + tools/__init__ registration | One commit. pytest + nasa_lint green. Live `GET /v1/agents` lists the new agent. |

## Caveats

1. **Uncertainty expressed via `gap_source='document_ingest/uncertain'` is a stringly-typed flag, not a typed field.** Works for v1 and keeps the migration surface zero. If it becomes load-bearing (e.g., reviewers want to query "all uncertain proposals across all sources"), add a proper `uncertainty_level` enum column in a future commit.

2. **No batch multi-proposal fan-out.** If a section is genuinely 50/50 between two placements, the agent flags it as uncertain with candidates list rather than emitting two parallel proposals. Humans pick from the candidates manually. If we find the review UI needs the two-row view, that's v2.

3. **The 20-doc held-out fixture is deferred.** The agent ships without it; the fixture is a manual acceptance artifact to produce on the first real ingestion load. Logged alongside the existing `test_agents_runtime.py:3` "manual verification plan" convention.

4. **Agent runtime depth.** `max_turns=16` (up from 12 on the other agents) because one run handles 5 proposals × ~3 tool calls each = 15 turns. If we hit the limit mid-batch, the run exits cleanly and the next run picks up the remainder.

## Related files

- `backend/app/agents/definitions/integrity_reconciler.yaml` — template
- `backend/app/agents/tools/integrity_reconciler_tools.py` — tool-shape template
- `backend/app/services/document_extractor.py` — producer of `document_ingest` proposals (untouched)
- `backend/app/routers/proposals.py` — proposal-detail endpoint the review UI hits (no changes v1)
- `~/Projects/master-corvus/public/roadmap-state.json::gov-aip-p4-205` — scope / verification
