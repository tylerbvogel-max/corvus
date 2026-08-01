# ADR-008: Treat architecture as evidence-linked source-derived state

- Status: Accepted
- Recorded: 2026-07-29

## Context

Independent architecture drawings decay as code, routes, deployment boundaries,
and ownership change.

## Decision

The architecture manifest owns reviewed intent. Deterministic extraction owns
source facts. Conformance joins them, verifies evidence, fingerprints freshness,
and drives every Architecture UI view.

## Consequences

- Contradictions and stale artifacts remain visible.
- Static-analysis limitations and accepted debt baselines must be explicit.
- Diagrams are rendered from the model rather than maintained separately.

## Alternatives considered

- Maintain independent diagrams in a drawing tool.
- Generate architecture only from imports with no authored intent.
