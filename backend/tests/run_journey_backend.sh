#!/usr/bin/env bash
# Boot a throwaway-Postgres fixture backend for the operator-journey browser
# specs (frontend/tests/journeys/, roadmap record durability-frontend-contracts
# verification #6).
#
# Same mutation-safety pattern as tests/replay_nvm_throwaway.py: the database
# named here is DROPPED (schema-level) and rebuilt via `alembic upgrade head`
# on every run, so journey specs can click real write actions — proposal
# approval, integrity resolution, roadmap reconciliation — without touching
# governance data in corvus_mind. The database itself must already exist
# (the yggdrasil role lacks CREATEDB):
#   sudo -u postgres psql -c 'CREATE DATABASE corvus_test_journeys OWNER yggdrasil'
#   sudo -u postgres psql -c 'CREATE DATABASE corvus_test_journeys_locomo OWNER yggdrasil'
#
# Usage: run_journey_backend.sh <tenant-id> <db-name> <port>
#   run_journey_backend.sh corvus-mind   corvus_test_journeys        8006
#   run_journey_backend.sh corvus-locomo corvus_test_journeys_locomo 8007
#
# Invoked by frontend/playwright.journeys.config.ts as a webServer command.
set -euo pipefail

TENANT="${1:?tenant id required}"
DB="${2:?db name required}"
PORT="${3:?port required}"

case "$DB" in
  corvus_test_*) ;;
  *) echo "refusing: '$DB' is not a corvus_test_* database" >&2; exit 1 ;;
esac

cd "$(dirname "$0")/.."
export DATABASE_URL="postgresql+asyncpg://yggdrasil:yggdrasil@localhost:5432/${DB}"
export TENANT_ID="$TENANT"
export PYTHONPATH=.
# The embedding model is already local; forbid HF Hub revalidation chatter.
export HF_HUB_OFFLINE=1

echo "== resetting throwaway database ${DB}"
psql -U yggdrasil -h localhost -d "$DB" -q \
  -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

echo "== migrating ${DB} to alembic head"
./venv/bin/alembic -q upgrade head

if [ "$TENANT" = "corvus-mind" ]; then
  echo "== seeding operator-journey fixtures"
  ./venv/bin/python tests/seed_journey_fixture.py
fi

echo "== serving ${TENANT} fixture backend on :${PORT}"
exec ./venv/bin/python -m uvicorn app.main:app --port "$PORT"
