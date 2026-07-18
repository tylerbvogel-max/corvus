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

wait_for_cli() {
  # bounded: 96 probes x 10 min = 16h max (JPL-2)
  for _ in $(seq 1 96); do
    if out=$(claude -p "reply with exactly: ok" --output-format text \
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
      | grep -E "\[ingest\] done|\[db\]|\[memory\]|\[nospread\]|\[baseline\]|giving up|Traceback|Error"
  done
done
echo SWEEP-DONE
