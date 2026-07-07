# Corvus — Grounding & Anti-Hallucination Backlog (fresh-session kickoff)

This doc is a **follow-along for a new session**. It carries the context needed to
continue the citation/grounding work without re-deriving it. Read top to bottom,
then pick a feature from §6.

---

## 1. Orientation (read first)

- **Repo:** `~/Projects/corvus` — unified multi-tenant neuron graph for prompt
  prep. Full architecture + conventions: `~/Projects/corvus/CLAUDE.md` (read it).
- **Active tenant:** `corvus-aero` (aerospace/defense). Domain config in
  `backend/tenants/corvus-aero/`.
- **Run backend:**
  `cd ~/Projects/corvus/backend && source venv/bin/activate && TENANT_ID=corvus-aero PORT=8002 uvicorn app.main:app --port 8002 --reload`
  (restart after `config.py` changes — settings are instantiated at import).
- **Run frontend (dev):** `cd ~/Projects/corvus/frontend && VITE_API_PORT=8002 npm run dev`
  (needs `source ~/.config/nvm/nvm.sh && nvm use 22.22.0` for build/tsc). The built
  UI is also served by the backend at `http://localhost:8002`.
- **Tests:** `cd backend && TENANT_ID=corvus-aero python -m pytest tests/ -q` (≈421 pass).
- **tsc/build:** `cd frontend && npx tsc -b && npm run build`.

### Hard conventions (non-negotiable)
- **LLM = Claude CLI only** (personal subscription, no API SDK/credits). All calls
  go through `llm_provider._anthropic_chat` (subprocess). CLI must be isolated:
  `cwd=/tmp`, strip `CLAUDECODE*`/`CLAUDE_CODE_*` env, `--strict-mcp-config`,
  `--no-session-persistence`, `--system-prompt`. See CLAUDE.md "LLM Provider Policy".
- **NASA lint** runs on every backend edit + pre-commit: `python scripts/nasa_lint.py <files>`.
  Strict: no recursion, bounded loops, no mutable UPPER_CASE globals (use tuples /
  MappingProxyType), no bare except, functions ≤100 lines (≤60 guideline).
- **Git:** push **only** to `private` (never `origin`). Force-push needs the user's
  explicit OK. **Never commit:** `backend/backup.log`, `frequency-hopping.md`,
  `readables/*.pdf`, `SESSION-*-HANDOFF.md`. `frontend/dist/` is gitignored.
  Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## 2. Recall/answer architecture as it stands now

- **Recall is embed-only ("cheap").** The per-query LLM classifier was DELETED
  (`classifier.py`, `ClassifyStage`, `AdaptiveClassifyStage` gone) — it was slow
  (~18s extended-thinking), sometimes empty, and lower-quality than the free
  neighbor-vote. `settings.recall_mode = "cheap"` is the only registered mode.
- **Answer generation:** `execute_query` runs slots concurrently
  (`asyncio.gather`). `/query/stream` (SSE) emits pipeline **stage** events and a
  per-slot **`slot_result`** event the moment each slot finishes (progressive
  population — fast models first). Note: the CLI's `stream-json` emits whole
  content blocks, NOT token deltas, so there is no true token streaming.
- **Reasoning effort:** per-request `low|medium|high` via `effort_var` (ContextVar)
  → `--effort`. Default `low`. Note: empirically `--effort` is noisy at reducing
  Haiku's thinking; don't over-trust it.

## 3. Grounding pipeline (the subsystem this backlog extends)

Two failure modes, two guards, two Query Lab badges (footer row of each slot card,
next to `$cost`/`tokens`, shown only when count > 0):

1. **Fabricated citation KEYS** → `⚠ N fabricated` (red-orange). Frequency-hopping:
   each neuron/engram in the prompt gets a secret per-query key `[FQ-XXXXXX]`; any
   cited key not in the map is fabricated. Stripped per-slot. `citations_fabricated`.
2. **Ungrounded authority REFERENCES** → `◇ N ungrounded` (gold). A standard/reg
   named in prose (`per MIL-STD-1521`) that isn't in the retrieved context — the
   hop layer can't see it (not an `[FQ]` key). Counted per-slot. `ungrounded_refs`.

