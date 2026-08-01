# ADR-004: Isolate tenants by database and process identity

- Status: Accepted retrospectively
- Recorded: 2026-07-29

## Context

Private developer memory and evaluation corpora must not share mutable graph
state or lifecycle side effects.

## Decision

`TENANT_ID` fixes configuration and database identity for the life of a process.
Corvus Mind and LoCoMo use separate tenant configurations and PostgreSQL
databases while sharing the engine implementation.

## Consequences

- Cross-tenant contamination is structurally harder.
- Scripts and operators must verify tenant identity before mutation.
- Shared engine fixes require tenant-specific verification where behavior or
  fixtures differ.

## Alternatives considered

- Shared tables with a tenant identifier on every row.
- Separate repositories for each tenant.
