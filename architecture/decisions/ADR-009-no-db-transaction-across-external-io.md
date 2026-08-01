# ADR-009: Do not hold database transactions across external model waits

- Status: Accepted
- Recorded: 2026-07-29

## Context

SQLAlchemy request sessions autobegin on their first query. Without an explicit
phase boundary, the session keeps a pooled PostgreSQL connection checked out
while a provider CLI, fallback, citation repair, or entailment judge can wait
for seconds or minutes. With a bounded pool, concurrent model work then turns
into database starvation even though the model subprocesses are doing no
database work.

## Decision

Query delivery is split into three phases:

1. Read and materialize all graph/context inputs.
2. Commit the completed read phase, return its connection, and perform external
   model work using in-memory inputs only.
3. Open a fresh transaction to persist the Query, citation audit, coverage,
   firing, cost, and learning receipts.

DB-aware evaluate, refine, follow-up, title, and entailment callers use the same
explicit release boundary before invoking a provider. Provider-slot helpers do
not receive a database session.

## Consequences

- Slow provider waits consume provider capacity, not PostgreSQL pool capacity.
- A Query row is not visible until model and pre-persistence citation work has
  completed; failures before that point leave no incomplete Query artifact.
- Loaded ORM objects remain readable because the application sessionmaker uses
  `expire_on_commit=False`.
- Callers must place the boundary only after the current database phase is
  semantically complete because it commits pending writes.

## Alternatives considered

- Increase the PostgreSQL pool size and retain request-long transactions.
- Insert and commit a pending Query row before the provider call.
- Open separate sessions for every stage while retaining the request session.
