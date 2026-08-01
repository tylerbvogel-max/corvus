# ADR-005: Use one typed Action Bus for durable graph mutation

- Status: Accepted retrospectively
- Recorded: 2026-07-29

## Context

Proposals, agents, maintenance, reconsolidation, and explicit saves need
consistent validation, idempotency, audit, and reversal semantics.

## Decision

Governed writers converge on typed Action Bus handlers and proposal application.
Workflow-specific direct ORM writes are architectural debt unless explicitly
exempted as bootstrap, evaluation, or migration behavior.

## Consequences

- Mutation receipts are attributable across different producers.
- Rollback and cache-rebuild semantics can be centralized.
- Bypasses are visible to architecture fitness checks.

## Alternatives considered

- Let every workflow commit ORM changes directly.
- Build separate mutation buses for proposals, agents, and janitors.
