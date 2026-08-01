#!/bin/bash
# LoCoMo sweep with a CLI-health gate: a hard usage-limit window must PAUSE
# the sweep, not corrupt it — during the 2026-07-12 outage every failed
# answer was judged wrong and banked (conv-1 baseline scored 57% with
# multi-hop at 9%). Gate probes `claude -p` before each phase and waits.
#
# Usage: sweep.sh <first_conv> <last_conv> [phases...]
# Resume rule: pass --no-reset phases for a conv whose ingest is banked.
# Set LOCOMO_LIFECYCLE_MODE=full-lifecycle to exercise the production-ratio
# maintenance schedule across the suite's global session ordinals.
set -u
cd "$(dirname "$0")/../../backend"
source venv/bin/activate
export PYTHONPATH=. TENANT_ID=corvus-locomo

# Probe with the SAME absolute path llm_provider calls (systemd units don't
# carry the nvm PATH — a bare `claude` probe failed for 16h on 2026-07-19
# while the CLI was healthy the whole time), and strip the nested-session
# markers exactly like the real call path does.
#
# Resolution order mirrors llm_provider._resolve_claude_cli exactly — env, then
# PATH, then the HIGHEST nvm-installed CLI. The old default hardcoded v20.20.0,
# which silently probed a different binary than the app called once the machine
# convention moved to 22.22.0.
resolve_claude_cli() {
  if [[ -n "${CLAUDE_CLI_PATH:-}" ]]; then
    printf '%s\n' "${CLAUDE_CLI_PATH/#\~/$HOME}"
    return
  fi
  if command -v claude >/dev/null 2>&1; then
    command -v claude
    return
  fi
  ls -1 "$HOME"/.config/nvm/versions/node/*/bin/claude 2>/dev/null \
    | sort -V | tail -n 1
}
CLAUDE_CLI="$(resolve_claude_cli)"
if [[ -z "$CLAUDE_CLI" ]]; then
  echo "no Claude CLI resolved (set CLAUDE_CLI_PATH)" >&2
  exit 2
fi

wait_for_cli() {
  # bounded: 96 probes x 10 min = 16h max (JPL-2)
  for _ in $(seq 1 96); do
    if out=$(env -u CLAUDECODE -u CLAUDE_CODE_ENTRYPOINT -u CLAUDE_CODE_SSE_PORT \
               "$CLAUDE_CLI" -p "reply with exactly: ok" --output-format text \
               --strict-mcp-config --no-session-persistence 2>&1 </dev/null) \
       && [ -n "$out" ]; then
      return 0
    fi
    echo "[gate] CLI unavailable ($(date +%H:%M)) — waiting 10 min"
    sleep 600
  done
  echo "[gate] CLI still down after 16h — aborting sweep"
  exit 1
}

first=$1; last=$2; shift 2
phases=${*:-all}

for c in $(seq "$first" "$last"); do
  for ph in $phases; do
    wait_for_cli
    echo "=== CONV $c phase=$ph ($(date +%F' '%H:%M)) ==="
    args=(--conv "$c" --phase "$ph")
    [ "$ph" != all ] && [ "$ph" != ingest ] && args+=(--no-reset)
    ../eval/locomo/run_locomo.sh "${args[@]}" 2>&1 \
      | grep -E "\[ingest\] done|\[db\]|\[memory|\[nospread|\[embed-only|\[baseline|PROVIDER|Drift|giving up|Traceback|Error"
  done
done
echo SWEEP-DONE
