# ADR-001: Separate cheap recall from provider-model judgment

- Status: Accepted retrospectively
- Recorded: 2026-07-29

## Context

Recall runs at prompt and tool boundaries, where latency and availability matter.
Provider-model judgment is slower, fallible, and dependent on external CLI
availability.

## Decision

Default recall uses local embeddings, PostgreSQL retrieval, graph scoring,
spread, inhibition, and prompt assembly without invoking a provider LLM.
Distillation, compilation, critics, extraction, agents, and optional answering
may use bounded provider-model calls.

## Consequences

- Ambient recall remains available during provider outages.
- Retrieval quality must be improved through observable deterministic stages.
- Model-bearing workflows require separate timeout, validation, and audit paths.

## Alternatives considered

- Invoke a model on every recall.
- Use keyword-only retrieval without semantic or graph stages.
