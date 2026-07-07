# Corvus — Future Feature Backlog

Ideas captured but not yet built. Grounded in real gaps found during use.

## Grounding & anti-hallucination

Context: the frequency-hopping exit layer validates citation *keys* (`[FQ-…]`);
query 495 exposed that a model can still name a standard as authority in bare
prose (`per MIL-STD-1521`) with no key behind it. Shipped so far:
- **Per-slot citation exit + fabrication badge** — strips fabricated `[FQ-…]`
  tokens on every slot and shows a count. (commit 1c9cde2)
- **Ungrounded-reference detection + badge** — flags standards/regs named in the
  answer that aren't in the retrieved context, per slot. (this commit)
- **Prompt guardrail** — instruction forbids naming any regulation/standard/spec
  as authority unless it carries a citation key. (this commit)

### 3. Inline UI distinction: map-backed citation vs. bare prose reference
Render validated `[FQ]` citations as clean superscripts (done), and *visually
mark* standard references in prose that carry no citation token (subtle
underline / amber tint) so a reader instantly sees which authority claims are
grounded vs. asserted. Turns the `ungrounded_refs` signal from a footer count
into inline, at-a-glance context. Frontend-only (reuse `extract_standard_refs`
positions + the citation map).

### 4. Entailment / claim-grounding check (deepest, opt-in)
A valid `[FQ]` key proves the *source exists in context*, not that the claim is
*entailed* by it — the hopping design explicitly leaves entailment out of scope.
Add an opt-in LLM grounding pass on the **primary** answer that checks each cited
claim against its cited neuron's content and flags "cited a real source, but it
doesn't say that." High cost (an extra LLM call), so primary-only + config-gated.
Reuses `check_output_grounding` as the seam.

### 5. Primary answer on higher effort / stronger model
Haiku at low effort invents references more freely. Option: run the **primary**
answer at medium effort (or sonnet) for better grounding while compare slots stay
cheap (haiku/low). A latency/cost trade — expose as a setting (e.g.
`primary_answer_effort`, `primary_answer_model`) rather than hardcoding.
