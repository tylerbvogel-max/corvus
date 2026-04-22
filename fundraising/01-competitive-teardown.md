# Competitive Teardown — Corvus vs. the Knowledge/Governance Landscape

Last updated: 2026-04-13

## Purpose

Identify where Corvus is **demonstrably different**, not just "better." Investors will default-assume you're a Glean/Collibra clone with extra steps unless you can articulate the wedge in one sentence. This doc maps the five closest competitors on the axes that actually matter for a provenance-first positioning.

---

## The five comps

| Company | What they are | Where they win | Where they have a structural gap |
|---|---|---|---|
| **Glean** | Enterprise search + RAG assistant | Breadth of connectors (100+ APIs), semantic search polish, "Google for your company" UX | Shallow provenance — knows *where* a chunk came from, not *why* it was authoritative, who governs it, or the causal chain between sources |
| **Palantir Foundry** | Ontology + lineage + ops platform | Deepest provenance + lineage in market; regulated-industry credibility; ontology is the gold standard | Sold top-down via Forward Deployed Engineers; 6–18mo deployments; mid-market can't afford or wait; not self-serve |
| **Collibra** | Data governance catalog | Mature compliance workflows, Forrester Wave Leader in governance + AI governance | Slow, expensive, metadata-about-data (not knowledge-graph-native); bolted-on AI story |
| **Atlan** | Active metadata platform | "Metadata lakehouse," AI pipeline lineage as native capability, modern UX, fast-growing | Still catalog-framed; graph is metadata-graph not knowledge-graph; lineage is pipeline-centric not decision-centric |
| **Writer (Palmyra + KG)** | Full-stack enterprise LLM + KG | Owns model + KG + UI; "grounding" narrative; closed-loop system | KG is a grounding layer for content generation, not a reasoning/decision substrate; provenance is "which doc"-level |

---

## The axes that matter

### 1. Unit of provenance

- **Glean / Writer KG**: chunk or document. "This answer cites doc X."
- **Atlan / Collibra**: dataset or column. "This field flows from source Y."
- **Palantir Foundry**: object + action. "This decision was made on object Z in state S, by user U."
- **Corvus**: *neuron* — a typed, scored, governed node with 5 signals (Burst/Impact/Precision/Novelty/Recency), gating, and an edge graph that represents *why* it fires. This is a finer grain than document/dataset and a different grain than object/action.

**The wedge**: Corvus is the only system where provenance is recorded at the unit of *reasoning* rather than the unit of *storage* or *decision*.

### 2. How provenance gets enforced

- Glean: none (retrieval cites sources, doesn't gate them).
- Palantir: governance workflows, access policies, branching, checkpoint justifications.
- Collibra/Atlan: policy automation over metadata.
- Corvus: **gating signals** (Precision, inhibitory regulation, chandelier dampening) are part of the retrieval pipeline itself — ungoverned or low-precision neurons cannot fire into the context window. Governance is *computational*, not procedural.

### 3. Deployment model

- Palantir: months, FDE-led, seven-figure contracts.
- Glean/Writer/Atlan/Collibra: weeks to months, sales-led.
- Corvus *could* be: self-serve, tenant-per-domain (you already have 5 tenants running), MCP-exposed for external agents.

This is the Glean-vs-Palantir gap: Palantir has the depth, Glean has the motion. **Corvus's claim should be: Palantir-depth provenance with Glean-speed deployment.**

### 4. Graph semantics

- Glean/Writer: vector index + light knowledge graph. Edges are mostly "mentions" / "similar to."
- Atlan/Collibra: metadata graph. Edges are "flows to" / "owned by" / "tagged as."
- Palantir: ontology. Edges are typed business relationships.
- Corvus: **neuromorphic graph** — 6-layer hierarchy (Dept→Role→Task→System→Decision→Output), typed edges (stellate/pyramidal/etc.), promoted vs. weak edges, spread activation, inhibitory regulation.

The biomimetic framing is a double-edged sword: differentiating to curious technical buyers, off-putting to literal governance buyers. **Recommend: lead with the outcome ("governed context assembly"), not the biology.**

### 5. Narrative fit to 2026 tailwinds

| Tailwind | Glean | Palantir | Collibra | Atlan | Writer | Corvus |
|---|---|---|---|---|---|---|
| RAG/assistants | ✅ core | ⚪ adjacent | ⚪ adjacent | ⚪ adjacent | ✅ core | ✅ core |
| AI agent governance | ⚪ bolt-on | ✅ native | ⚪ bolt-on | ⚪ evolving | ⚪ claimed | ✅ native |
| EU AI Act / SEC AI disclosure | ⚪ | ✅ | ✅ | ✅ | ⚪ | ✅ if messaged |
| "Palantir for mid-market" | ❌ | N/A | ❌ | ⚪ | ❌ | ✅ **open lane** |

---

## One-sentence wedge (draft)

> Corvus is the governed-context layer for enterprise AI agents — Palantir-depth provenance at Glean-speed deployment, priced for the mid-market that can't afford a Forward Deployed Engineer.

## Competitive risks to pre-empt

1. **"Why not just buy Glean + Collibra?"** — Because integration of retrieval + governance is the product; bolting two tools together doesn't give you gated retrieval where policy decisions happen at firing-time, not audit-time.
2. **"Palantir will extend Foundry downmarket."** — Probably true eventually. Corvus's defense: self-serve motion + multi-tenant architecture Palantir will never ship because it conflicts with FDE economics.
3. **"LLMs will make the knowledge graph obsolete."** — Inverse of true. Larger context windows make *governance of what goes into them* more valuable, not less. Agents acting autonomously make provenance load-bearing for legal liability.
4. **"Atlan already does AI lineage."** — Atlan tracks lineage *of the pipeline*. Corvus tracks provenance *of the reasoning*. Show the difference in a demo, not in slides.

## What the demo must show

To survive first meetings with the Glean-class investor list (Sequoia, KP, Lightspeed, General Catalyst):

1. A query that retrieves context from a governed neuron graph, with every fired neuron traceable to its source + policy justification.
2. The same query with a **policy change** applied mid-session — neurons that previously fired now dampen; answer changes; audit trail is automatic.
3. An agent (via MCP) consuming Corvus context and producing a decision with full provenance attached to the output.
4. Tenant isolation: same query, two tenants, different domain graphs, zero cross-contamination.

You already have 4 of 5 tenants running and MCP exposure — the demo is mostly scripting, not building.

---

## Sources
- [Palantir Foundry Ontology](https://www.palantir.com/platforms/foundry/foundry-ontology/)
- [Palantir Data Lineage docs](https://www.palantir.com/docs/foundry/data-lineage/overview)
- [Atlan vs. Collibra comparison](https://atlan.com/collibra-alternatives-enterprise-data-governance/)
- [Writer Agentic AI governance](https://writer.com/guides/agentic-ai-governance/)
- [Collibra](https://www.collibra.com/)
