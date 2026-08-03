# Runbook: backup restore

## Detection

You need this when data is lost or corrupted, or when you want to reproduce a
bug against real data without touching production. There is no alert for
"you should restore" — that judgement is always human.

## When

Data loss, corruption, or standing up a copy to reproduce a bug against real
data without touching production.

## Procedure — EXECUTED 2026-08-02, timings are measured

```
pg_dump -Fc corvus_mind -f corvus_mind.dump          # 7.5s, 39 MB
createdb -O yggdrasil corvus_test_restore            # -O matters: PG15+ denies
pg_restore -d corvus_test_restore corvus_mind.dump   #    CREATE on public to non-owners
psql -d corvus_test_restore -At -c "SELECT version_num FROM alembic_version"
```

Then prove the copy actually serves, rather than assuming a clean exit code
means a usable database:

```
DATABASE_URL=postgresql+asyncpg://yggdrasil:yggdrasil@localhost:5432/corvus_test_restore \
TENANT_ID=corvus-mind uvicorn app.main:app --port 8009

backend/venv/bin/python backend/scripts/verify_deployment.py --url http://127.0.0.1:8009 \
  --database-url postgresql://yggdrasil:yggdrasil@localhost:5432/corvus_test_restore \
  --expect-revision 027_synaptic_homeostasis
```

Observed on the drill: **6/6 checks passed** — 1,430 neurons restored, schema
at head, and recall returned 5 real hits in 621ms against the restored copy.

## Notes

- Restore is only verified when recall works. A restored database with an empty
  or unembedded graph passes every structural check and serves nothing.
- Set `CORVUS_JOB_RECEIPTS_DIR` to a scratch path, or the restored instance
  writes job receipts into the live system's health state.
- Drop the copy when done: `dropdb corvus_test_restore`.
