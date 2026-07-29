# Corvus vs. Zep and mem0 — convergent surface, different animal

**Date:** 2026-07-29 · **Status:** Analysis (design retrospective, not a spec)
**One-liner:** Corvus, Zep/Graphiti, and mem0 converge on the same outer
plumbing but organize around different theses — mem0 optimizes a compact
current fact set, Zep optimizes a temporal world model, and Corvus optimizes
*trust*. Same silhouette, different animal.

This document answers a recurring question: is Corvus just another agent-memory
system that happens to reach the same place as Zep and mem0 by a different
route, or is it solving a genuinely different problem? Grounded in the shipped
Corvus architecture (`CORVUS-MIND-DESIGN.md`, `README.md`, and the recall/gate
code) and the current public architecture of the other two.

---

## 1. The short version

**Convergent at the plumbing, divergent at the thesis.** Diff the outer
contract — capture → distill facts → store → retrieve on query → inject into
context, plus some temporal handling of staleness — and all three rhyme. Corvus
*knows* it rhymes: `backend/app/services/recall_lanes.py` names "the Mem0
retrieval lesson" as the reason it added lexical and entity retrieval lanes
alongside embeddings.

But the organizing idea underneath differs enough that Corvus is not a
reimplementation of the same product:

- **mem0** asks *"what is the compact, current set of facts about the user?"*
- **Zep** asks *"what does the world look like, and when did each fact change?"*
- **Corvus** asks *"which memories have earned the right to influence the next action?"*

The last question forces primitives the first two never need.

---

## 2. What is genuinely shared (the convergent layer)

- **Pipeline shape.** Ingest → LLM-distill salient content → persist → hybrid
  retrieve → feed the model. mem0 (extract + update), Zep/Graphiti (extract
  entities + edges), and Corvus (episode → distiller → write gate → graph) are
  structurally the same loop.
- **Past pure cosine.** All three abandoned vector-only recall. Zep fuses
  semantic + BM25 + graph traversal; mem0 added entity linking; Corvus fuses an
  embedding lane + a Postgres `tsvector` lane + an entity lane via
  reciprocal-rank fusion (`recall_lanes.py`, `scoring_engine.py`). Corvus
  arrived here *by measuring* — an embedding-similarity 0.832 complementary pair
  outranking a 0.807 true duplicate — and by explicitly borrowing mem0's lesson.
  This is the clearest deliberate convergence in the codebase.
- **Temporal awareness.** Zep's bitemporal edge invalidation (`invalid_at`) and
  Corvus's supersede-with-`was-true-until` are close cousins: both refuse to let
  "this used to be true" simply vanish.

If you squint only at the retrieval subsystem, the three are siblings.

---

## 3. Where the thesis actually diverges

| Axis | mem0 | Zep / Graphiti | Corvus |
|---|---|---|---|
| **What memory is *of*** | User facts & preferences from dialogue | An entity-relationship world model extracted from conversation | *Agentic episodes with outcomes* — tool traces, exit codes, migration failures, port conventions. Explicitly **refuses** to store "best practices" (model weights absorb those within a generation) |
| **Core scarce resource** | Token efficiency / freshness | Temporal reasoning / provenance | **Trust.** "Bad memory is worse than no memory" |
| **Admission** | LLM decides ADD / UPDATE / DELETE / NOOP at write | LLM entity + edge extraction with validity intervals | **Evidence-gated ladder**: born at *informational*, promotes to *guidance* / *organizational* only on a verifiable outcome (test passed, exit 0 after a documented failure, user confirmation) |
| **Conflict resolution** | DELETE the loser | Invalidate the edge (bitemporal) | Demote-with-history **plus a third verdict the others lack: SCOPING** — "true in repo A, false in repo B" is conditioned on a `scoped-by` edge, not resolved as a contradiction |
| **LLM in the read path** | Sometimes (rerank) | Yes, at points | **Never** — hard axiom (~150ms embed-only recall); reads synchronous and free, writes asynchronous and expensive |
| **Integration model** | SDK you call | SDK / API you call | **Harness-invisible** — hooks + MCP; ambient injection on `UserPromptSubmit` with zero model cooperation |
| **Human role** | None (managed service) | None (managed service) | **Governor of a self-authoring system** — curates policy, not instances |

---

## 4. Corvus primitives with no real equivalent in either

- **Memory → capability compiler.** Stable consolidated lessons emit *skill
  files* into `~/.claude/skills/` (`skill_compiler.py`). Memory becomes native
  progressive-disclosure playbooks that load on task match at ~zero standing
  cost and work even with the backend down (push = injection, pull = skills).
  Neither mem0 nor Zep compiles memory into new agent capabilities.
