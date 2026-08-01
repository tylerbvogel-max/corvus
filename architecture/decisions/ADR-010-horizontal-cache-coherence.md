# ADR-010: Keep high-churn recall state PostgreSQL-canonical across workers

- Status: Accepted
- Recorded: 2026-07-29

## Context

The original recall accelerators assumed one backend process:

- `NeuronIndex` copied every firing row and retained every distinct query ID.
  Its memory grew with all historical use and was duplicated per worker.
- The adjacency cache copied the full mutable edge graph (plus a CSR copy).
  Co-fire learning changed that graph on nearly every query, but only the
  writing process received the incremental update.
- The semantic matrix was small and useful, but neuron/engram writes
  invalidated only the writer's process.

Adding workers under that design multiplied memory and allowed two successful
requests against the same PostgreSQL database to observe different recall
state.

## Decision

`CACHE_COHERENCE_MODE=database` is the horizontal-safe policy:

1. Firing aggregates are computed from PostgreSQL. The optional process-local
   index is bypassed; when used in single-worker mode it stores scalar all-time
   aggregates plus only the configured burst window.
2. Spread activation fetches each frontier bidirectionally from canonical
   neuron/engram edge rows. Returned edges and the next frontier have explicit
   hard bounds.
3. Neuron and engram embeddings remain 384-dimensional JSON arrays in
   PostgreSQL `TEXT` columns; Corvus does not currently install or query
   pgvector. Each worker loads active vectors into a small process-local NumPy
   `float32` matrix because matrix cosine selection is cheap at the present
   corpus size. A trigger-maintained `cache_versions` row advances only when an
   embedding or active set changes; every worker checks that O(1) revision
   before using its matrix.
4. Worker lifespans serialize canonical seed initialization with a
   tenant-database PostgreSQL advisory lock. Schema migration is an external
   Alembic deployment step governed by ADR-011. Regulatory seed completeness
   is calculated from the tenant's real tree, making the empty memory-tenant
   tree idempotent instead of repeatedly deleting its valid department anchor.

PostgreSQL is therefore the coherence mechanism, not a broadcast bus or sticky
session.

## Consequences

- Worker count no longer multiplies firing-history or adjacency memory.
- Committed firing and edge writes are visible to every subsequent database
  read without cache-invalidation races.
- Embedding changes become visible on each worker's next semantic operation.
- Concurrent worker startup cannot race canonical seeding; schema DDL is never
  executed inside the worker lifespan.
- Database-backed spread performs bounded SQL reads per hop and currently uses
  the configured manual hop fallback when no local CSR exists.
- The explicitly deployed 2,000-node frontier and 10,000-edge-per-hop values
  remain initial safety ceilings, not empirically optimal constants. Each
  database-backed spread persists frontier truncation, dropped-node count,
  edge-limit saturation, and observed maxima in stage/retrieval telemetry;
  `/metrics/mind` aggregates cap-hit rate over the current recall window.
  Recalibration therefore requires observed pressure rather than intuition.
- `process-local` mode remains available as an explicit single-worker latency
  optimization, not a horizontally correct deployment mode.

## Alternatives considered

- Broadcast invalidations and reload the whole adjacency graph after almost
  every query.
- Keep process-local caches with sticky sessions and accept divergent recall.
- Move cosine selection into PostgreSQL without first introducing an indexed
  vector representation.
- Adopt Redis as a second canonical coordination system.
