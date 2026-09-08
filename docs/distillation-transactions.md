# Distillation transaction recovery

Each failed item rolls back the current database transaction before the batch
continues or propagates the exception. Only the previously recoverable exception
classes (OSError, ValueError, AssertionError, RuntimeError) become sanitized item
failures. Unexpected exceptions, including database errors, still abort the batch
after cleanup. A rollback failure also aborts; the session is not reused for the
next item. The HTTP maintenance boundary retains its sanitized failure response.

Successful earlier commits are preserved. This is not an atomic batch, and a
rollback cannot undo an item's already completed commit. In particular, database
commit followed by failed filesystem marker persistence remains a separate retry
integrity problem. Cancellation and process death are not covered by this
exception-recovery guarantee. Existing same-job HTTP locks do not make database
and filesystem writes atomic.

## Evidence

`backend/tests/test_distillation_transactions.py` covers recoverable failures,
unexpected errors, rollback failure, preserved earlier commits, success and no
work. `backend/scripts/reproduce_distillation_transactions.py` exercises the
actual distillation service and route with PostgreSQL temporary-table writes,
synthetic episode files and replaced provider calls. It requires an explicit
`CORVUS_TEST_DATABASE_URL` with a disposable database name and uses fresh physical
connections between cases. Run from backend with `TENANT_ID=corvus-mind` and
`PYTHONPATH=.`. It prints observations, not an automatic pass/fail verdict.

Before repair, the recoverable case committed rows `[1, 2, 9]`: failed-item row 1
leaked into the next successful commit. The database-error case left the session
unusable. Expected repaired observations are rows `[2, 9]` for the partial batch
and a usable session containing only committed seed `[9]` after the handled 503.
This direct-route probe is not an independent network HTTP test or a proof of
checkpoint idempotency. It never uses personal episode files or live memory.
