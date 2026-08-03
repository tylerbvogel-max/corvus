# Runbook: database unavailable

## Symptom

`GET /ready` returns 503 with a `schema` check failure naming a driver
exception. `GET /health` keeps returning 200 — that is correct, not a bug: the
process is alive, so restarting it fixes nothing.

## Detection

- `GET /ready` → 503, `checks[].name == "schema"`, status `fail`
- `verify_deployment.py` → `[FAIL] readiness`
- Structured logs: any request carrying the failure has a `request_id`

## First response

1. Confirm the process is alive and only the dependency is gone: `/health` 200
   plus `/ready` 503 means do **not** restart the service.
2. `pg_isready`, then check the tenant's `DATABASE_URL`.
3. The `remediation` field on the failing check names the exact fix.
4. Once the database returns, `/ready` recovers on its own — no restart needed.

## Drill — EXECUTED 2026-08-02

Disposable database `corvus_test_readiness` created and migrated to head, a
backend booted against it on :8009, healthy baseline confirmed (200/200), then
`dropdb --force` **under the running process**.

Observed:

```
/health  200  {"status":"ok","check":"liveness","tenant":"corvus-mind"}
/ready   503  schema: database dependency failed:
              InvalidCatalogNameError: database "corvus_test_readiness" does not exist
              remediation: Verify the database exists and is reachable ...
              capabilities: PASS  (checks are not short-circuited)
```

The drill found a real defect on its first run: `/ready` answered **500
"Internal server error"**, because asyncpg's `InvalidCatalogNameError` is not a
`SQLAlchemyError` and escaped the catch. Fixed — a readiness probe reports,
never raises — and pinned by regression test.
