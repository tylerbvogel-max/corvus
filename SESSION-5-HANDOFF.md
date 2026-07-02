# Session 5 Handoff — Document Ingest: RLM eval, whole-doc extraction, two-phase pipeline with guardrails

## What Was Built

This session addressed a production-quality problem in Corvus's document-ingest pipeline and, in the course of fixing it, introduced a general three-layer agent-guardrail framework that prevents silent hallucination for any LLM-driven action that mutates the graph.

Three distinct deliverables landed, in sequence:

1. **MIT Recursive Language Models evaluation (rejected, documented).** Spawned as a research task, evaluated RLM against Corvus's document-ingest problem, and concluded RLMs provide no advantage for documents that fit in a 200k-token context window. Evaluation workspace preserved at `~/Projects/rlm-eval/` for reference.
2. **Phase 1 — whole-doc single-pass extraction (commit `c49a1c3`).** Replaced the per-section chunked extractor with a single Opus call consuming the full document. On MIL-STD-1587E, extraction went from 1 proposal → 5 neurons to 81 proposals covering every substantive subsection.
3. **Phase 2 — narrow-contract agent + three-layer guardrails + Opus (commit `62d4137`).** Split extraction from graph placement. Built a general anti-hallucination framework (tool-level validation, mandatory read-back, audit-trail-derived summary, mutation-count reconciliation). Placer agent reliably commits placements under Opus.

## Design Philosophy

Two architectural principles emerged and were codified in memory for future sessions:

**Service vs. agent separation.** A single synchronous LLM call with structured JSON output is a service function, not an agent. Agents in Corvus are YAML-declared, tool-using, iterative loops with `max_turns` and `tool_allow_list`, invoked via `/v1/agents/{name}/run`. Extraction is a service (one Opus call); placement is an agent (iterative graph-navigation per artifact). Conflating them adds framework overhead for no benefit on the service side and blurs responsibility on the agent side.

**Assume the model will lie about what it did.** LLMs invoked via the Claude CLI for multi-step agentic workflows reliably hallucinate "I completed the task" summaries even when they skipped the actions entirely. The architectural response is to make fabrication impossible rather than unlikely — tool-level validation, mandatory post-commit reads, and authoritative summaries derived from observed actions rather than the model's self-report.

**Narrowing funnel → expanding funnel separation** (user-framed). Document ingest has two genuinely distinct cognitive tasks: reading dense source material and extracting discrete knowledge artifacts (narrowing), and deciding where each artifact fits in the neuron graph (expanding). These are separate skills that a human organization would split between subject-matter experts and knowledge-graph curators. The two-phase pipeline mirrors this split.

---

## Pipeline Architecture (Current)

```
User uploads PDF
        │
        ▼
┌─────────────────────────────────────┐
│ Pass 1: document_parser.parse_*     │  service, synchronous
│ PyMuPDF → text + Section structure  │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ Phase 1: extract_whole_document()   │  service, ONE llm_chat(opus) call
│ Full doc → JSON artifact list       │  state='artifact' AutopilotProposals
│ [dispatch: chunked fallback if      │  gap_evidence: section, page,
│  doc > WHOLE_DOC_THRESHOLD_CHARS]   │  verbatim_quote, node_type, tags
└─────────────────────────────────────┘
        │ (auto-chains)
        ▼
┌─────────────────────────────────────┐
│ Phase 2: per-artifact loop          │  orchestrator in document_extractor
│ for artifact_id in remaining:       │  circuit-break after 3 consec aborts
│   execute_agent(neuron_placer,       │
│     input_context={artifact_id},    │
│     expected_mutations=1,           │
│     require_verification_for=       │
│       'get_placement_status')       │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ neuron_placer agent (Opus, 15 turns) │  agent, fixed 5-turn workflow:
│ get_ingest_proposal_detail           │    1. read artifact
│  → search_graph_parents              │    2. search candidates
│  → refine_ingest_classification      │    3. commit (Layer 1 validates)
│    (validated; promotes state)       │
│  → get_placement_status              │    4. read-back (Layer 2 enforced)
│  → done                              │    5. done (Layer 3 derives summary)
└─────────────────────────────────────┘
        │
        ▼
Standard human-approval proposal queue → Neuron graph
```

---

## Backend Changes

### New Files

| File | Purpose |
|---|---|
| `backend/tests/test_document_extractor.py` | Hermetic unit tests for whole-doc extraction, grouping, page-marker injection, parser (array + object fallback), persistence, artifact-shape vs placed shape |
| `backend/app/agents/definitions/neuron_placer.yaml` | Phase 2 agent definition (renamed from `document_ingest_reviewer.yaml`) |

### Renamed

| Before | After |
|---|---|
| `backend/app/agents/definitions/document_ingest_reviewer.yaml` | `backend/app/agents/definitions/neuron_placer.yaml` |

