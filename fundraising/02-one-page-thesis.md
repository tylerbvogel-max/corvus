# Corvus — One-Page Thesis

**Corvus is the governed-context layer for enterprise AI agents.** Palantir-depth provenance, Glean-speed deployment, priced for the mid-market.

---

## Why now

Three forces are converging in 2026:

1. **Agents are taking real actions.** Every enterprise is deploying AI agents that write code, file tickets, move money, or touch customer records. Each action creates legal and operational liability.
2. **Regulation is arriving.** EU AI Act (enforcement phases through 2026), SEC AI-disclosure rules, and sector-specific guidance (FDA, FAA, DoD) all require traceable decision provenance. "The model said so" is not a defense.
3. **The existing stack is split.** Retrieval vendors (Glean, Writer) don't govern. Governance vendors (Collibra, Atlan) don't retrieve. Palantir does both but only for Fortune 500 customers willing to host FDEs for 9 months. **Nothing serves the agent-shipping mid-market.**

## The insight

Retrieval and governance are the *same problem*. You cannot govern what you don't control retrieval of, and you cannot safely retrieve into an agent's context without governance. Building them as one system — where policy decisions happen at retrieval time, not audit time — is the only path to provably safe autonomous agents.

## The product

Corvus is a multi-tenant neuromorphic knowledge graph. Instead of storing documents and retrieving chunks, it stores **neurons** — typed, scored, governed units of reasoning — and runs queries through an 8-stage pipeline that *gates* what reaches the model's context window.

- 6-layer ontology: Department → Role → Task → System → Decision → Output
- 5-signal scoring (Burst, Impact, Precision, Novelty, Recency) with inhibitory regulation
- Typed edges + spread activation over a promoted-edge graph
- Policy enforcement at firing-time: ungoverned neurons cannot reach the context window
- MCP-exposed for external agents; multi-tenant for domain isolation

**Already running in production across 5 tenants** (aerospace, plumbing, real estate, investment, personal). ~2,800 neurons, ~463K edges, Claude CLI integration, NASA-compliance linter on the codebase.

## The wedge customer

AI-native mid-market companies (500–5,000 employees) shipping agent products who are getting asked by *their own customers* "how do you know the agent is right?" and can't answer with document citations. Named prospects: defense-aerospace primes' subcontractor tier, regulated-industry SaaS vendors, agentic coding platforms.

These customers:
- Feel the pain now (their buyers demand provenance for procurement).
- Can't afford Palantir or wait 9 months.
- Are technical enough to deploy a self-serve graph.
- Produce logos that unlock enterprise later.

## Why this team

*[This is the section you most need to strengthen. Current gap: solo builder, no recognizable provenance/governance pedigree. Fix path in 03-angel-operator-targets.md. Interim framing: deep technical build already in production across 5 domains, NASA-standard engineering rigor, domain expertise in defense aerospace as the first wedge tenant.]*

## What we're raising

A **$750K–$1.5M pre-seed** from operator angels (Palantir / Databricks / Snowflake / Glean alumni) to:

1. Convert 2 existing tenants into paid design partners.
2. Recruit a credibility co-founder from the provenance/governance space.
3. Ship the governed-context demo that pattern-matches to Sequoia/KP/Lightspeed for an institutional seed 9–12 months later.

## The asymmetric bet

Glean's 2019 bet was: "knowledge graphs will matter when LLMs arrive." They were right and are now worth $7.2B at ~$200M ARR.

Corvus's 2026 bet is: **"governance of knowledge graphs will matter when agents arrive."** The tailwind is bigger (regulation + liability, not just user desire), the moat is deeper (governance compounds with usage), and the lane is open (no self-serve Palantir exists).

---

*Companion docs: `01-competitive-teardown.md`, `03-angel-operator-targets.md`.*
