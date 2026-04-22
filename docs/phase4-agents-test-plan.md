# Phase 4 Agents — Verification Test Plan

**Scope:** Co-ship of Pattern #201 (A-Base) + #202 (A-Dedup) + #208 (A-UI).
**Target commit:** first merge of `backend/app/agents/`, `backend/app/routers/agents.py`,
`frontend/src/components/AgentsPage.tsx`.
**Pre-agent eval baseline:** `tenant=corvus-aero`, `certified_eval_run_id=3`,
`suite=smoke`, `hash=5212806f355e0ed05f2b`, `scoring_engine_version=1.0.0`,
`certified_at=2026-04-17`. Any regression in this run after merge is a blocker.

## 1. Automated tests (pytest)

Run from `backend/` with venv active:
```bash
TENANT_ID=corvus-aero pytest tests/test_agents_tool_base.py \
    tests/test_agents_registry.py tests/test_agents_runtime.py -v
```

**Expected:** 27/27 passed. Covers:

| File | What it proves |
|------|----------------|
| `test_agents_tool_base.py` | ToolRegistry fail-closed on unknown/duplicate names; Tool dataclass frozen; snapshot is read-only MappingProxy. |
| `test_agents_registry.py` | YAML loader rejects unknown top-level keys, missing required keys, duplicate tools in allow-list, and unknown tool names (fail-closed). Load is idempotent. |
| `test_agents_runtime.py` | `_parse_envelope` handles plain JSON, JSON-in-prose, multiline JSON, nested objects; raises `AgentProtocolError` on empty/malformed input. `ToolNotAllowedError` is a real Exception subclass. |

## 2. Static / lint gates

```bash
python3 scripts/nasa_lint.py backend/app/agents/ backend/app/routers/agents.py \
    backend/tests/test_agents_tool_base.py \
    backend/tests/test_agents_registry.py \
    backend/tests/test_agents_runtime.py
```

**Expected:** `NASA lint: all checks passed.` (strict + guideline). No recursion,
every loop bounded, no mutable module-level dicts, no bare `except`, no function
over 100 lines (strict) or 60 lines (guideline — flag but not blocker).

Frontend type-check:
```bash
cd frontend && source ~/.config/nvm/nvm.sh && nvm use 22.22.0 && \
    npx tsc --noEmit -p tsconfig.app.json
```
**Expected:** no NEW errors. Pre-existing errors in `IntegrityPage.tsx` and
`ProposalQueuePage.tsx` are unrelated and tracked separately.

## 3. Live service smoke (requires DB + Claude CLI)

### 3.1 Startup loads registry cleanly

```bash
cd backend && source venv/bin/activate
TENANT_ID=corvus-aero PORT=8002 uvicorn app.main:app --port 8002
```
**Expected:** server starts, log line mentions `/v1/agents` router mounted, no
registry-load errors. In a second shell:
```bash
curl -s -H "X-Corvus-Role: reader" http://localhost:8002/v1/agents | jq .
```
**Expected:** JSON array with one entry (`dedup`), `tool_count == 6`,
`manual_trigger == true`, `schedule_enabled == false`.

### 3.2 Agent detail endpoint

```bash
curl -s -H "X-Corvus-Role: reader" http://localhost:8002/v1/agents/dedup | jq .
```
**Expected:** includes `tool_allow_list` array of 6 strings and
`system_prompt_preview` non-empty.

### 3.3 RBAC gate on trigger

```bash
# reader role — must fail 401/403
curl -i -X POST -H "X-Corvus-Role: reader" \
    -H "Content-Type: application/json" -d '{}' \
    http://localhost:8002/v1/agents/dedup/run
```
**Expected:** non-2xx, RBAC rejection.

### 3.4 Admin trigger on clean DB

With the admin role header:
```bash
curl -s -X POST -H "X-Corvus-Role: admin" \
    -H "Content-Type: application/json" -d '{}' \
    http://localhost:8002/v1/agents/dedup/run | jq .
```
**Expected:** 200 OK, body includes `action_id`, `agent_name: "dedup"`,
`turns >= 1`, `tool_calls >= 1`, `summary` non-empty. If no pending
`near_duplicate` findings exist, agent should emit `done` quickly with a
summary like "no findings to process."

### 3.5 Run history + child-action trace

```bash
curl -s -H "X-Corvus-Role: reader" \
    "http://localhost:8002/v1/agents/runs?limit=5" | jq .
curl -s -H "X-Corvus-Role: reader" \
    "http://localhost:8002/v1/agents/runs/<action_id>" | jq .
```
**Expected:** run list contains the run from 3.4. Detail view includes
`child_actions[]` with one entry per tool call; each has
`tool`, `state` (`applied`/`failed`), `input`, `result` or `error`, `reason`.

### 3.6 Dedup integration (seeded data — covers Task #52)

Seed two `IntegrityFinding` rows of `finding_type='near_duplicate'`:

- **Case A — clear duplicate:** two neurons with ~identical labels and content
  (e.g., copy neuron X to a new row with trivial whitespace diff). Cosine
  similarity should land >= 0.95.
- **Case B — clear distinct:** two neurons that share a vocabulary word but
  cover different concepts (e.g., "DCAA audit preparation" vs. "GAAP revenue
  recognition"). Cosine should be <= 0.75.
- **Case C — borderline:** two related but non-identical policy clauses
  (similarity 0.80–0.90). Forces the `compare_neurons_semantic` LLM branch.

Then trigger the agent (3.4) and verify in the DB:

```sql
SELECT id, kind, state, reason,
       input_json->>'tool' AS tool_name, error_message
  FROM actions
  WHERE parent_action_id = <root_action_id>
  ORDER BY id;
```
**Expected per case:**
- Case A → `agent.tool.mark_duplicate` child action in `applied` state, a new
  `autopilot_proposals` row with `proposed_action='merge_neurons'` and
  `state='proposed'`. Neurons themselves are UNCHANGED (human approval gate
  preserved).
- Case B → `agent.tool.mark_reviewed_as_unique` with
  `resolution='differentiated'`. Finding closes; no neuron mutation.
- Case C → `agent.tool.compare_neurons_semantic` invoked first, then either
  branch based on the LLM classification. Reasoning text (`reason` column)
  cites embedding similarity zone.

### 3.7 Tool-not-allowed fail-closed

Temporarily add a tool name the dedup agent does NOT allow-list (e.g.,
`merge_neurons_direct` if such a tool existed) to test the allow-list gate.
Quickest way without editing YAML: craft an LLM prompt that is likely to
trigger a non-allow-list tool call. The runtime must:
- create a child Action with `state='failed'`, `error_message` starts with
  "Tool … is not in agent … allow-list",
- feed the error back to the LLM,
- continue or terminate cleanly within `max_turns`.

No neuron mutation must occur regardless of LLM output.

### 3.8 Turn-cap bound

Set `max_turns: 2` in `dedup.yaml` temporarily, seed five pending findings,
trigger a run. Expected: run terminates at 2 turns regardless of remaining
work; final action `state='applied'`, `result_json.turns == 2`,
`result_json.summary` reflects incomplete processing. Revert the YAML after.

### 3.9 UI smoke — Frontend /agents page

```bash
cd frontend && npm run dev  # port 5173, proxies to 8002
```
Navigate **Autopilot → Agents**. Verify:
- [ ] Registered agents table shows `dedup` with all columns populated.
- [ ] "Run now" is enabled; clicking it triggers a run and refreshes the
      history table (run appears at top with `state='applied'`).
- [ ] Run history table shows rows from 3.4, 3.6, 3.8; error runs (from 3.7)
      are highlighted with warm background.
- [ ] Agent filter + state filter both narrow results correctly.
- [ ] Clicking a run opens the modal; tool-call table has non-empty
      `Reason`, `Input`, `Result`/`Error` cells.
- [ ] Error bar appears on trigger failure (e.g., attempting `Run now` with
      reader-role header via devtools).

## 4. Regression — pre-agent baseline still holds

After all tests:
```bash
cd backend && TENANT_ID=corvus-aero python -m app.eval.run_smoke \
    --suite smoke --tag post-p4-201-202-208
```
Compare the post-merge run score against `certified_eval_run_id=3`.
**Expected:** mean score delta within ±0.02, no regression in any case
marked `must_pass=true` in the suite.

## 5. Hand-off checklist (what must be green to call Phase 4 step 1 done)

- [ ] Section 1 — 27/27 pytest green.
- [ ] Section 2 — NASA lint + TypeScript compile clean (no new errors).
- [ ] Section 3.1 — registry loads at startup.
- [ ] Section 3.2–3.3 — read endpoints + RBAC.
- [ ] Section 3.4 — admin trigger returns well-formed envelope.
- [ ] Section 3.5 — child actions visible with reason + input + result.
- [ ] Section 3.6 — all three dedup cases land on the correct tool and
      produce proposals (never direct merges).
- [ ] Section 3.7 — allow-list fail-closed verified under attack.
- [ ] Section 3.8 — turn cap bounds execution.
- [ ] Section 3.9 — UI end-to-end usable.
- [ ] Section 4 — baseline eval regression within tolerance.

## 6. Known follow-ups (out of scope for this session)

- Schedule-driven trigger (currently only manual). When `schedule.enabled: true`
  is set, we still need a scheduler process to call `execute_agent` on a cron.
  That lands with Pattern #4 scheduler work or a standalone systemd timer.
- Second-wave agents (#203 autopilot-curator, #204 integrity-triager,
  #205 ingest-refiner) — all have Pattern #6 (ontology branching) as a soft
  prereq per the roadmap-state.json node annotations.
- Full end-to-end dedup integration as an automated test (requires live
  Postgres + Claude CLI in CI). Currently covered as manual §3.6.