The rename reflects broadened scope: the agent no longer refines classifications on pre-classified proposals — it performs primary placement on unclassified artifacts.

### Modified Files

#### `backend/app/services/document_extractor.py`
Major rewrite. New shape:

- `extract_whole_document(job, extracted_text, structure) -> (proposals, usage, raw_text)` — single Opus call, returns raw text for audit.
- `_inject_page_markers(text, structure)` — inserts `[PAGE N]` markers at section boundaries so the LLM can cite pages.
- `_build_whole_doc_prompt` + `_WHOLE_DOC_SYSTEM_PROMPT` — artifact-shape prompt, NO graph-placement fields requested; strict JSON output contract.
- `_build_whole_doc_user_message(..., request_nonce)` — prepends unique per-run marker (job.id) to bust Anthropic prompt cache.
- `_parse_llm_proposals()` — multi-strategy JSON extraction (whole string → array span → individual objects fallback), filters non-dict entries.
- `create_whole_doc_proposals()` — groups LLM-emitted proposals by `section` field; writes AutopilotProposal rows with `state='artifact'`.
- `_persist_proposal_group(is_artifact=True|False)` — shared persistence for both whole-doc (artifact state) and chunked-fallback (proposed state) paths.
- `_build_artifact_spec(prop, job)` — ProposalItem spec with ONLY content fields (no placement fields).
- `run_document_extraction` — dispatches to whole-doc path if `len(extracted_text) <= WHOLE_DOC_THRESHOLD_CHARS (600,000)`, else chunked fallback.
- `_run_whole_doc_extraction` → `_run_phase1_extraction` + `_run_phase2_placement` (split for clarity and JPL-4 compliance).
- `_run_phase2_placement` — loops over remaining artifact IDs, invokes `execute_agent` per-artifact with `expected_mutations=1` and `require_verification_for='get_placement_status'`. Circuit-breaks after 3 consecutive aborts.
- Default model on upload changed from `sonnet` to `opus` (router: `backend/app/routers/document_ingest.py`).

#### `backend/app/agents/runtime.py`
Extended `execute_agent` with two new kwargs and introduced a factored-out loop architecture:

- `expected_mutations: int | None = None` — if set, `{done}` envelopes with `mutations < expected_mutations` are rejected.
- `require_verification_for: str | None = None` — if set, `{done}` requires the named tool to have been called during the run.
- `_MAX_DONE_REJECTIONS = 2` — after this many done-envelope rejections, run auto-aborts with structured summary.
- `_LoopState` dataclass threads mutable loop state through helper functions (factored `execute_agent` from 241 lines to under 60 via `_run_turn_loop`, `_handle_one_turn`, `_handle_done_envelope`, `_handle_tool_envelope`, `_invoke_tool`, `_finalize_run`).
- `_evaluate_done_envelope(mutations, expected_mutations, called_verification_tool, require_verification_for)` — pure function; returns rejection message or None.
- `_fetch_child_actions(session, root_action_id)` — queries Action rows for summary derivation.
- `_derive_summary(...)` — Layer 3 — walks child Actions post-run and composes authoritative summary from observed tool inputs/outputs. Agent's own summary preserved as `model_self_report` but not treated as authoritative.
- `_describe_mutation(tool_name, inp, out)` — pure per-tool line-builder for derived summaries (refine, flag_uncertain, dedup tools, integrity tools).
- `_tool_is_readonly(kind)` + `_READONLY_TOOL_NAMES` — classifier used during summary derivation.
- `AgentRunResult.model_self_report: str` field added for audit retention.
- `_build_initial_message` — now prepends `=== STANDALONE REQUEST — not a continuation (marker) ===` where marker is derived from input_context (artifact_id / job_id / proposal_id / finding_id). Cache-bust against Anthropic prompt cache continuation-hallucinations.

#### `backend/app/agents/tools/ingest_reviewer_tools.py`
- `list_pending_ingest_proposals` — now filters `state='artifact'` (was `state='proposed'`). Retained but dropped from the neuron_placer agent's allow-list (agent no longer enumerates; orchestrator loops per-artifact).
- `refine_ingest_classification`:
  - Input schema extended: each item update requires `parent_id` and `rationale` (in addition to existing `layer`, `department`, `role_key`).
  - Calls `_validate_placement_updates()` before committing — Layer 1 validation. Raises `ValueError` on unknown `parent_id` (not a real active Neuron), unknown `department` (not in the set of distinct departments on active neurons), `role_key` not under the chosen department, or `layer` incompatible with parent's `node_type`.
  - On successful call with complete classification: promotes `state` from `'artifact'` to `'proposed'` (Guardrail 1 commit signal).
  - Per-item rationale carried to `ProposalItem.reason` for human review context.
  - Agent attribution: `reviewed_by='agent:neuron_placer'` (was `agent:document_ingest_reviewer`).
