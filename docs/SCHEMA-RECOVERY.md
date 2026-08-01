# Schema Authority, Backup, and Recovery

Corvus uses one schema authority: Alembic. Application lifespan code may seed
rows and warm caches, but it may not create or alter schema objects.

## Startup contract

Both Docker and the Corvus systemd unit invoke
`backend/scripts/start_backend.sh`. The script runs `alembic upgrade head`
before Uvicorn. The application then performs an independent, read-only guard:
it connects to PostgreSQL and requires the database's Alembic heads to equal
the heads packaged with the running build. An unmanaged, behind, ahead, or
unreachable database fails before any seed write.

For a manual backend start, use the same entry point:

```bash
cd backend
TENANT_ID=corvus-mind PORT=8005 bash scripts/start_backend.sh
```

Do not bypass the script in normal operation. Direct Uvicorn startup is useful
only for deliberately testing the application's fail-closed schema guard.

## Automated migration smoke tests

The test database is destroyed. Its name must begin with `corvus_test_` or
`corvus_migration_`; the test refuses every other name.

```bash
cd backend
TENANT_ID=corvus-mind PYTHONPATH=. \
CORVUS_TEST_DATABASE_URL="$DISPOSABLE_DATABASE_URL" \
venv/bin/python -m pytest -q -s tests/test_migration_smoke.py
```

That proves a blank database reaches head through `alembic upgrade head` alone
and that `alembic check` sees no ORM drift. To exercise a representative
pre-head database, add a custom-format dump:

```bash
CORVUS_TEST_SNAPSHOT_DUMP="$SNAPSHOT_DUMP" \
CORVUS_TEST_DATABASE_URL="$DISPOSABLE_DATABASE_URL" \
TENANT_ID=corvus-mind PYTHONPATH=. \
venv/bin/python -m pytest -q -s tests/test_migration_smoke.py
```

The snapshot leg restores the dump, records stable content checksums for
neurons, edges, proposals, chat sessions, and roadmap ledgers, upgrades to
head, and requires the checksums to remain equal. The one permitted historical
transform is migration 013's backfill of NULL `neurons.abstraction_type`; every
other neuron field remains inside the checksum.

## Backup

Use a custom-format dump so restore can be selective and parallelized later.
Do not put credentials in the command line; use `.pgpass`, a protected
`PGPASSFILE`, or an interactive prompt. Keep the destination directory mode
`0700` and the dump mode `0600`.

```bash
umask 077
backup_dir="$(mktemp -d /tmp/corvus-backup-XXXXXX)"
pg_dump --format=custom --no-owner --no-acl \
  --file="$backup_dir/corvus_mind.dump" \
  --dbname=corvus_mind
sha256sum "$backup_dir/corvus_mind.dump"
```

Record the source database, UTC timestamp, application commit, Alembic head (or
the explicit absence of `alembic_version`), dump byte size, SHA-256, PostgreSQL
client/server versions, and elapsed time. Never commit a live dump: it contains
private memory and provenance.

## Restore drill

Restore only into an explicitly named disposable database whose nonexistence
was checked first. Never use `--clean` against a live tenant.

```bash
sudo -u postgres createdb --owner="$DB_OWNER" --template=template0 "$RESTORE_DB"
pg_restore --no-owner --no-acl --exit-on-error \
  --dbname="$RESTORE_DB" "$SNAPSHOT_DUMP"
```

Run the snapshot migration test above, then verify:

```sql
SELECT version_num FROM alembic_version;
SELECT extname FROM pg_extension ORDER BY extname;
SELECT indexname, indexdef
FROM pg_indexes
WHERE schemaname = 'public'
  AND (indexdef ILIKE '% vector_%'
       OR indexdef ILIKE '% hnsw %'
       OR indexdef ILIKE '% ivfflat %')
ORDER BY indexname;
```

Extension and vector-index inventories must match before and after upgrade.
The 2026-07-31 production drill observed only `plpgsql` and no vector indexes:
Corvus currently stores embeddings as text, while pgvector remains a separate
proposed roadmap item. Absence is therefore the present parity target; this
record does not silently introduce a new storage engine.

Before declaring recovery successful, start Corvus against the restored
database on a non-production port and run `/health`, `/recall`, and a known
read-only roadmap request. Confirm tenant identity and stop the disposable
process afterward.

## Failed migration recovery

1. Keep the application stopped. The startup script and schema guard are
   supposed to fail; do not stamp past the error.
2. Preserve the failed database and migration logs for diagnosis.
3. Restore the last verified dump into a newly named database.
4. Run the snapshot migration test and read-only application probes there.
5. Change the tenant's database target only after checksums and probes pass.
6. Retain the old database until the replacement has survived verification.

Do not downgrade or repair production in place unless a separately reviewed
migration explicitly defines that recovery. Historical proposal and audit
tables are provenance, even when their former feature paths are retired.

## 2026-07-31 observed drill

- Frozen source dump: 31,236,208 bytes in 6.53 seconds; SHA-256 recorded
  outside the repository.
- Blank bootstrap: 2.160 seconds; `alembic check`: 1.329 seconds.
- Legacy-snapshot restore: 8.876 seconds; upgrade: 1.150 seconds.
- Restored rows: 1,315 neurons, 3,984 neuron edges, 1,349 proposals,
  1 chat session, and 2 roadmap ledgers.
- Stable checksums matched across the restored upgrade for all five domains.
- Failure found and retained as a test fixture contract: the source database
  had no `alembic_version`; application restart would previously accept that
  unmanaged state and mutate schema during lifespan.