- **Adversarial self-testing as a first-class layer.** Honeypot episodes to
  verify dedup/consolidation thresholds; citation hopping (per-query ephemeral
  `[FQ-xxxxxx]` keys that catch fabricated citations deterministically,
  `citation_hopping.py`); memory-poisoning defenses; secret redaction *at
  capture* (`redaction.py`); the work/personal capture wall. This is a security
  posture — memory as an attack surface — not a data-store feature.
- **A governance / policy plane.** The human curates a "constitution" (e.g.
  destructive-op memories need N confirmations, user-preference memories outrank
  efficiency memories, nothing crosses a tenant wall) rather than individual
  memories. A legacy of Corvus's regulated-domain (aerospace) origin; the
  managed competitors are autonomous by design with no per-memory human role.
- **A different kind of graph.** Zep's is an *ontology of the world* — nodes are
  entities, edges are semantic facts with validity. Corvus's is a *neuron graph*
  with spread activation and 6-signal scoring (relevance, impact, burst,
  recency, precision, novelty). The difference is load-bearing: Corvus had to
  **gate its temporal/provenance edges to zero propagation**
  (`mind_janitors.py`, the `_ETYPE_CODE` memory-semantics tier), because
  otherwise a `supersedes` edge would *boost* the very node it supersedes. Zep
  never faces this because its graph does not conduct activation.
- **Self-reinforcement immunity.** Because Corvus injects its own memories into
  future sessions, a naive consolidator would count an injected lesson's downstream
  episodes as fresh "confirmations" and inflate its weight with no new evidence.
  The consolidation rule discounts them: an episode from a session where the
  lesson was already injected is a *usage*, not a *confirmation*. This problem
  only exists because memory is a live instruction channel back into the agent —
  a loop mem0/Zep's read-only recall model does not create.

---

## 5. The honest caveat

Corvus's "trust is the hard part" framing is **not orthogonal to the field — it
is early to where the field is heading.** The 2025–26 research frontier is
visibly converging on the same posture: SAGE (a novelty gate for memory
evolution), MemGuard (preventing memory contamination), MOSS (auditable agentic
memory), and especially **PROJECTMEM** (a local-first, event-sourced *judgment
layer* for AI coding agents, which is close to Corvus in spirit). So the fair
statement is not "Corvus invented an axis nobody else has." It is: the *shipped
products* — mem0 and Zep — optimize retrieval and temporal reasoning, while
Corvus optimizes verification, and the *research* is catching up to that choice.

It is also worth conceding the genuine overlaps rather than overclaiming:
Zep's bitemporal invalidation and Corvus's supersede-with-history are close;
mem0's UPDATE-if-information-increases and Corvus's promotion ladder both fight
staleness; and all three now do hybrid retrieval. The novelty is in the
*combination and priority*, not in every component.

---

## 6. Bottom line

Not convergent design — **convergent surface, different solution.** You could
describe all three with one sentence ("a memory layer for agents that beats
stuffing everything into context"), and their retrieval subsystems have
genuinely rhymed over time. But the questions they organize around differ, and
Corvus's question — *which memories have earned the right to act?* — forces a
set of primitives (the evidence ladder, SCOPING, no-LLM-in-hot-path, the skill
compiler, capture-layer redaction, honeypots, self-reinforcement discounting)
that a personalization-first memory layer never has to build. The "same feel" is
arrived at from a meaningfully different place.

---

## Sources

- Zep: A Temporal Knowledge Graph Architecture for Agent Memory — https://arxiv.org/abs/2501.13956
- What Is a Temporal Knowledge Graph? (Zep) — https://www.getzep.com/ai-agents/temporal-knowledge-graph/
- Mem0: Scalable Memory Architecture (EmergentMind) — https://www.emergentmind.com/topics/mem0-system
- State of AI Agent Memory 2026 (mem0) — https://mem0.ai/blog/state-of-ai-agent-memory-2026
- PROJECTMEM: Local-First, Event-Sourced Memory for AI Coding Agents — https://arxiv.org/pdf/2606.12329
- MemGuard: Preventing Memory Contamination in Long-Term Memory-Augmented LLMs — https://arxiv.org/pdf/2605.28009

*Internal grounding:* `CORVUS-MIND-DESIGN.md`, `README.md`,
`backend/app/services/recall_lanes.py`, `citation_hopping.py`,
`mind_janitors.py`, `skill_compiler.py`, `redaction.py`.