Plus a **prompt guardrail** telling the model not to name any reg/standard as
authority unless it carries a citation key (kept generic — no example numbers, so
it doesn't pollute the grounding context).

## 4. Key files & seams

| Concern | File · symbol |
|---|---|
| Hop map + verify + strip + repair-instruction | `backend/app/services/citation_hopping.py` (`mint_hop_map`, `verify_citations`, `strip_hallucinated`, `extract_citation_tokens`, `repair_instruction`, `HopMap`) |
| Per-answer exit (verify→strip→count), used per-slot | `backend/app/services/executor.py` · `_clean_answer_citations` |
| Per-slot integration + badges' source data + SSE emit | `executor.py` · `_execute_slot` (sets `citations_fabricated`, `ungrounded_refs`; emits `slot_result`) |
| Primary audit-session persistence | `executor.py` · `_apply_citation_hop_exit` |
| Repair-mode retry (one bounded re-cite) | `executor.py` · `_repair_citations` |
| Standard/reg extraction + grounding count | `backend/app/services/regulatory_coverage.py` (`extract_standard_refs`, `count_ungrounded_refs`, `extract_cfr_refs`) |
| Citation instruction / guardrail | `backend/app/services/prompt_assembler.py` · `_append_citation_instruction` |
| Config knobs | `config.py` (`citation_hop_failure_mode="strip"`, `citation_hopping_enabled`, `citation_hop_require_all`, `default_effort`) |
| SSE endpoint | `backend/app/routers/query.py` · `post_query_stream` (on_stage → queue → `event_generator`) |
| Existing grounding check (entailment seam for §6.4) | `backend/app/services/input_guard.py` · `check_output_grounding` |
| Query Lab badges + stream handling | `frontend/src/components/QueryLab.tsx` (`ModelCard` footer; `submitQueryStream` `slot_result` handler) |
| Slot result type | `frontend/src/types.ts` · `SlotResult` |
| Badge styles | `frontend/src/App.css` (`.fabrication-badge`, `.ungrounded-badge`) |

## 5. How to verify grounding changes

- **Unit (fast, no LLM):** import the pure fns and assert on synthetic strings, e.g.
  `count_ungrounded_refs("... per MIL-STD-1521 ...", context)` → expected count.
- **Live SSE:** `curl -sN -X POST localhost:8002/query/stream -H 'Content-Type: application/json'
  -d '{"message":"...","slots":[{"mode":"haiku_neuron"}],"effort":"low"}'` → parse
  `data:` lines; the `slot_result` event carries `citations_fabricated` +
  `ungrounded_refs`. (A single haiku slot answers in ~30–120s.)
- **Gotcha:** `query.assembled_prompt` (DB column) is often **empty** — do NOT use it
  as the grounding context in tests. The live path uses `ctx.system_prompt` (the
  real assembled prompt with packed source content). `PreparedContext.system_prompt`.
- **Gotcha:** keep the guardrail instruction **generic** — listing concrete standard
  numbers in it would make them appear in `ctx.system_prompt` and be treated as
  "grounded," causing false negatives.

## 6. Pending features

*(all shipped 2026-07-07 — see §7; kept for reference)*

## 7. Shipped so far (context, not to redo)
- **§6.5 Primary answer quality floor** — `primary_answer_effort` (default `medium`,
  acts as a floor: never lowers an explicit higher request effort; `""` disables)
  + `primary_answer_model` (`""` = keep slot model; must be a MODEL_REGISTRY key).
  Applied to slot 0 only, inside its asyncio task context so the `effort_var` bump
  can't leak to compare slots (`executor._apply_primary_overrides`).
  Tests: `tests/test_primary_answer_overrides.py`.
- **§6.3 Inline ungrounded-ref marks** — backend now returns
  `ungrounded_ref_list` (normalised refs) per slot alongside the count
  (`regulatory_coverage.list_ungrounded_refs`); frontend
  (`src/standardRefs.ts`, pattern port — keep in sync with backend) wraps
  occurrences in `<mark class="ungrounded-ref">` (gold dotted underline) in all
  QueryLab answer renders. Backend decides WHICH refs are ungrounded; TS only
  locates them.
- **§6.4 Entailment check** — opt-in (`entailment_check_enabled`, default OFF;
  knobs: `entailment_check_model/max_claims/source_chars`). One batched judge
  call on the PRIMARY answer: sentences citing `[FQ]` keys are checked against
  their cited neuron/engram content (`services/entailment_check.py`,
  hop map loaded via `Query.citation_hop_session_id`). Advisory only — attaches
  `entailment` to `output_checks[0]`, SSE stage `entailment_check`, QueryLab
  badge "Entailment: n/m supported". Live-verified: caught a real
  cited-source-doesn't-say-that (MIL-STD-882E) on query 497.
  Tests: `tests/test_entailment_check.py`.
- Per-slot citation exit + `⚠ fabricated` badge; `citation_hop_failure_mode` → `strip`. (commit 1c9cde2)
- Ungrounded-reference detection + `◇ ungrounded` badge + generic guardrail. (commit 572135c)
- Progressive per-slot streaming. (8a11ce0) · Effort control. (7fdd2ed) · Classifier deletion. (f669f2c)
