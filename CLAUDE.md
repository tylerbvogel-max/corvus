# Corvus

Unified, multi-tenant neuron graph for prompt preparation. Two-stage Haiku pipeline: classify intent → score neurons → assemble context → execute with enriched prompt.

## Active roadmap
**AIP governance roadmap** — canonical source is the Master Corvus roadmap. Roadmap state (nodes + statuses + per-node context prompts): `~/Projects/master-corvus/public/roadmap-state.json`. Visual view: Master Corvus → Roadmap Flowchart at http://localhost:5175/. When the user asks to "work the next AIP roadmap item," look at nodes with `status` in `("proposed", "planned")` in the `governance` section (ids prefixed `gov-aip-`) and pick the next one whose dependencies (see `edges` array) are cleared. Each node's `prompt` field carries the kickoff context for that item, including migrated historical rationale from prior session work.

## Multi-Tenant Architecture
- **TENANT_ID** env var selects the tenant: `corvus-aero` (aerospace) or `corvus-flow` (plumbing)
- Domain config lives in `backend/tenants/{tenant_id}/` (tenant.yaml + Python modules)
- `backend/app/tenant.py` is the singleton loader — imported as `from app.tenant import tenant`
- All domain-specific content (prompts, patterns, seed data, concepts, regulatory trees) is in tenant dirs
- Service code is domain-agnostic — reads from `tenant.*` properties

## LLM Provider Policy
**All LLM calls route through the Claude CLI** (personal subscription — no API credits). Never use the `anthropic` Python SDK directly. The `_anthropic_chat` function in `backend/app/services/llm_provider.py` shells out to `claude -p --output-format json` via subprocess.

### Gotcha: "Claude Code cannot be launched inside another Claude Code session"
When Corvus is developed or run from inside a Claude Code session, the CLI subprocess inherits `CLAUDECODE=1` (and `CLAUDE_CODE_*` vars), which the CLI treats as a nested-session signal and refuses to launch.

**Fix:** Strip `CLAUDECODE*` and `CLAUDE_CODE_*` from the child process env before spawning the CLI. `llm_provider._anthropic_chat` already does this — apply the same pattern to any new subprocess-based LLM integration.

```python
child_env = {k: v for k, v in os.environ.items()
             if not k.startswith("CLAUDECODE") and not k.startswith("CLAUDE_CODE_")}
```

### Gotcha 2: the CLI subprocess must be isolated from the repo's agentic context
A `claude -p` launched with cwd inside this repo loads the project's `.mcp.json`
and `CLAUDE.md`, decides it is an agent with Corvus MCP tools, and answers
"I need permission to access the neuron graph" instead of completing the task
(this silently broke the production classifier for months — every call burned
tokens and fell back to empty classification). Every CLI call must use:
`cwd="/tmp"`, `--strict-mcp-config` (with no `--mcp-config` = zero MCP servers),
`--no-session-persistence`, and `--system-prompt` (full replace, never
`--append-system-prompt`). `llm_provider._anthropic_chat` does all four.

### Gotcha 3: pass the user prompt via STDIN, never argv
Prompts that begin with `-` (e.g. `--- Pair 0 ---` in judging formats) are
parsed as CLI options and crash the parser; argv also has size limits.
`proc.communicate(input=user_message.encode())` with bare `-p`.

## Stack
- Python FastAPI + PostgreSQL (async SQLAlchemy + asyncpg) + Anthropic Python SDK
- Alembic for schema migrations
- Port: from `PORT` env var (default 8002)

## Dev Commands
```bash
cd ~/Projects/corvus/backend
source venv/bin/activate
TENANT_ID=corvus-aero PORT=8002 uvicorn app.main:app --port 8002 --reload
# Or for plumbing tenant:
TENANT_ID=corvus-flow PORT=8003 uvicorn app.main:app --port 8003 --reload
```

## Docker
```bash
TENANT_ID=corvus-aero docker compose up --build
```

## Key Concepts (post generic-org overhaul, 2026-07)
- **Neurons**: flat substrate nodes. The engine's semantic axis is
  `abstraction_type` (structural | concept | principle | process | procedure |
  artifact); numeric `layer` is projection-depth metadata only (navigation/viz).
- **Region**: the silo tag (physical column is still `department`;
  `Neuron.region` is a synonym). Silos = labeled regions on ONE shared graph,
  never separate graphs. Per-region knobs live in `RegionPolicy` (scoring
  weights, loop config, ACL, write-gate overrides) — see `/admin/regions`.
- **Firing**: when a neuron is selected for a query context.
- **Scoring signals**: Burst, Impact, Precision, Novelty, Recency + Relevance
  (stimulus, gated) + a cold-start prior (authority + freshness + centrality,
  Bayesian shrinkage on invocations). Weights per-region overridable.
- **Edges**: stellate = intra-region, pyramidal = cross-region (derived from
  region membership at write time), instantiates = concept links.
- **Recall modes**: cheap (embed-only + neighbor vote, ~$0, ~300-600ms) | full
  (LLM classify) | adaptive (cheap with LLM escalation on low similarity).
  **cheap is the default** for both HTTP (`settings.recall_mode`) and MCP
  `query_graph` — the per-query Haiku classify was removed from the hot path
  (executive decision, 2026-07): ~18s of variable extended-thinking, occasional
  empty output, and the nested-session failure mode, for a task the free
  neighbor-vote handles as well or better. full/adaptive stay selectable.
