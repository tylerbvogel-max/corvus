# ADR-011: Make Alembic the sole schema-mutation authority

- Status: Accepted
- Recorded: 2026-07-31

## Context

FastAPI lifespan mixed canonical seeding with hundreds of lines of runtime DDL.
The initial Alembic baseline did not create a fresh database, Docker and systemd
used different startup contracts, and the live Corvus Mind database had no
`alembic_version` table. A process could therefore accept or mutate an
unmanaged schema as a startup side effect.

## Decision

Alembic is the only normal schema-mutation authority. Docker and systemd use
one startup script that runs `alembic upgrade head` before Uvicorn. FastAPI
lifespan performs a separate read-only check that the connected database heads
exactly match the packaged Alembic heads before any seed write. Fresh bootstrap
and representative restored-snapshot upgrades are destructive opt-in tests
against database names that are mechanically constrained to disposable
prefixes.

## Consequences

- Blank databases, legacy databases, and normal deployments use one migration
  chain.
- Unmanaged, behind, ahead, and unreachable databases fail before application
  seeding.
- Backup and restore verification includes stable content checksums and
  extension/vector-index inventory parity.
- Runtime seeding remains serialized, but application workers never coordinate
  or execute schema DDL.

## Alternatives considered

- Continue application-owned idempotent DDL.
- Stamp fresh databases at head after `Base.metadata.create_all` outside
  Alembic.
- Let Docker auto-migrate while systemd and manual Uvicorn starts trust whatever
  schema they find.