- `flag_ingest_uncertain` — relabeled `agent:neuron_placer`; otherwise unchanged.
- New tool `search_graph_parents(label_pattern, department, role_key, max_layer, max_results, rationale)` — ILIKE search over Neuron table for candidate parent placements. Returns up to `max_results` candidates with layer, department, role_key, sibling_count.
- New tool `get_placement_status(proposal_id, rationale)` — Layer 2 mandatory read-back. Returns current persisted state of a proposal (state, gap_source, placement fields, reviewed_by, reviewed_at). Marked read-only.
- `_validate_placement_updates(session, updates)` — batched validation helper.
- `_NODE_TYPE_LAYER_COMPAT` — frozen-dict soft compatibility between node_type and allowed graph layers.
- `_fetch_valid_parents`, `_fetch_known_dept_roles` — helpers.

#### `backend/app/agents/definitions/neuron_placer.yaml`
- `name: neuron_placer` (renamed from `document_ingest_reviewer`).
- `model: opus` (from `haiku`).
- `max_tokens: 4096` (from `1024`).
- `max_turns: 15` (up from 16; narrowed workflow needs less headroom but must survive Layer 1 validation retries).
- `tool_allow_list`: dropped `list_pending_ingest_proposals` (orchestrator provides artifact_id); added `search_graph_parents` and `get_placement_status`. Final list: `get_ingest_proposal_detail`, `search_graph_parents`, `refine_ingest_classification`, `flag_ingest_uncertain`, `get_placement_status`.
- `admin_description` — rewritten to describe the primary-placement role.
- `system_prompt` — rewritten end-to-end. Describes the fixed 5-turn workflow, the guardrail contract (runtime-enforced hard rules), and the layer↔node_type guidance. Emphasizes: "You will be given exactly one artifact_id via input_context."

#### `backend/app/routers/document_ingest.py`
- Upload endpoint `model` form default changed from `"sonnet"` to `"opus"`.

#### Test files updated / added
- `backend/tests/test_document_extractor.py` (new) — 20 tests covering whole-doc extraction, grouping, page markers, parser fallbacks, artifact-shape persistence.
- `backend/tests/test_ingest_reviewer_tools.py` — updated for: new tool imports, Layer 1 validation rejection tests (unknown parent/department, layer/node_type mismatch), `get_placement_status` read-back tests, allow-list assertion (no list_pending, yes get_placement_status), state-promotion assertion on refine. Extended `_FakeExecuteResult` with `.all()` and `.scalar()` methods.
- `backend/tests/test_agents_runtime.py` — added tests for `_evaluate_done_envelope` (mutation-count check, verification-tool check, ordering), `_describe_mutation` (per-tool line shape), `_derive_summary` (refuses to use self-report, uses observed mutations, reports auto-abort path, reports no-mutations case).

### Model Registry / Database

No schema changes. Intentional — state='artifact' is additive to the existing `AutopilotProposal.state` string column; no enum constraint. All intermediate state lives in existing columns (`gap_evidence_json`, `neuron_spec_json`, `reason`). See `project_corvus_ingest_architecture` memory for rationale.

### Test + Lint Status

- Full regression: **290 tests pass** (up from 251 at session start).
- NASA lint: **strict-clean** on all modified files. Guideline warnings exist on `_build_extraction_prompt` (74 lines, pre-existing) and `_persist_proposal_group` (70 lines, created this session but acceptable trade-off for a single cohesive row-construction function).

---

## Supporting Artifacts

### `~/Projects/rlm-eval/` — MIT RLM evaluation workspace (preserved)

