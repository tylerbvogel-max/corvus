#!/usr/bin/env bash
# LoCoMo runner. Exists so the benchmark exercises the SAME scoring config the
# memory tenant actually runs — deploy/memory-tenant.env is the single source of
# truth, shared with deploy/corvus-mind.service. Invoking run_locomo.py directly
# silently falls back to Corvus stock defaults (50/50 relevance-vs-usage, raw
# additive spread), i.e. it would benchmark a system nobody runs.
#
# Usage: ./run_locomo.sh --conv 0 --phase all [--max-questions N] \
#          [--lifecycle-mode raw|consolidation|full-lifecycle]
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

set -a
# shellcheck disable=SC1091
source "$REPO/deploy/memory-tenant.env"
set +a

export TENANT_ID=corvus-locomo          # throwaway tenant; harness hard-asserts this

# Production gates duplicate merges on human sign-off. A benchmark has no human
# to countersign, so leaving the gate on would let duplicate facts pile up and
# crowd the recall slots — measuring a graph state that never exists in
# production (where the merges DO get approved) and diverging from the prior
# run's conditions. Auto-fuse in the throwaway tenant models the approved
# steady state. Never set this on a real memory tenant.
export MIND_DEDUP_REQUIRES_APPROVAL=false
export PYTHONPATH="$REPO/backend"
: "${LOCOMO_CONCURRENCY:=2}"            # >2 concurrent CLI subprocesses OOM this box
export LOCOMO_CONCURRENCY

# The Claude CLI refuses to launch inside a Claude Code session; strip the
# markers so the subprocess starts clean (same fix as llm_provider).
unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT CLAUDE_CODE_SSE_PORT 2>/dev/null || true

# Provider integrity (gate 2): no codex fallback may serve a benchmark call.
# run_locomo.py also sets this before importing app code; exported here as
# defense in depth.
export CODEX_PATH=/nonexistent/locomo-certificate-fallback-disabled
export LLM_MODEL_ALIASES='{}'

cd "$REPO/backend"
exec "$REPO/backend/venv/bin/python" "$REPO/eval/locomo/run_locomo.py" "$@"
