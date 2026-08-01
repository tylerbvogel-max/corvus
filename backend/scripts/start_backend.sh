#!/usr/bin/env bash
set -euo pipefail

backend_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$backend_dir"

if [[ -x "$backend_dir/venv/bin/alembic" ]]; then
    alembic_bin="$backend_dir/venv/bin/alembic"
    uvicorn_bin="$backend_dir/venv/bin/uvicorn"
else
    alembic_bin="$(command -v alembic)"
    uvicorn_bin="$(command -v uvicorn)"
fi

bind_host="${CORVUS_BIND_HOST:-127.0.0.1}"
bind_port="${PORT:-8005}"

echo "Applying reviewed Alembic migrations before Corvus startup"
"$alembic_bin" upgrade head

echo "Starting Corvus on ${bind_host}:${bind_port}; application schema guard is enabled"
exec "$uvicorn_bin" app.main:app --host "$bind_host" --port "$bind_port"