Not part of the Corvus repo. Contains:
- `questions.py` — 11 hand-authored questions about MIL-STD-1587E with ground truth; reusable for any future ingest regression testing.
- `claude_cli_client.py` — Claude-CLI `BaseLM` subclass with `register_claude_cli_backend()` monkey-patch; reusable if RLM is ever revisited.
- `extract_pdf.py` — MIL-STD-1587E → plaintext with `[PAGE N]` markers (standalone from Corvus's document_parser).
- `harness.py`, `harness_corvus_only.py`, `extract_test.py`, `extract_oneshot.py` — the three-path eval infrastructure.
- `aggregate.py` — scoreboard renderer.
- `eval_results.json` — per-question outputs across 3 paths (RLM, one-shot Claude, Corvus).
- `FINDINGS.md` — conclusion: do not integrate RLM; fix ingest by replacing chunking with single-shot whole-doc Claude call. Preserved verdict for future revisit triggers.

### Memory entries

- `project_corvus_rlm_eval.md` — RLM rejected; real fix is single-shot Claude with whole doc. Re-open conditions documented.
- `project_corvus_ingest_architecture.md` — Two-phase pipeline described. Service-vs-agent rule of thumb codified. Key files + thresholds.

---

## Integration Evidence (Pre-Commit Smoke Tests)

On the 17 waiting artifacts from the one-attempt ingest done during development:

**Sonnet run (diagnostic before Opus switch)**: agent reliably hallucinated `{done}` with fabricated "Placed N artifacts" summaries but zero mutations. Every run was caught by guardrails — artifacts stayed in `state='artifact'`, audit trails honestly said `"No mutations committed"`, no data corruption. Confirmed guardrails function as designed under a failing model.

**Opus run (post-switch with `max_turns=15`)**: artifact 117 placed successfully in 8 turns, 6 tool calls, 1 mutation. Trace: `get_ingest_proposal_detail → search_graph_parents (×3, first attempt invented department "Materials & Processes" and was rejected by Layer 1) → refine_ingest_classification (succeeded) → get_placement_status → done`. Proposal 117 state promoted artifact→proposed, reviewed_by=`agent:neuron_placer`, placement recorded with confidence 0.90. Artifact 118 similarly placed in 7 turns, 5 tool calls. Both survived the narrow-contract + guardrail flow.

Note: full end-to-end verification (fresh MIL-STD-1587E upload through both phases, ~80 artifacts) was not run this session — deferred to next session as roadmap node `fwd-two-phase-verify` (see master-corvus roadmap).

---

## Known Limitations (Carried Forward)

1. **Wrong-but-valid placements** — Layer 1 validation checks that `parent_id` exists and `department` is real; it does NOT check that the chosen parent is semantically appropriate for the artifact's subject. An "aluminum heat-treatment" artifact landing under an "elastomer seal" parent passes every guardrail. Caught only at human approval. Tracked in `fwd-hallucination-review`.

2. **Opus cost and wall-time per artifact** — observed 3-5 minutes of Claude CLI time per placement (5-8 tool turns with Opus extended thinking). A full ~80-artifact ingest runs ~4-6 hours wall-time, ~$30-50 cost. Acceptable at ingest frequency (rare per document, 5-year authoritative lifetime).

3. **Sonnet is not a viable fallback** for the placer — it fails reliably in hallucination mode. If Opus capacity becomes an issue, a deterministic service function (one Sonnet call per artifact with structured JSON output, committed by code) was scoped as "Option C" but not built. Would replace the agent entirely rather than tuning it further.

4. **Chunked fallback path** (for documents > 600k chars / ~150k tokens) still produces fully-classified proposals directly at extraction time (no Phase 2). Kept as a safety net; no doc has triggered it yet. Revisit when first hit.

5. **Phase 2 orchestrator is sequential** — 80 artifacts × 4-5 min each is substantially longer than parallel execution would allow. Chose sequential for predictability. Revisit if ingest time becomes a user-visible problem.

6. **Auto-chain robustness** — Phase 2 is auto-triggered from Phase 1's completion within the same background task. If the backend restarts mid-ingest, artifacts remain in `state='artifact'`; re-running the same job (or manually triggering `neuron_placer`) resumes from where it left off. Not explicitly documented in end-user-facing docs.

---

## Roadmap Impact

Two new master-corvus roadmap nodes added during this session, both in the `forward` section:

- `fwd-hallucination-review` — audit hallucination risk across all Corvus agents and promote the runtime guardrail framework from neuron_placer-specific to universal. Includes verification matrix for each agent.
- `fwd-two-phase-verify` — end-to-end verification of the two-phase pipeline against a fresh MIL-STD-1587E upload. 19-step verification array; includes SQL queries, endpoint calls, and three possible verdicts (ship / tune / revert) with follow-up actions per verdict. Foundational — downstream citation/ranking/reingest features inherit the invariants this pipeline establishes.

Master Corvus roadmap-state.json at version 21 (forward section height: 1280).

---

## Commits

| SHA | Scope |
|---|---|
| `a9b3169` | Knowledge → Agents admin page (pre-session work that spilled into this session's opening) |
| `c49a1c3` | Phase 1 — whole-doc single-pass extraction, default model → Opus |
| `62d4137` | Phase 2 — narrow-contract neuron_placer agent + three-layer guardrails + Opus switch |

All pushed to `private/main`. Not pushed to `origin/aurora` (per Corvus git policy — public updates are manually controlled).

---

## What Next Session Should Read First

1. This file (SESSION-5-HANDOFF.md) for what was built + why.
2. `~/Projects/corvus/CORVUS-STATUS.md` for overall project state (not updated this session — pending verification).
3. Master Corvus roadmap node `fwd-two-phase-verify` prompt for the exact verification steps.
4. `~/.claude/plans/now-back-to-the-reactive-cocke.md` for the detailed plan that was executed (already superseded by what was shipped, but useful for "why this shape").
5. Memory entries `project_corvus_rlm_eval.md` and `project_corvus_ingest_architecture.md`.
