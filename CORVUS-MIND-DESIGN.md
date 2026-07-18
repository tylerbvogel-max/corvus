# Corvus-Mind — Agentic Memory Tenant (Design & Kickoff)

**Date:** 2026-07-10 · **Status:** Proposed, review-gated (roadmap node `fwd-corvus-mind` in master-corvus, prereq: north-star retrospective)
**One-liner:** A new Corvus tenant that serves as the institutional-memory organ for Claude Code (and any future harness), accumulating verified, situated agentic experience that survives model swaps.

---

## 1. Thesis & framing

- The 5-year "holy grail" harness property is **institutional memory**: accumulated, verified, private knowledge living outside model weights, with swappable models on top. Harness loops are commodity (~200 lines); the memory substrate is the durable, defensible layer.
- **Corvus is the organ, not the organism.** Do not fork Claude Code or build "corvus-code" as a harness. Mount Corvus into Claude Code via its *supported extension surfaces only*: MCP config, hooks, and the skills directory. This inherits permissions, models, UX, and co-trained agentic instincts for free, and keeps Corvus portable to any future harness (~50 lines of hook glue are the only harness-specific part).
- Fits the two-track strategy: personal-Corvus hardening, clean IP. Wedge story: *"your agents get smarter every week, and the knowledge survives model swaps."*
- Slots into GTM as proof of the domain-agnostic claim: agentic self-knowledge is just another tenant.

## 2. The critical content decision: episodes, not best practices

**Do NOT load curated "how agents should act" knowledge.** General agentic competence (batch tool calls, write scripts instead of N calls, verify before claiming done) gets absorbed into model weights via RL within a model generation — it depreciates on every release.

**DO load situated, episodic experience with outcomes** — the knowledge weights structurally cannot eat:

- "Fresh-DB alembic migrations fail at 017; `Base.metadata.create_all` works (evidence: exit 0 after)"
- Tool-usage traces with outcomes: tool + args-shape + context → success/failure, cost, latency
- User corrections and preferences ("never suggest browser cache clearing; restart the dev server")
- "Last three times `tenant_config` changed, `test_recall.py` broke first"

