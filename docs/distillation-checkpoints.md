# Distillation checkpoint contract

The PostgreSQL `distillation_checkpoints` row owns the processed input boundary.
Lesson/proposal writes, attribution updates, the boundary, and pending follow-up
work commit together. `save_lesson(commit=False)` preserves the existing evidence
and authority gates, including Assistant-scope review. A filesystem `.distilled`
file is only a repairable receipt projection.

Each source is identified by its canonical absolute path within its database.
Moving sources or sharing a database between installations with different path
names requires explicit operator reconciliation; it is not automatic identity
discovery. Cooperating callers acquire a PostgreSQL transaction advisory lock.
Rollback, cancellation cleanup, connection loss, and process death release it.
The lock is reacquired for post-commit recovery, never retained on a connection
returned to the pool. A busy source rejects the attempt without changing it.

## Input and retry semantics

Complete newline-terminated records are frozen before provider execution. Only
new episode and transcript bytes become extraction evidence; committed prefixes
are SHA-256 checked before advancement. Prior injections remain known-memory
exclusion context, not new attribution evidence. A transcript path change,
rewritten/truncated prefix, malformed complete record, or input over the explicit
16 MiB safety bound requires repair. Trailing incomplete records are not consumed.
The size bound is conservative, not a measured optimal corpus limit.

Episode and transcript files are not captured in one filesystem transaction.
They are immutable for the subsequent provider call, but the producer does not
provide an exact cross-file Stop offset. External provider calls may repeat after
a precommit interruption; this is not exactly-once inference or billing.

After the memory commit, pending enrichment and action-log projections retry
without extracting lessons or applying attribution again. Embeddings and edge
upserts commit before semantic-cache invalidation; failures propagate rather than
being recorded as healthy enrichment. Retired/deleted neurons are not revived.
Action projections use deterministic event IDs and a persistent local file lock.
They are fsynced before acknowledgement. This does not claim distributed log
delivery or change the semantics of unrelated legacy action writers. Marker
replacement is atomic; failure leaves the database receipt recoverable.

`/distill/status` reads database boundaries and separates ready work from blocked
legacy/changed/missing inputs. Pending post-commit work is eligible for recovery
even when its original input is missing. No-work remains healthy, not failure.
One recovery attempt may finish pending work without also processing a later
append; that append remains eligible for a subsequent run.

## Historical data and deployment

Migration 028 adds only the checkpoint table. It does not process memory or import
old markers. A historical marker has no verifiable byte boundary: it is reported
as `legacy-boundary-unverified`, preserved, and never automatically replayed.
Operator-reviewed recovery is required; deleting a marker is not a recommended
backfill strategy. No personal database backfill or service deployment is part
of this change. Apply migrations before enabling the new runtime.

Downgrading 028 deletes checkpoint recovery state. It is therefore not a safe
operational rollback after new distillation runs. Preserve a database backup and
stop distillation before any reviewed rollback; reverting code to the old marker
protocol can suppress appended content and is not an integrity-preserving fix.

## Verification status

Implementation is not completion. Synthetic regression results, migration proof,
required-suite results, limitations, and the protected-main commit belong in the
canonical `memory-showcase-integrity` ledger receipt before closure.

## Backlog health and regression coverage

`GET /distill/status` returns eligible work separately from blocked inputs.
`GET /metrics/mind/slo` reports the backlog as `unknown` when any input is
blocked, because the eligible subset is not the complete outstanding backlog.
Malformed or negative counts also remain unknown. Zero ready and zero blocked
is healthy; a known eligible backlog above the existing threshold is breached.
The SLO's operator guidance points to the status endpoint for blocked details.

The required migration CI job runs the opt-in checkpoint database suite after
Alembic upgrade and schema check. Its HTTP lifecycle fixture uses real routers,
a disposable database and temporary episode files. It replaces unrelated metric
sources, providers and staged memory effects; the suite's separate real-writer
cases cover persistence. This is not a personal-backend or deployed-image test.
