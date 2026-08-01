# ADR-003: Keep the operational frontend optional to the memory organ

- Status: Accepted retrospectively
- Recorded: 2026-07-29

## Context

Harnesses need memory continuously. The human console is opened intermittently
for observation, review, and bounded control-plane work.

## Decision

Hooks, MCP, timers, the API, and PostgreSQL operate independently of React and
Vite. The frontend remains a real control plane, but not a runtime dependency of
recall, capture, or maintenance.

## Consequences

- Frontend failure does not disable headless memory.
- Consequential UI operations require explicit backend contracts.
- The UI cannot be treated as the canonical owner of domain state.

## Alternatives considered

- Couple memory operation to an open browser.
- Remove the human console and operate only through scripts.