This is non-transferable, non-trainable-away, and compounds. (Today's flat memory files hold exactly this kind of content — but with no structure, no decay, no verification. That gap is the product.)

## 3. Architecture overview

```
Claude Code (unmodified harness)
│
├── READ  path: Corvus MCP server (cheap recall) + SessionStart/UserPromptSubmit hooks
├── WRITE path: PostToolUse/Stop hooks → episodes.jsonl → session-end distiller → reviewer gate
└── MAINTENANCE: systemd timers (autopilot pattern) → janitor agents
                        │
              Corvus, TENANT_ID=corvus-mind (fresh graph, own DB)
```

**Deliberate asymmetry:** reads are synchronous and free (felt every agent step); writes are asynchronous and expensive (felt never). Never put an LLM in the recall hot path — cheap mode (~150ms embed-only, $0) is the budget.

### 3.1 Tenant, not new system

- `TENANT_ID=corvus-mind`, own Postgres DB, domain config in `backend/tenants/corvus-mind/` — architecturally identical to how corvus-flow became a tenant.
- Fresh graph. Do **not** mix with corvus-aero (the aerospace graph is a regression testbed; keep both clean of each other).
- **Node types:** `episode`, `lesson`, `tool-profile`, `context-scope`.
- **Edge types (temporal semantics are first-class):**
  - `supersedes` — with was-true-until history retained (old memory demoted, not deleted; "this used to be different" is itself useful context)
  - `scoped-by` — conditions a lesson on a context node (repo, project, environment)
  - `evidence-link` — episode → lesson provenance

### 3.2 Read path

- **MCP server** (Corvus already speaks MCP). Minimal tool surface per the anticipated-use rule — just two tools:
  - `recall(query, context)` — cheap mode only
  - `remember(lesson, evidence)` — explicit saves by the model
- **SessionStart hook** — inject top-weighted memories for cwd/project at launch (a ranked + decayed MEMORY.md analog).
- **UserPromptSubmit hook** — run cheap recall against the incoming prompt, inject hits as a system-reminder. This is *ambient memory*: zero model cooperation required, which flat files can't do.

### 3.3 Write path (evidence-gated — this is the whole trust story)

Bad memory is worse than no memory (confidently wrong forever). Nothing writes to the graph directly from a live session.

1. **PostToolUse / Stop hooks** append raw events to a local `episodes.jsonl`: tool, args summary, exit code, duration, error text. Deterministic, no LLM — essentially a `jq >>` one-liner. Cheap enough to run on everything.
2. **Session-end distiller** (triggered by Stop hook, or batched by the autopilot timer). Reads episode log + transcript, extracts *candidate* lessons with evidence links. Runs on **Opus via Claude CLI** (quality-first-backend rule; never the API SDK — no credits). Remember the CLI subprocess gotchas: strip `CLAUDECODE*` env, cwd=/tmp, `--strict-mcp-config`, prompts via stdin.
3. Candidates enter the graph at **provisional weight**; the ingest-reviewer-agent pattern gates promotion to full weight. Promotion criteria = attached verifiable outcome (test passed, task verified, user confirmed).

### 3.4 Maintenance: janitors (existing pattern, upgraded semantics)

Run off systemd timers exactly like `corvus-autopilot.timer`.

| Janitor | Aerospace semantics | Corvus-mind semantics |
|---|---|---|
| Duplicate detector | Hygiene: merge & discard | **Consolidation trigger**: N near-dup lessons across sessions = N confirmations → fuse into one high-weight engram, keep episodes linked as provenance. Accumulate weight, don't discard. Front half of episodic→semantic consolidation. |
| Disagreement detector | Someone's wrong; resolve toward truth | **Staleness engine**: check timestamps/evidence recency first; prefer *supersede-with-history* over merge/delete (world changed ≠ someone wrong). Third verdict: **SCOPING** — contextual truths (true in repo A, false in repo B) pushed from global to conditioned-on-context via `scoped-by` edges rather than merged or deleted. |
| Decay auditor (**new**) | — | Hunt high-weight-but-rotten nodes: recalled often but never acted on, or acted on with worsening outcomes. Recall frequency alone keeps zombie memories warm; agentic facts rot fast (tools update, repos refactor). |
| Compiler (**new**) | — | Stable, strong consolidated engrams **emit skill files into `~/.claude/skills/`** — consolidated memory becomes progressive-disclosure playbooks the harness loads natively. Closes the self-extension loop with zero harness modification. Reverse check: when a compiled skill's source engrams get superseded, flag the artifact for regeneration. Graph = source of truth; skills/tools = build output. |

**Compounding trick:** janitor actions are themselves episodes (a merge later split = bad merge; an escalation resolved in 2s = wasted escalation; a supersession re-contradicted = misfired staleness heuristic). Feed them back → the curation layer learns its own thresholds. Cheap here because verdicts are abundant, unlike aerospace where human verdicts are sparse/expensive.

### 3.5 HITL restructured: from write-path gate to policy plane

The oracle inverts: in aerospace, the human expert is the only ground truth. In agentic memory, **the environment is the oracle** — memories are born with evidence and re-tested for free every time they're recalled and acted on. So:

1. **Auto-accept** — evidence-backed memories enter at moderate weight; recall-outcome cycles adjust weight with no human involvement.
2. **Escalation queue** — janitors surface only what can't self-resolve (ambiguous disagreements, scoping calls). Reuse the existing review-queue UI; admission criterion changes from "new item, verify" to "system has a specific problem only judgment can settle."
3. **Policy, not instances** — the durable human role: e.g. "destructive-op memories need N confirmations before auto-apply," "user-preference memories outrank efficiency memories," "nothing crosses project/tenant boundaries." Human curates the *constitution*; the system curates the memories.

User-role summary: agent is the primary reader/writer; the human shifts from primary knowledge author (aerospace mode) to **governor of a self-authoring system** (mind mode). This is the fair test of the walk-away-from-it property.

## 4. Build order

1. **Tenant + schema** — `corvus-mind` domain config, node/edge types above.
2. **Episode-logging hooks** — deterministic, no LLM. Validate the data is good *before* spending anything on distillation.
3. **MCP recall + SessionStart injection** — at this point it already beats flat-file memory.
4. **Distiller** (Opus via CLI, evidence-linked candidates, provisional weight).
5. **Janitors** (consolidate / supersede+scope / decay).
6. **Compiler** (engram → skill file emission + regeneration checks).

## 5. Bootstrap

One-time backfill ingest from existing raw material on this machine:

- `~/.claude/projects/*/` session transcripts (months of episodes)
- Existing flat memory files (`~/.claude/projects/-home-tylerbvogel/memory/`)

Gives the fresh graph a seed population and — more valuably — gives the janitors a real, messy corpus to be tested against on day one (graph-as-testbed rule: real data over synthetic; keep bad rows as regression evidence).

## 6. Design principles to hold (from the harness discussion)

- **A fantastic harness differs in what it refuses to make the model do.** Move deterministic work out of the token stream into structure. Corvus-mind is that principle applied to memory.
- **Progressive disclosure over monolithic prompts** — never a giant rule library in context; one-line triggers, on-demand bodies. The compiler targets exactly this mechanism.
- **MCP tool surfaces stay minimal** — expose only tools with anticipated use; schemas are context cost.
- **Verification is what makes memory writable** — memory and verification are not separate features.
- **Where policy attaches:** dangerous/oversight-worthy operations = named tools with per-tool policy; bulk data/iteration = code outside the context window.

## 7. Constraints & gotchas (project-level)

- Review-gated: do not start until the north-star retrospective settles direction. Roadmap node: `fwd-corvus-mind` in `master-corvus/public/roadmap-state.json` (prereq + edge from `north-star`).
- All LLM calls via Claude CLI (personal subscription), never the Anthropic API SDK.
- Corvus git: push only to `private` remote, never `origin`.
- Fresh throwaway DBs: use `Base.metadata.create_all`, not alembic (breaks at 017); drop whole DB, not `drop_all`.
- Don't re-add LLM classification to the default recall path (cheap mode is a settled decision).

---

## 8. Adversarial design review (2026-07-10, pre-build)

Grounded in a three-pass codebase audit (tenant anatomy, read/write machinery, harness-side survey). Overall verdict: **the architecture holds** — the reusable primitives this doc leans on mostly exist — but five assumptions were wrong or half-right, four amendments are adopted below, and two hard problems are unsolved and must shape phases 4–5.

### 8.1 Assumption audit

| # | Design assumption | Ground truth | Verdict |
|---|---|---|---|
| 1 | Cheap recall: ~150ms, embed-only, $0 | Confirmed — `CheapClassifyStage` is the **only** registered recall mode; zero LLM end-to-end (`pipeline/stages/__init__.py:22`) | EXISTS |
| 2 | Review-queue UI reusable for escalations | Confirmed — `routers/proposals.py` + `ProposalQueuePage.tsx` et al. | EXISTS |
| 3 | "Ingest-reviewer-agent pattern" gates candidates | Exists but renamed `neuron_placer` (2026-04-23); gates `artifact → proposed` into the *human* queue; never writes the graph directly. Pattern is directly reusable for lesson candidates | EXISTS-BUT-DIFFERENT |
| 4 | Candidates "enter at provisional weight" | **No such write parameter.** `avg_utility` is hardcoded 0.5 (`models.py:75`); the routing axis is `authority_level` + write-gate tier, weight moves only post-hoc (eval A/B wins, explicit ratings, decay) | WRONG → Amendment B |
| 5 | Tenant-defined node types (`episode`, `lesson`, …) | `abstraction_type` is a free string, BUT engine predicates hardcode the six existing values (`gap_detector.py:38-50`, `reconciler.py:477`, `consolidation.py:53`) — novel values are invisible to exactly the janitor machinery we want | HALF-RIGHT → Amendment A |
| 6 | Temporal edge types (`supersedes`, `scoped-by`, `evidence-link`) | Store fine (free column), but spread-activation treats unknown types as **pyramidal** (`adjacency_cache.py:22`, `neuron_service.py:856-863`) — a `supersedes` edge would *boost* the superseded node. Edge-level was-true-until history is net-new; only `Neuron.superseded_by` (`models.py:88`) exists | HALF-RIGHT → phase-5 code |
| 7 | MCP surface: two tools, `recall` + `remember` | **No write tool exists at all** (9 read-only tools); tool set is fixed at import time, not per-tenant. Needs a mind-specific MCP module (phase 3). Bonus find: pre-existing `NameError` bug in `query_graph` (`mcp_server.py:144`) | MISSING → phase-3 code |
| 8 | "Environment is the oracle" — recall-outcome loop re-tests memories for free | **Does not exist.** `record_firing` only bumps `invocations`; no organic recalled→acted-on→outcome signal anywhere | MISSING → §8.3.1 |
| 9 | Janitors "transfer wholesale" | Detectors exist (`dedup` agent, `conflict_monitor`, reconciler) but janitors are hardcoded `_run_*_if_due` calls in `/tick`, verdicts are merge/differentiate/dismiss — no SCOPING, no supersede-with-history. Semantics upgrades are real code | EXISTS-BUT-DIFFERENT |
| 10 | Consolidated lessons = "engrams" | **Name collision.** In code, `Engram` = external-regulation retrieval index resolved via eCFR (`engram_service.py`). | WRONG → Amendment C |
| 11 | Episode hooks ≈ "a `jq >>` one-liner" | `jq` is not installed on this machine; hooks must be `python3` stdin readers (the NASA-lint hook pattern) | DETAIL |
| 12 | Hooks/skills surfaces are available | Confirmed greenfield: **zero** hooks configured anywhere; `~/.claude/skills/` doesn't exist yet | CONFIRMED |
| 13 | Bootstrap corpus exists | Confirmed: 21 sessions / 96 MB JSONL in the home project alone (2026-06-13→07-10), clean typed-event schema with `parentUuid` DAG; + 29 flat memory files | CONFIRMED |
| 14 | Tenant onboarding is config-only | Confirmed — `tenant.yaml` (4 required keys) + 7 required modules + `corvus_org.yaml`; DB URL auto-derives (`corvus-mind` → `corvus_mind`); schema self-creates on first boot via `create_all`. Six tenants already exist, flow is the template | CONFIRMED |

### 8.2 Amendments (adopted)

- **A. Node types ride `node_type`, alias `abstraction_type`.** `node_type` (free `String(50)`) carries the mind vocabulary; `abstraction_type` maps onto the existing six so every engine predicate keeps working: `episode→artifact` (decay-reclaimable — episodes *should* decay), `lesson→principle`, `tool-profile→procedure`, `context-scope→structural`. Zero service-code fork, and consolidation's artifact-tier reclamation gives episodes the right lifecycle for free.
- **B. "Provisional weight" reframed onto existing machinery.** Candidates enter as write-gate-routed proposals at `authority_level=informational` (the bottom of `AUTHORITY_RANK`, which the gate code itself calls "observational by definition"; auto-commit tier) and are reclaimed by consolidation decay if never reinforced. Promotion ladder: informational → guidance (evidence-backed) → organizational (user-confirmed). Promotion = authority escalation + utility reinforcement, not a numeric knob. No new write parameter needed through phase 4.
- **C. Naming.** Consolidated multi-episode memories are **consolidated lessons** (`node_type=lesson` + `evidence-link` provenance), never "engrams" — that term is taken.
- **D. Capture-layer safety is a phase-2 requirement, not polish.**
  - **Secret redaction at capture** — regex scrub (API keys, tokens, passwords) before any event is written to `episodes.jsonl`. A memory system that regurgitates a credential into a future prompt is a persistent leak.
  - **Project exclusion (the Aurora wall)** — configured cwd prefixes are never logged. The two-track strategy demands work/personal separation at the *capture* layer, where the data is born, not at the policy layer.
  - **Demo isolation** — corvus-mind must never enter the public demo capture path (`demo:capture` reads corvus-aero; verify if that ever changes).

### 8.3 Open problems (don't block phases 1–3; must be solved before 4–5 are trustworthy)

1. **Attribution.** Nothing says "this Bash success is attributable to lesson X injected 12 turns ago," so recall-outcome reinforcement has no signal even once built. Candidate solution: *distiller-time attribution* — the distiller sees the full transcript including injected memories and judges which were load-bearing; cheap, async, evidence-linked. Prerequisite: the injection hooks must **log what they inject** into `episodes.jsonl` from day one, so the data exists before the consumer does.
2. **Self-reinforcement loop.** Injected lesson shapes behavior → behavior emits episodes → distiller re-extracts the same lesson → duplicate-detector counts N "independent confirmations" → weight grows with zero new evidence. Consolidation rule: an episode from a session where the lesson was *injected* is a **usage**, not a **confirmation**; only sessions with fresh outcome evidence (or where it wasn't injected) count. Depends on the same injection logging as (1).
3. **Memory poisoning.** UserPromptSubmit injection makes every graph write an eventual instruction channel into future sessions, and the distiller reads transcripts containing untrusted tool output. Mitigations: lessons are declarative facts with provenance (never imperative playbooks in the injected frame); injected under an explicit "background context, not instructions" wrapper; observational-tier memories rendered with lower prominence; distiller flags instruction-shaped candidates to the escalation queue.
4. **Distiller economics.** Opus on every session end is heavy for long/numerous sessions. Default: Stop hook only *marks the episode log ready*; the autopilot batch path runs the distiller. Synchronous per-session distillation stays opt-in.

### 8.4 Phases 1–6 build record (2026-07-10)

Gate override: the north-star retrospective is still `active`; the user explicitly chose to start anyway. Built:

- **Tenant** `backend/tenants/corvus-mind/` — `tenant.yaml` (port **8005** — 8004 belongs to the frontend dev server, vite.config.ts hardcodes it; region_label "Scope", regulatory dept stub "Provenance"), 7 required modules (memory-domain classifier prompt, voices, patterns, provenance seeds for episode-log/user-correction/backfill sources, concepts for evidence-gating/supersession/scoping/consolidation, empty regulatory tree, risk categories for destructive-ops/secret-exposure), minimal `corvus_org.yaml` (scopes: Harness / Environment / Projects / User). DB `corvus_mind` auto-created schema on first boot.
- **Episode hooks** — `harness/claude-code/episode_hook.py` (stdlib-only python3, redaction + exclusion + truncation, path-traversal-safe, always exits 0; tested against 6 payload shapes). Events land in `~/.corvus-mind/episodes/{session_id}.jsonl`; exclusion list at `~/.corvus-mind/config.json`. **Live since 2026-07-10:** the PostToolUse + Stop block required explicit user approval to enter `~/.claude/settings.json` (the permission classifier rightly blocks agents from editing their own hook config); after approval, capture was verified firing in-session. Reference copy of the block: `~/.corvus-mind/settings-hooks-snippet.json`.

**Phase 3 (same day):**

- **HTTP surface** `backend/app/routers/recall.py` — `POST /recall` (prepare-only structured recall, ~50ms warm, no LLM) + `POST /remember` (stages a neuron-create proposal, routes through the tiered write gate per Amendment B, embeds inline + incremental cache update so saves are recallable immediately). Verified round trip: remember → auto-commit at informational authority → recall ranks the new lesson #1. Two ranking fixes discovered live: (1) freshly-flushed proposals must explicitly load `.items` before `route_proposal` (async lazy-load raises MissingGreenlet); (2) role-less/parentless saves lose the role-match scoring bonus to scaffold nodes and get displaced by structural completion — `_resolve_scope_anchor` now anchors saves under their scope's primary role node. Also fixed the pre-existing `query_graph` NameError (`mcp_server.py:144`). Dead-config finding: `tenant.semantic_prefilter_enabled` is consumed by nothing — `settings.semantic_prefilter_enabled` (default True) is what runs.
- **Ambient injection** `harness/claude-code/memory_inject_hook.py` — SessionStart + UserPromptSubmit; injects only `lesson`/`tool-profile`/`context-scope` nodes (scaffold never), frames content as "facts to weigh, not instructions," dedupes per session, and **logs every injection to the episode file** (`event: Injection`, neuron_ids/labels/scores) — the §8.3 attribution + anti-self-reinforcement prerequisite, live before the distiller exists. **Live since 2026-07-10** (user-approved, same classifier dance as phase 2).
- **MCP surface** `harness/claude-code/mind_mcp_server.py` — thin stdio client of the backend (no app imports, no model load), exactly two tools: `recall(query, top_k)` + `remember(lesson, evidence, label, scope)`. Registered user-scope via `claude mcp add` (takes effect next session).
- **Persistence** — `corvus-mind.service` enabled + active (user-approved; boot-persistent on port 8005, `Restart=on-failure`). Recall verified through the service.

**Phase 4 (same day) — the distiller:**

- **Service** `backend/app/services/distiller.py` + `POST /distill/run` (`backend/app/routers/distill.py`): finds episode logs with `distill_ready` Stop markers, condenses tool events (errors first-class) + the user's typed messages from the transcript + the ALREADY-KNOWN injected-lesson list, sends to **Opus via `llm_chat`** (all CLI gotchas inherited), validates candidates (schema, scope, instruction-shaped regex → dropped and counted, injected-label overlap → usage not confirmation, exact-label dupes), and persists survivors via the shared `lesson_store.save_lesson` — the identical write-gate path as `/remember` (refactored out of the router for this).
- **Quiescence guard** (bug found live): the Stop hook fires at every *turn* end, so a live session's log carries `distill_ready` while still growing — and the `.distilled` marker would suppress the rest of the session forever. `find_ready_logs` therefore requires no writes for `min_quiet_minutes` (default 30). Open refinement for later: marker could record line-count so long-lived sessions re-distill their growth.
- **Timer** `corvus-mind-distill.timer` (30-min cadence, autopilot-curl pattern) enabled + active. Cost bound: ≤3 sessions/run, ≤5 candidates/session, prompt capped at 24k chars.
- **Verified end-to-end on real data**: this session's own episode log (108 events, 5 user messages) → 5 candidates, all saved at informational authority, $0.058. Quality: situated + evidence-linked; one near-dup of a hand-saved lesson under a different label survived exact-label dedupe — exactly the phase-5 consolidation janitor's job, and the rows stay as its first test corpus (graph-as-testbed rule).
- Second `.format()`-with-JSON-braces bug class hit (`KeyError: '"label"'`): prompts containing literal JSON schemas must use `.replace`, not `.format`.

**Phase 5 (same day) — the janitors** (`backend/app/services/mind_janitors.py` + `POST /janitor/run` + `corvus-mind-janitor.timer` @ 6h):

- **Edge gating shipped first** (the §8.1 finding-6 fix): `supersedes`/`scoped-by`/`evidence-link` are code-3 memory-semantics edges in `_ETYPE_CODE`, gated to zero propagation in both the scalar (`_compute_edge_activation`) and vectorized (`_spread_edge_gates`) spread paths. Provenance and temporal links never conduct activation.
- **Consolidation**: embedding pass finds candidate pairs; ≥0.88 same-scope auto-fuses; the 0.75–0.88 borderline band gets a **batched Haiku verdict** (duplicate / complementary / contradictory / unrelated) because real data proved similarity alone can't order the band — a complementary pair measured 0.832 while a true duplicate measured 0.807. Fusion = canonical keeps content and gains utility (+0.05/confirmation, cap 0.95), absorbed member is deactivated with `superseded_by` + a promoted `evidence-link` edge (accumulate, don't discard). **The §8.3 discount rule is live**: a confirmation is only counted if the absorbed lesson's source session did NOT have the canonical injected (checked against Injection events in the episode log).
- **Staleness**: reuses the existing conflict-monitor scan, restricted via a new generic `node_type:` scope filter in `load_neuron_embeddings` (scanning empty-content scaffold produced only ambiguous verdicts). Lesson-vs-lesson contradictions resolve: same-scope → supersede-with-history (older demoted ×0.5, kept active, `superseded_by` + `supersedes` edge); cross-scope → **SCOPING verdict** (both stand as contextual truths, finding resolved without mutation). Non-lesson findings are left for the human escalation queue.
- **Decay audit**: demotes warm zombies (≥20 invocations, no `SynapticLearningEvent` reinforcement) by ×0.9 per run, floor 0.4.
- All graph mutations go through the Action Bus (`edge.link` with promotion-by-construction — memory edges are semantic assertions, not co-fire statistics, so they must land in `neuron_edges`, not the reapable weak tier). **Every janitor action is logged as an episode** (`janitor-actions.jsonl`) per the §3.4 compounding trick.
- **Verified live**: planted a genuine paraphrase duplicate → Haiku verdict "duplicate" → fused (utility 0.5→0.55, evidence-link row, absorbed lesson vanished from recall while canonical's score rose); both complementary pairs correctly kept and reported.

**Explorer fix (same day):** lessons were invisible in the Explorer's initial view — `/neurons/tree` `max_depth` cuts by `layer`, and saves carried a hardcoded `layer=3` at tree depth 2. Layer now derives from the anchor (`anchor.layer + 1`); existing rows corrected. Layer must always equal tree depth.

**Bootstrap backfill (same day, reordered before the compiler):** `harness/claude-code/backfill_transcripts.py` — transcripts → synthetic episode logs (live hook's redaction/schema reused by import; structural `is_error` failure signals; >150-event sessions split into parts; `bf-{rank}` filenames make the existing distill timer drain highest-signal-first, score = 2·errors + 3·corrections + recency). First run: 42 logs from 4,112 historical tool calls; tool distribution + ranking in `~/.corvus-mind/backfill-report.json`. Top log verified: 5 real lessons @ $0.057. The 29 flat memory files were deliberately NOT imported (curated + actively in use — dual-source risk, separate decision).

**Phase 6 (same day) — the compiler** (`backend/app/services/skill_compiler.py` + `POST /compile/run` + `corvus-mind-compile.timer` @ 24h):

- **One substrate, two projections** (settled in the phase-6 discussion): injection is *push* — small, per-prompt, query-matched declarative facts; skills are *pull* — a one-line trigger in the harness listing, full body loaded on task match at ~zero standing cost, working even with the backend down. The compiler's job is deciding which lesson clusters have crossed from fact to procedure.
- **Eligibility**: same-scope embedding clusters (union-find @ ≥0.55) of ≥3 active, unsuperseded lessons. Composer = Opus (quality-first-backend), declarative-playbook prompt, JSON out, evidence citations kept inline. Output namespaced `mind-*` in `~/.claude/skills/` (dir created — didn't exist) with provenance frontmatter comment (source neuron ids; "do not hand-edit; the graph is the source of truth").
- **Reverse check**: manifest (`~/.corvus-mind/compiled-skills.json`) records skill → source ids; a skill is retracted + recompiled when any source rots (inactive / superseded / utility < 0.4) or its cluster grows. The compiler only ever touches manifest-owned `mind-*` dirs. Compiler actions are logged as episodes (emit / retract / compose_failed).

### 8.6 Transferable Capability Capsule (2026-07-14)

Corvus-Mind now has an explicit harness-neutral transport contract rather than
relying on several clients reaching the same backend:

- `services/capability_capsule.py` emits schema `corvus.capability-capsule`
  version `1.0.0`: identity, governance, active unsuperseded memories, compiled
  skills, semantic tool requirements, lifecycle requirements, harness profiles,
  and SHA-256 integrity (optional HMAC authenticity via
  `CORVUS_CAPSULE_SIGNING_KEY`). Stable memory identity is separate from the
  content-version hash so gate-added evidence produces a version conflict, not
  a duplicate identity.
- Portability scopes are explicit and conservative: universal, user,
  organization, project, machine, harness. Machine/harness memories travel for
  inspection but are withheld from automatic rebinding into a different body.
- `GET /capabilities/capsule`, `POST /capabilities/verify`,
  `POST /capabilities/reconcile`, and `POST /capabilities/health` provide export,
  integrity checking, preview/apply reconciliation, and dimension-level health.
  Apply routes only novel portable memories through `lesson_store.save_lesson`
  at informational authority; there is no raw restore or identity-promotion
  bypass.
- Skills compile canonical-first into
  `~/.corvus-mind/capabilities/skills/`, then project into the declared Claude
  Code, Codex, and OpenCode skill directories. The injection hook reads the
  canonical self-model/charter with a one-cycle Claude-directory fallback.
- `harness/parity_probe.py` measures each declared body; OpenCode is honestly
  degraded when it lacks advisory pre-tool injection. `verify_transfer_live.py`
  plants a transport lesson and proves duplicate preview, tamper rejection,
  evidence-gated import, idempotent replay, and stable re-export identity.

**Live evidence (2026-07-14):** real capsule contained 257 active,
unsuperseded memories after the scope/supersession filter. Claude Code and Codex
scored 100%; OpenCode scored 97.5% (lifecycle coverage 0.8: no pre-tool advisory
surface). Five existing skills were rendered byte-for-byte to canonical + all
three targets. The planted lesson initially exposed a project-binding identity
bug, then an evidence-append identity bug; both were fixed without deleting the
planted graph row. Final replay: 257/257 already present, 0 new, 0 conflicts,
tampered capsule HTTP 422, apply no-op, planted stable id
`mem_b3b8c333339d412a90a7d563` present on re-export. Capability tests: 14 passed;
focused capability/reference/recall/write-gate suite: 57 passed before the final
two identity tests were added (the capability file alone then passed 14/14).
- **Verified live**: the 5-lesson Corvus dev-servers cluster compiled to `mind-corvus-dev-servers` ($0.02) — correct port map (8002/8003/8004-vite/8005/5175), working startup commands, gotchas, all evidence-cited. Second run emitted nothing (idempotent). Bounded: ≤2 Opus calls/run.

All six design layers are now live and unattended: capture → distill (30min) → gate → graph → janitors (6h) → compile (24h) → recall/injection/skills.

### 8.5 Codex CLI harness (2026-07-14)

Codex CLI 0.144.4 is the third Corvus-Mind harness, alongside Claude Code and
opencode. The implementation lives in `harness/codex/` and follows the
opencode precedent: a thin harness adapter shells out to the existing
`harness/claude-code/memory_inject_hook.py` and `episode_hook.py`. Recall,
redaction, exclusions, attribution, capsule delivery, and episode semantics
remain single-source; Codex is a harness, never a Corvus model provider.

- **Recall:** the existing `mind_mcp_server.py` is registered globally as the
  `corvus-mind` stdio MCP server, launched with the backend venv Python. No API
  key path was introduced; Codex retains ChatGPT-token subscription auth.
- **Injection:** Codex has native `SessionStart`, `UserPromptSubmit`, and
  `PreToolUse` command hooks. `additionalContext` becomes developer context,
  so the compiled charter/self-model and query-matched lessons arrive
  ambiently. `~/.codex/AGENTS.md` is not used: it would be only a static
  fallback if hooks were disabled or untrusted. Changed hooks must be reviewed
  once with `/hooks`; vetted automation may use the explicit hook-trust bypass.
- **Capture:** native `PostToolUse` and `Stop` hooks feed the shared episode
  hook. `Stop` supplies Codex's rollout JSONL as `transcript_path`, so no SQLite
  polling or duplicate transcript writer is needed. Codex documents that
  transcript format as unstable; the distiller must treat it as best-effort.

Observed acceptance evidence: `/health` returned HTTP 200 with port 8005;
a real Codex MCP call returned neuron 28 (`Corvus-mind backend runs on port
8005`); a separately unrequested charter policy appeared in the model's
answer; and session `019f6279-f5aa-7ae3-b0cc-af580df5c0a3` appeared at
`/metrics/mind/sessions` with 5 events, 4 injections, and a distill-ready Stop
record pointing to the Codex rollout.

**Parity gaps versus Claude Code:** Codex's rollout schema is not stable, and
locked-down noninteractive `read-only` runs cancel the stdio server's localhost
HTTP hop unless network/sandbox permission is granted (the scoped
`danger-full-access` acceptance run completed in 124.7 ms). Interactive use can
approve the local MCP operation. Otherwise recall, ambient injection,
PreToolUse warnings, capture, attribution, and distiller intake have native
event parity.
