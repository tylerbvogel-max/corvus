# ADR-002: Fail open for continuity and fail closed for durable writes

- Status: Accepted retrospectively
- Recorded: 2026-07-29

## Context

A temporary memory failure should not strand a coding session. A bad durable
write compounds across future sessions and can pollute skills and standing
policy.

## Decision

Ambient recall and capture degrade without blocking the harness. Durable
mutations require evidence framing, typed validation, authority routing, audit,
and human review where direction or identity is affected.

## Consequences

- Coding continues through temporary memory outages.
- Write workflows accept additional review latency and implementation ceremony.
- Retrieval availability and memory integrity deliberately have different
  failure policies.

## Alternatives considered

- Fail closed for every hook.
- Permit best-effort direct graph writes from agents and models.
