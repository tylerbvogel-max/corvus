# Corvus × CrewAI — trusted memory for agent crews

Most agent memory is a vector store any agent can write to. That's the
poisoning vector: one bad extraction, prompt-injected "fact", or
hallucinated summary becomes permanent context for every future run.

Corvus is a memory **server** with a different contract:

- **Evidence-gated writes.** Every save is a staged, audited proposal.
  Observational facts auto-commit but *decay if never reinforced*;
  identity- and policy-tier writes queue for human countersign. No
  unguarded write path exists.
- **Instruction-shaped content is dropped.** "From now on always…" never
  becomes a memory.
- **Supersede, never silently overwrite.** A temporal change log records
  (old_value, new_value, changed_at, reason); point-in-time queries
  ("what did we believe on date D?") are first-class.
- **Graph retrieval.** Recall runs spreading activation + inhibitory
  regulation over typed edges — multi-hop associations surface, not just
  cosine neighbors.

## Quick start

Run a Corvus memory tenant (see repo root), then:

```python
from corvus_crewai import make_corvus_tools
from crewai import Agent

recall, remember = make_corvus_tools("http://localhost:8005")
agent = Agent(role="researcher", goal="...", tools=[recall, remember])
```

Agents recall with text queries (full graph pipeline) and save facts with
mandatory evidence.

## Why not `Memory(storage=CorvusStorageBackend())` for reads?

`CorvusStorageBackend` is provided and its **saves** ride the write gate.
But CrewAI 1.x's `StorageBackend.search()` receives only a query
*embedding* produced by CrewAI's own embedder — the query text never
reaches the backend. A remote memory whose retrieval is text-driven
(query classification, graph spread, inhibition) cannot operate on a
foreign embedding vector. `search()` therefore raises with an
explanation, and text recall goes through the tools above.

**Upstream proposal:** pass the query text alongside its embedding in
`StorageBackend.search()` (an additive, backward-compatible parameter).
That single change lets any remote or graph-structured memory provider —
not just Corvus — plug into CrewAI memory natively.