- **Write gate**: tiered policy (tenant.yaml `write_gate:` + per-region
  overrides). Observational writes auto-commit through the same Action Bus
  proposal.apply tree as human approvals; authoritative writes queue.
  Consolidation decay (rides the autopilot /tick heartbeat) reclaims
  unreinforced auto-commits.
- **Reconciler**: the horizontal cross-region loop (integrity framework scans:
  contradictions, staleness divergence, homonym/synonym, seam gaps), findings
  routed to owning regions. `/admin/integrity/reconciler/sweep` or
  `reconciler_interval_hours` > 0.
- **Seeding a blank org**: ingest corpus → `/admin/seeding/knn-edges` +
  `cooccurrence-edges` → `discover-regions` (Leiden + LLM labels → human
  approval) → `retype-edges`. No hand-authored taxonomy required.
- **Propagation**: child firing propagates up at 0.6× per parent link.
- **Citation hopping**: anti-hallucination exit layer
  (`settings.citation_hopping_enabled`, default ON). Each neuron AND resolved
  regulation (engram) in the prompt gets a random per-query key `[FQ-XXXXXX]`
  (secret to the LLM; for regulations the real CFR ref is kept beside it); the
  exit layer (`services/citation_hopping.py` + `executor._apply_citation_hop_exit`)
  catches cited keys absent from the secret map = fabricated references. The
  frontend resolves keys to numbered superscripts via the response
  `citation_map`. MCP external agents verify via `verify_citations(hop_session_id,
  answer)`.
- **Overhaul log**: OVERHAUL-STATUS.md (design decisions + per-workstream
  verification evidence).

## Corvus Integration
Corvus (screen-watcher) is integrated as a subpackage under `backend/app/corvus/` with endpoints at `/corvus/`. Chrome extension captures → OCR → classify → interpret → queue observations for neuron graph ingestion.

## NASA Software Engineering Compliance Policy

All code contributions to this project MUST adhere to the following NASA software engineering standards. These requirements apply to code reviews, development practices, and Corvus-driven development suggestions.

### Governing Standards
- **NPR 7150.2D** — NASA Software Engineering Requirements (primary procedural authority)
- **NASA-STD-8739.8B** — Software Assurance and Software Safety Standard
- **NASA-STD-8739.9** — Software Formal Inspections Standard
- **NASA SWEHB** — Software Engineering Handbook (guidance/best practices)
- **JPL Power of Ten** — Holzmann's 10 rules for safety-critical code (JPL/NASA flight software)

### Code Review Requirements (per NPR 7150.2D & NASA-STD-8739.8/9)
1. **Software safety**: All changes must consider failure modes. Code that controls neuron scoring, observation evaluation, or LLM-driven actions must document assumptions and failure behavior.
2. **Configuration management**: Every change must be traceable (git commit with descriptive message). No uncommitted changes in production.
3. **Secure coding**: Follow NASA Secure Coding Portal practices — validate all external inputs, sanitize LLM outputs before database writes, no hardcoded credentials, no SQL injection vectors.
4. **Formal inspections**: Non-trivial changes (new endpoints, schema changes, LLM prompt modifications) require structured review against acceptance criteria before merge.
5. **Testing & verification**: New features require verification evidence. API endpoints need at minimum a smoke test. LLM pipeline changes need evaluation against known-good queries.
6. **Software classification**: This system processes operational knowledge and influences decision-making. Treat it as safety-relevant software — changes to scoring algorithms, neuron creation, or observation approval logic require heightened review.
7. **Metrics & measurement**: Track token usage, model costs, and pipeline latency. Cost projections and actuals must remain visible in the UI.
8. **Third-party software management**: LLM model updates (Haiku/Sonnet/Opus version changes), dependency upgrades, and Anthropic SDK updates must be evaluated for behavioral impact before adoption.
9. **Documentation**: Public-facing endpoints must have docstrings. Schema changes must include migration logic. LLM system prompts must document their intent and expected output format.
10. **Safety-critical coding (Power of Ten)**: Simple control flow (no recursion/goto), bounded loops, no dynamic allocation after init, functions under 60 lines, 2+ assertions per function, smallest variable scope, mandatory static analysis, restricted pointer use, zero compiler warnings, development rigor matched to criticality.

### Automated Enforcement (two tiers)

The NASA linter (`scripts/nasa_lint.py`) runs automatically via two mechanisms:
- **Claude Code hook**: runs after every Edit/Write on `backend/app/**/*.py`, giving immediate feedback
- **Pre-commit hook**: runs on staged files, blocks commit on strict violations

**Strict tier (blocks commit):**
- JPL-1: No recursion — functions must not call themselves
- JPL-2: Bounded loops — every `while` must have a comparison/bool test, a container drain pattern (`while stack:`), or a `break`
- JPL-6: No mutable globals — module-level `UPPER_CASE` dicts must use `MappingProxyType`, lists must be tuples
- NPR-3: No bare except — every `except` must name a specific exception type
- JPL-4 hard limit: Functions must not exceed 100 lines

**Guideline tier (warns, does not block):**
- JPL-4: Functions should be under 60 lines. Refactor into helpers if exceeded.

When the hook reports a strict violation after an edit, fix it immediately before continuing.
When the hook reports a guideline warning, fix it if the function was just created or substantially modified. Leave existing violations for dedicated cleanup passes.

### Corvus Development-Specific Rules
- Screen capture data is ephemeral — never persist raw screenshots beyond the processing pipeline
- Observation-to-neuron flow must maintain provenance (source_origin="corvus", refinement records)
- LLM evaluation proposals are advisory only — human approval required before graph modifications
- Interpretation cadence and alert thresholds must be configurable, not hardcoded
