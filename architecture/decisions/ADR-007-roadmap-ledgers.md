# ADR-007: Preserve forward intent in revisioned Roadmap Ledgers

- Status: Accepted retrospectively
- Recorded: 2026-07-29

## Context

Memory preserves learned behavior, but unfinished plans, acceptance criteria,
strategic assumptions, and dispositions need a durable canonical register.

## Decision

Roadmap Ledgers own forward intent. A mapped project mutation requires a session
admission pinned to the ledger's current revision.

## Consequences

- Agents receive durable kickoff and verification context.
- Revision drift invalidates stale authorization.
- Off-ledger work is explicit, and admission never implies roadmap completion.

## Alternatives considered

- Store plans only in transient agent plans or static Markdown.
- Infer mutation permission from cwd without revision awareness.
