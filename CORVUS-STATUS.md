# Corvus — Project Status & Session Handoff

Last updated: 2026-04-13

## What Is Corvus

A biomimetic, multi-tenant neuron graph for prompt preparation. Instead of traditional RAG, Corvus organizes domain knowledge as a 6-layer hierarchy (Department → Role → Task → System → Decision → Output) and runs queries through an 8-stage pipeline:

1. **Structural resolve** — zero-cost regex + keyword path for short, structurally-resolvable queries
2. **Parallel classify + embed** — Haiku classifies query → intent / departments / roles / keywords, in parallel with embedding
3. **Semantic prefilter** — cosine-similarity cut over candidate pool (uncapped since 2026-03-21)
4. **Score** — 5 gated signals (Burst, Impact, Precision, Novelty, Recency) + modulatory gating
5. **Spread activation** — typed edges (stellate / pyramidal / etc.) over promoted-edge table; weak edges live in JSONB
6. **Inhibitory regulation** — three-pass GABAergic / chandelier / neuromodulatory dampening
7. **Prompt assembly** — top-K neurons packed into a 4000-token system prompt
8. **Execute** — Haiku (or Sonnet/Opus via multi-slot blind A/B) answers with enriched context. Also served via MCP for external agents, and via `/context` for Fluent (no LLM execution).

Domain: defense aerospace (X-plane prototype development, modeled as `corvus-aero`). Additional tenants: `corvus-flow` (plumbing), `corvus-roost` (real estate), `corvus-hedge` (investment), `corvus-apex` (personal workstation).

## Canonical Docs

Deep docs and forward-plan live in Master Corvus, not here. This file exists only to capture Corvus-local session handoff state. For anything beyond "what's currently in flight":

- **Unified plan (architecture / governance / measurement / forward)** — `~/Projects/master-corvus/` → sidebar "★ Corvus Plan". Source files under `src/components/plan/sections/`.
- **Roadmap flowchart (spatial view, per-node Claude kickoff prompts)** — same app → "◇ Roadmap Flowchart". State: `~/Projects/master-corvus/public/roadmap-state.json`.
- **Active AIP session handoff** — `~/Projects/corvus/ROADMAP-WORKLOG.md` (per `CLAUDE.md`: when asked to "work the next AIP roadmap item," start there).
- **AIP plan of record** — `~/.claude/plans/staged-booping-globe.md`.

## Current Stats (approximate, 2026-04-13)

- **~2,800 neurons** across 9 departments, ~51 roles. Target: 3,500+ (active "role bolstering" work).
- **~463K edges** total: ~154K promoted in `neuron_edges` (weight ≥ 0.10, ≥ 2 co-fires), ~309K weak in JSONB.
- **51 neurons at layer = -1** — known bug, slated for fix (Forward Plan item #7).
- **~99%** of dev neurons lack provenance metadata — forward-enforcement planned at ingestion path (Forward Plan item #3).

Exact counts drift daily. Run `psql corvus_aero -c "SELECT layer, COUNT(*) FROM neurons GROUP BY layer ORDER BY layer;"` for current state.

## Stack

- **Backend:** Python FastAPI + PostgreSQL (async SQLAlchemy + asyncpg) + Alembic migrations
- **LLM:** Claude CLI via subprocess (personal subscription — zero API credits). Multi-provider abstraction supports Anthropic / Google Gemini Flash / Groq Llama / Azure OpenAI. See `backend/app/services/llm_provider.py`. **Never use the Anthropic SDK directly** (per `CLAUDE.md`).
- **Frontend:** React + Vite + TypeScript + D3.js / Sigma.js for visualizations
- **Port:** `PORT` env var, default 8002

## Dev Commands

```bash
cd ~/Projects/corvus/backend
source venv/bin/activate
TENANT_ID=corvus-aero PORT=8002 uvicorn app.main:app --port 8002 --reload
# Other tenants:
TENANT_ID=corvus-flow  PORT=8003 uvicorn app.main:app --port 8003 --reload
TENANT_ID=corvus-roost PORT=8004 uvicorn app.main:app --port 8004 --reload
TENANT_ID=corvus-hedge PORT=8005 uvicorn app.main:app --port 8005 --reload
TENANT_ID=corvus-apex  PORT=8006 uvicorn app.main:app --port 8006 --reload
```

Docker: `TENANT_ID=corvus-aero docker compose up --build`. Tests: `cd backend && TENANT_ID=corvus-aero pytest tests/ -v`.

## Directory Layout (pointers, not snapshots)

Code structure drifts; instead of duplicating trees here, read the live layout:

- `backend/app/` — FastAPI app (routers, services, models, pipeline stages). See `CLAUDE.md` for LLM-provider conventions and NASA linter policy.
- `backend/tenants/{tenant_id}/` — all domain-specific content (prompts, patterns, seed data, concepts, regulatory trees). Service code stays domain-agnostic.
- `backend/alembic/versions/` — migration history.
- `frontend/src/components/` — UI components.
- `.mcp.json` — MCP server config; 7 tools exposed.

## Recent Milestones (last ~5 weeks)

Mirrors Master Corvus Plan §1.7 — see that for the canonical list. Highlights:

- **2026-03-16** — Unified `corvus` repo cutover (multi-tenant aero + flow).
- **2026-03-20** — Eval-corpus suite (10 investor demo queries).
- **2026-03-21** — Scale-ready pipeline (adjacency cache, vectorized scoring, uncapped prefilter).
- **2026-03-27** — Claude CLI switch for all LLM calls; per-tenant DB isolation; `corvus-roost` added.
- **2026-03-28 → 29** — Engram system (retrieval indices for live regulatory API integration).
- **2026-03-30** — `corvus-hedge` + `corvus-apex` tenants; access-gate authentication.
- **2026-03-31 → 2026-04-02** — Agent orchestration built and fully removed (~1,564 LOC). Decision rationale: the neuron graph IS the coordination layer; expose via MCP to genuine external agents instead. See Master Corvus Governance §2.1.
- **2026-04-01** — Multi-slot query execution with live pipeline visualization.
- **2026-04-02** — Scored gap detection + mandatory proposal-approval workflow (proposal queue reaches current form).
- **2026-04-03** — Document ingestion pipeline; advisor mode; graph-integrity system; source-pill filter on proposals.
- **2026-04-06** — Tiered edge storage (hot/cold split, 107 MB → 27 MB on the hot table) + GovCloud deployment infrastructure.
- **2026-04-10** — **AIP Pattern #1** landed: Action Bus (universal write primitive for governed model types).
- **2026-04-11** — **AIP Pattern #2** landed: bidirectional lineage, enriched firings, `NeuronScoreOverride`.
- **2026-04-12** — LLM routing hardened through Claude CLI (last SDK leakage removed); origin-based proposal filter.

## Current Focus

- **AIP roadmap execution** — Patterns #1 and #2 landed; next pattern is queued in `ROADMAP-WORKLOG.md`. Start sessions there.
- **Role bolstering** — ongoing neuron expansion toward 3,500+.
- **AS9146 FOD-prevention ingestion test** — validating document ingestion end-to-end through the proposal queue.
- **Forward Plan backend items** — see Master Corvus Forward Plan §4.1 for the prioritized list (1: done, 2–9 in various states).

## Out-of-Date Information Moved

The old (2026-03-08) snapshot contents — SQLite scaling phases, pre-unification neuron counts, pre-proposal-queue roadmap, per-session feature lists — have been removed. That history is preserved in git; if you need it, check `git log -p CORVUS-STATUS.md` before this refresh.
