# Corvus-Mind: Agentic Institutional Memory Framework

> **Trust-gated memory for AI agents.** A multi-tenant neuron graph that captures, consolidates, and recalls situated agentic experience — with evidence at every layer.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.141+-009688.svg)](https://fastapi.tiangolo.com/)

---

## What Is This?

**Corvus-Mind** is the reference implementation of a **trust-gated memory methodology** for LLM agents. It solves the core problem of agentic memory: *how does an agent remember what's true without accumulating confident falsehoods?*

The methodology has three pillars:

| Pillar | Mechanism | Why It Matters |
|--------|-----------|----------------|
| **Evidence-Gated Admission** | Every candidate memory enters at *informational* authority with provenance links; promotion to *guidance* or *organizational* requires a verifiable outcome (test passed, exit 0 after documented failure, user confirmation) | Bad memory is worse than no memory — a confidently wrong lesson repeats forever |
| **Supersession with History** | When the world changes, old memories are demoted with a `was-true-until` record, not deleted — "this used to be different" is itself useful context | Agentic facts rot: tools update, repos refactor, services move ports |
| **Context Scoping** | Contextual truths (true in repo A, false in repo B) are conditioned on scope rather than merged, deleted, or promoted to global | Many lessons are true only within a boundary: a migration workaround for one project's alembic chain, a port convention for one machine |

The framework is **harness-agnostic** — it works as an invisible add-on for any coding agent (Claude Code, OpenCode, Cursor, custom harnesses) via MCP, HTTP, or direct library use.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        AGENT / HARNESS                          │
└──────────────────────────────┬──────────────────────────────────┘
                               │ MCP / HTTP / Library
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                     CORVUS-MIND BACKEND                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  INGEST      │  │  RECALL      │  │  CONSOLIDATION       │  │
│  │  • Episode   │  │  • Embed-only│  │  • Decay reclamation │  │
│  │    capture   │  │    neighbor  │  │  • Near-dup fusion   │  │
│  │  • Distiller │  │    vote (~150ms)│  │    (human-gated)   │  │
│  │    (async)   │  │  • Semantic  │  │  • Staleness checks  │  │
│  │  • Write gate│  │    prefilter │  │  • Cross-scope reconciler││
│  │    (tiered)  │  │  • Hybrid    │  │    (Opus judge)      │  │
│  └──────────────┘  │    RRF lanes │  └──────────────────────┘  │
│                    └──────────────┘                             │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │                    NEURON GRAPH (PostgreSQL)              │  │
│  │  Neurons: flat substrate, typed by abstraction_type      │  │
│  │  Edges: stellate (intra-scope), pyramidal (cross-scope)  │  │
│  │  Scoring: 6-signal (relevance, impact, burst, recency,   │  │
│  │            precision, novelty) + cold-start prior        │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### Key Technical Choices

| Decision | Rationale |
|----------|-----------|
| **Embed-only classification** (neighbor vote) | ~150ms, $0, no nested Claude CLI session failures — deleted the 18s per-query Haiku classify |
| **Citation hopping (frequency-hopped keys)** | Anti-hallucination exit layer: each neuron gets a per-query ephemeral key `[FQ-xxxxxx]`; fabricated citations caught deterministically |
| **Trust > velocity write gate** | Instruction-shaped candidates dropped; evidence-shaped auto-commit with decay reclamation; human countersign for promotion |
| **Adversarial self-testing** | Honeypot episodes injected to verify dedup/consolidation thresholds; incident reports as trophies |
| **Measure first, decide second** | Embedding similarity 0.832 complementary pair outranking 0.807 true duplicate forced Haiku-verdict dedup instead of threshold tuning |

---

## Installation

### Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.11+ | |
| PostgreSQL | 15+ | pgvector is not required by the current text-embedding schema |
| Node.js | 20+ | For frontend demo only |
| **Claude CLI** | Latest | **Required for LLM calls** — uses your personal Anthropic subscription (no API credits consumed). Install via `npm install -g @anthropic-ai/claude-code` then `claude auth login` |

> **Note on LLM providers**: The backend supports multiple providers (Anthropic via Claude CLI, Google Gemini, Groq, Azure OpenAI). At least one must be configured. The Claude CLI path is the default and recommended route because it uses your existing subscription.

---

### 1. Database Setup

```bash
# Create the database (run as postgres user)
sudo -u postgres createdb -O $USER corvus_mind
```

---

### 2. Backend Installation

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Run the corvus-mind tenant (port 8005). This applies reviewed migrations,
# then the application independently verifies that the schema is at head.
TENANT_ID=corvus-mind PORT=8005 bash scripts/start_backend.sh
```

> **One-command with Docker** (if you prefer):
> ```bash
> docker compose up --build
> ```
> *Requires a `docker-compose.yml` — see [Docker Deployment](#docker-deployment) below.*

---

### 3. Frontend Demo (Optional)

```bash
cd frontend
npm install
VITE_API_PORT=8005 npm run dev
# Open http://localhost:5173
```

---

### 4. Verify Installation

```bash
curl http://localhost:8005/health
# {"status":"ok","neuron_count":0,"total_queries":0}

curl http://localhost:8005/tenant
# {"tenant_id":"corvus-mind","display_name":"Corvus Mind","memory_surface":true,...}
```

---

## Harness Integration — The "Invisible Add-On"

Corvus-Mind is designed to be **harness-invisible** — the agent doesn't know it's there; it just works better.

### Option A: Claude Code (Recommended — Full Feature Set)

The most mature integration with ambient recall, episode capture, and MCP tools.

#### 1. Install the Hooks

Add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/Projects/corvus/harness/claude-code/memory_inject_hook.py"
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/Projects/corvus/harness/claude-code/memory_inject_hook.py"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/Projects/corvus/harness/claude-code/episode_hook.py"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/Projects/corvus/harness/claude-code/memory_inject_hook.py"
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/Projects/corvus/harness/claude-code/episode_hook.py"
          }
        ]
      }
    ]
  }
}
```

#### 2. Register the MCP Server

```bash
claude mcp add --scope user corvus-mind \
  ~/Projects/corvus/backend/venv/bin/python \
  ~/Projects/corvus/harness/claude-code/mind_mcp_server.py
```

**What you get:**
- **Ambient recall** on every session start and user prompt (injected as background context)
- **Pre-mistake warnings** on Bash commands that match high-confidence lessons
- **Episode capture** of all tool uses (redacted, scoped, never breaks your session)
- **MCP tools**: `recall(query)`, `remember(lesson, evidence, label, scope)`, `distill(session_id)`

#### 3. Configure Exclusions (Optional)

Create `~/.corvus-mind/config.json`:

```json
{
  "excluded_cwd_prefixes": [
    "~/personal",
    "~/private"
  ]
}
```

Paths matching these prefixes are never logged — work/personal wall by design.

---

### Option B: OpenCode

```bash
# Symlink the plugin into OpenCode's plugin directory
mkdir -p ~/.config/opencode/plugin
ln -s ~/Projects/corvus/harness/opencode/corvus-mind.js ~/.config/opencode/plugin/corvus-mind.js
```

**What you get:**
- Ambient recall on session start and every user prompt (injected as synthetic system reminders)
- Episode capture on tool executions
- Transcript logging for distiller compatibility
- Uses the same Python hooks as Claude Code — identical memory surface

---

### Option C: Cursor / Windsurf / Generic HTTP Client

Use the HTTP API directly from any harness:

```bash
# Recall relevant memories
curl -X POST http://localhost:8005/query/stream \
  -H "Content-Type: application/json" \
  -d '{"message": "How do I fix the port-already-in-use error?", "model": "haiku"}'

# Propose a memory (enters at informational authority)
curl -X POST http://localhost:8005/ingest/observation \
  -H "Content-Type: application/json" \
  -d '{"text": "Port 8005 held by stale uvicorn — kill with `fuser -k 8005/tcp`", "source": "user_correction"}'
```

---

### Option D: Direct Python Library

```python
from app.tenant import tenant
from app.services.recall_lanes import recall_neurons

# Recall for a query
results = await recall_neurons(db, "port conflict on 8005", tenant_id="corvus-mind")
```

---

## Docker Deployment

The composition lives in [`docker-compose.yml`](docker-compose.yml) — it is not
duplicated here, because the copy that used to sit in this section had drifted
from the real file (it still advertised a `~/.claude` mount into `/root`, which
the container's unprivileged runtime user could never read).

Two things worth knowing before you run it:

- **The image ships with no LLM provider.** `CLAUDE_CLI_PATH` is not baked in.
  The Claude-CLI posture is a *local dev* arrangement (see below); in a
  container, supply `ANTHROPIC_API_KEY` / `GOOGLE_API_KEY` / `GROQ_API_KEY`, or
  run with none and accept a graph with no LLM calls. That default is
  deliberate: zero server-side LLM spend.
- **Postgres publishes on host port 5433**, not 5432, so it cannot collide with
  a PostgreSQL already serving `corvus_mind` on the host.

```bash
# Build and run
docker compose up --build
```

> **Note**: The Docker build requires the Claude CLI binary. Either install it in the image or mount your host's `~/.claude` directory (as shown) to use your authenticated CLI.

---

## The Memory Admission Ladder

```
┌─────────────────────┐     Verifiable outcome      ┌─────────────────────┐
│   INFORMATIONAL     │ ──────────────────────────► │      GUIDANCE       │
│   (raw capture)     │  test pass, exit 0 after    │  (evidence-backed)  │
│                     │  documented failure,        │                     │
│  • Episode logs     │  user verified              │  • Distilled lessons│
│  • Session transcripts                        │  • Auto-committed     │
│  • Distiller candidates                       │    if guardrails pass │
└─────────────────────┘                           └──────────┬──────────┘
                                                               │ User confirmation
                                                               ▼
┌─────────────────────┐                           ┌─────────────────────┐
│  ORGANIZATIONAL     │ ◄──────────────────────── │    (promotion)      │
│  (user-confirmed)   │   Explicit user          │                     │
│                     │   correction/preference  │  • Standing orders  │
│  • User corrections │   outranks efficiency    │  • Confirmed prefs  │
└─────────────────────┘                           └─────────────────────┘
```

**Consolidation decay** (runs from the dedicated janitor schedule):
- Unreinforced informational → deactivated → reclaimed
- Near-duplicate lessons across independent sessions → fused (human-gated), weight accumulated, provenance preserved
- **Independence required**: an episode where the lesson was already in context is a *usage*, not a confirmation — prevents self-reinforcement trap

---

## Scopes (Regions)

Corvus-Mind uses **scopes** (the memory tenant's analog of departments/regions):

| Scope | Purpose | Example Memories |
|-------|---------|------------------|
| **Harness** | How the coding harness operates | "Claude CLI needs `CLAUDECODE=1` stripped", "MCP server registered via `claude mcp add`" |
| **Environment** | Machine/OS facts | "PostgreSQL on 5432", "nvm at ~/.config/nvm/versions/node/v20.20.0/bin/node" |
| **Projects** | Per-repo working knowledge | "This repo uses `alembic upgrade head` not `migrate`", "Flaky test in `test_spread.py`" |
| **User** | Corrections & preferences | "User prefers `rg` over `grep`", "Always run `npm run build:demo` before deploy" |
| **Provenance** | Where memories came from | "Lesson L-42 sourced from session `abc-123`, verified by exit 0" |

Scopes are **policy boundaries** — memories must not cross project or tenant walls the user has declared closed.

---

## Configuration

All settings via environment variables (see `backend/app/config.py`):

```bash
# Core
TENANT_ID=corvus-mind
PORT=8005
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/corvus_mind

# LLM Providers (optional — with none set, the graph runs without LLM calls)
# Anthropic via Claude CLI (personal sub — no API credits). Usually you can
# leave this unset: the app resolves `claude` from PATH, then falls back to the
# highest nvm-installed CLI. Set it explicitly only to pin a specific binary,
# or for systemd units, whose PATH does not carry nvm.
# CLAUDE_CLI_PATH=~/.local/bin/claude
# Or API keys for other providers
GOOGLE_API_KEY=...
GROQ_API_KEY=...

# Memory tuning (corvus-mind defaults)
WEIGHT_RELEVANCE=0.70
WEIGHT_IMPACT=0.08
WEIGHT_SPREAD_BOOST=0.20
MIND_DEDUP_REQUIRES_APPROVAL=true
```

---

## Evaluation

Corvus-Mind includes eval harnesses for memory quality:

```bash
# LoCoMo long-conversation memory benchmark
cd eval/locomo
./run_locomo.sh

# ATANT adversarial memory injection test
cd eval/atant
python run_atant.py
```

Key metrics tracked:
- **Recall precision@k** — fraction of retrieved neurons actually relevant
- **Dedup verdict accuracy** — Haiku judge agreement on near-dup pairs
- **Hallucination rate** — citation-hopping catch rate
- **Consolidation safety** — false merge rate (should be ~0 with human gate)

### LoCoMo full-suite result (2026-07-21)

**65.4 overall** on all 10 conversations / 1,986 questions (LLM-judge percent-correct,
judge fixed at Claude Sonnet), run end-to-end through the shipped production pipeline —
chunked distillation through the write gate, hybrid three-lane recall (no LLM in the hot
path), strict refuse-when-unsure answering, and the full maintenance lifecycle (janitors +
skill compilers) at production-equivalent cadence:

| | single-hop | multi-hop | temporal | open-domain | adversarial |
|---|---|---|---|---|---|
| memory (shipped config) | 65.8 | 40.8 | 65.7 | 38.5 | **85.7** |
| full-context baseline | 87.0 | 64.2 | 65.4 | 44.8 | 59.2 |

The headline trade: the memory system gives up ~6.6pp overall versus stuffing the whole
transcript into context, and buys **+26.5pp on adversarial trap questions** — on the 446
questions whose correct answer is "no information available," it refuses correctly 85.7%
of the time, and not one of its adversarial misses was a wrongful refusal. Provider
integrity was receipt-verified per call (16,652 calls, zero fallbacks, single model
version per workload); the dataset is SHA-256-pinned and loaded fail-closed.

> The LoCoMo dataset (CC BY-NC 4.0) is not distributed with this repo — fetch it from
> [snap-research/locomo](https://github.com/snap-research/locomo) and place it at
> `eval/locomo/locomo10.json`.

---

## Project Structure

```
corvus/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app and read-only schema guard
│   │   ├── config.py            # Settings (env-driven)
│   │   ├── tenant.py            # Tenant singleton loader
│   │   ├── models.py            # SQLAlchemy models
│   │   ├── database.py          # Async engine/session
│   │   ├── routers/             # API endpoints
│   │   ├── services/            # Core logic (recall, scoring, consolidation, etc.)
│   │   ├── seed/                # Tenant seed data loaders
│   │   ├── middleware/          # Audit, security headers, access gate
│   │   └── compliance/          # Regulatory frameworks (not used by mind)
│   ├── tenants/
│   │   └── corvus-mind/         # Agentic-memory domain config
│   │       ├── tenant.yaml      # Tenant metadata, write gate, output policies
│   │       ├── concepts.py      # Core methodology concepts (evidence-gate, supersession, etc.)
│   │       ├── patterns.py      # Reference detection (session IDs, file:line, exit codes)
│   │       ├── voices.py        # Intent→system prompt mappings
│   │       ├── classifier_prompt.py
│   │       ├── risk_categories.py
│   │       ├── provenance_seeds.py
│   │       └── regulatory_tree.py
│   ├── alembic/                 # Sole schema-mutation authority
│   ├── requirements.txt
│   └── tests/                   # Engine tests (domain-agnostic)
├── frontend/                    # React + Vite + TypeScript demo UI
│   ├── src/components/          # Dashboard, graph viz, chat, neuron explorer
│   └── src/demo/shim.ts         # Static demo mode (Render static site)
├── harness/
│   ├── claude-code/             # MCP server, hooks for Claude Code
│   │   ├── episode_hook.py      # PostToolUse + Stop capture
│   │   ├── memory_inject_hook.py# SessionStart + UserPromptSubmit ambient recall
│   │   └── mind_mcp_server.py   # MCP tools: recall, remember
│   └── opencode/                # OpenCode integration
├── eval/
│   ├── locomo/                  # Long-conversation memory benchmark
│   └── atant/                   # Adversarial injection test
├── docs/design/                 # Architecture decision records
├── LICENSE
└── README.md
```

---

## Contributing

1. Fork & branch
2. Make changes with **evidence-backed commits** (tests, eval deltas)
3. Run NASA linter: `python scripts/nasa_lint.py backend/app/your_change.py`
4. PR with: what changed, why, verification evidence

**Code style**: NASA-STD-8739.8 / JPL Power of Ten — simple control flow, bounded loops, no mutable globals, functions ≤ 60 lines, 2+ assertions per function.

---

## License

MIT License — see [LICENSE](LICENSE).

Copyright (c) 2024-2025 **Tyler B. Vogel**

---

## Citation

If you use Corvus-Mind's methodology in research or production:

```bibtex
@software{corvus-mind,
  author = {Tyler B. Vogel},
  title = {Corvus-Mind: Evidence-Gated Agentic Institutional Memory},
  year = {2024},
  url = {https://github.com/tylerbvogel/corvus}
}
```

---

## Acknowledgments

- **Biomimetic inspiration**: cortical microcircuits (stellate/pyramidal/inhibitory), synaptic consolidation, memory reconsolidation
- **Methodology forged in**: Aurora Flight Sciences AI-ops (replacing $120k+ AppSheet with deterministic agent-built apps)
- **Adversarial testing culture**: the Corvid temperament — honeypots against your own systems, incident reports as trophies

---

**Built for the agent that remembers truthfully.** 🐦
