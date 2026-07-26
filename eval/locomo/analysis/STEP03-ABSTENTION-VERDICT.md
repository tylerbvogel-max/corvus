# Step 03 — Evidence-gated abstention: verdict (FALSIFIED, nothing shipped)

**Node:** `mind-calibrated-abstention` (forensics plan, step 3 of 6)
**Date:** 2026-07-26 · **Corpus:** conv-0 standing 263-neuron graph
`ebcad162` (live DB verified byte-identical to the v1/v2/v3 measurement
corpus before any probe ran) · **Base arm:** v3
(`conv0-memory-verified-full-lifecycleverifier-full-v3`, 199q, overall
.6583, adversarial 89.36, non-adv refusal 21.1%)

## The claim under test

Step 02's carry-forward: condition the verifier's unsupported→refusal
CONVERSION on raw retrieval sims (`unsupported + weak sims → refuse;
unsupported + strong sims → release`). More broadly (node text): any
nonzero evidence signal should beat the constant abstention policy.

## What was measured

Three offline probes against the live standing corpus, all
judge-scored with the harness `JUDGE_PROMPT` + judge model, aggregates
preserved (scripts: `step03_conversion_sweep.py`, `step03_force_probe.py`,
`step03_refusal_presence.py`; as-run one-shots + raw JSONs:
`~/.corvus-mind/evals/locomo/20260726T152954Z-step03-abstention-probes/`).
The v3 non-adversarial refusal pool is 32 of 152 questions: 16 verifier
conversions + 16 drafter self-refusals.

### 1. Conversion gate — falsified, correlation is INVERTED

Judging every silenced draft against gold:

| pool | silenced drafts actually CORRECT | top1_sim mean: correct-silenced | wrong-silenced | adversarial conversions |
|---|---|---|---|---|
| v3 (16 non-adv conversions) | **10/16** | 0.636 | 0.761 | 0.648 |
| v2 (19 non-adv conversions) | 10/19 | 0.649 | 0.727 | 0.660 |

The correct-but-silenced drafts sit on **weaker** raw sims than the
rightly-silenced wrong drafts, and are indistinguishable from adversarial
conversions. 6 of v3's 10 correct-silenced are open-domain inference
(gold expects a short judgment-call inference, not a stored fact) —
weak-sim by nature. Threshold sweep (both sim fields, both arms): best net is
+2/199 (v3) at thresholds that simultaneously release 4–6 wrong drafts
as new wrong assertions; the zero-adversarial-loss point (top1_sim ≥
0.725) nets +1 while releasing 5 wrong assertions. On Corvus workloads a
wrong answer is worse than no answer, so every point on the curve is a
bad trade. **Do not ship any conversion release.**

### 2. Forced attempt on drafter self-refusals — falsified as a scorer

Motivation: non-adv self-refusals sit at median top1_sim 0.685 (7 ≥
0.745) vs adversarial self-refusals at 0.603 (1 ≥ 0.745) — a real
separation, so "strong sims + refusal-without-attempting = disposition"
looked actionable. Probe: force a redraft (refusal forbidden), verify
with the v3 predicate (fail-closed), judge draft and final. Recall
reconstruction drift vs recorded telemetry: 0/50.

- Run 1 (all 50 self-refusals): non-adv final correct **1/16**;
  adversarial leak (fabrication waved through + judged wrong) **1/34** —
  the guardrail holds even under maximal forcing pressure.
- Run 2 (16 non-adv, drafts judged too): forced drafts correct **7/16**,
  but the verifier re-silenced 12, **including 5 of the 7 correct**;
  final correct 2/16, plus 2 new wrong assertions.

Forcing recovers ~1–2 questions and mints wrong assertions; no sim
threshold changes that. **Do not ship the forced-attempt gate.**

### 3. Where the refusals' evidence actually lives

Gold content-token coverage for the 32 wrongful refusals (null control:
other conversations' golds vs this graph — mean 0.448, full-coverage
rate 0.24):

| pool | mean cov in graph | mean cov in delivered top-10 | FULL in graph | FULL in delivered | stranded (in graph, not delivered) |
|---|---|---|---|---|---|
| draft-refused (16) | 0.858 | 0.449 | 9 | 3 | 6 |
| conversions (16) | 0.741 | 0.483 | 7 | 4 | 3 |
| **all (32)** | 0.800 | 0.466 | **16** | **7** | **9** |

## Verdict

At the v3 margin, residual over-refusal is **not an abstention-threshold
problem**. It decomposes into:

1. **Delivery starvation** (→ Steps 05/06): 9/32 refusals have the gold
   evidence in the graph but outside the delivered top-10; mean
   delivered coverage 0.466 vs 0.800 in-graph. The retrieval window,
   not the refusal policy, withholds the evidence.
2. **Verifier predicate precision on inference** (measured seam, no
   owner yet): 10/16 conversion-silenced and 5/12 re-silenced forced
   drafts were correct — mostly inferential answers the answerhood
   predicate cannot verify from delivered text. Raw sims cannot
   arbitrate this (correlation inverted, §1). Step 02 already measured
   the predicate dial's endpoints (v1 lenient: adversarial 76.6 FAIL ↔
   v3: 89.36 + this silencing); wording tuning is a dead end per the
   Step 02 verdict, and this data confirms sims don't rescue it either.
3. **True absence** (ingest density): 16/32 refusals lack full gold
   vocabulary in the whole graph (against a 0.24 null base rate —
   substantially above chance but far from universal).

The node's premise — "a constant policy is beaten by ANY signal with
non-zero predictive power" — fails at this margin: top1_sim's global
refused-vs-answered signal (AUC 0.632, step 01) is absent-to-inverted
exactly inside the pools where a gate would change a decision.

**Nothing ships. The headline number does not move; no re-certification
is triggered.** The adversarial guardrail was re-confirmed robust under
the most aggressive policy this node could have shipped (1/34 leak).

## Caveats

- Conv-0 only; pools are small (16–32). The falsification rests on the
  *direction* of the correlations (replicated across v1/v2/v3 pools and
  two independent probe runs), not on the exact deltas.
- Probes are offline analysis, not certificate arms: no
  llm-receipts.jsonl. Models pinned by the default env (sonnet
  draft/judge, haiku verify); the codex fallback was disabled in the
  force probes but not in the conversion-judgment run (adjacent runs on
  the same provider succeeded throughout, so a silent fallback is
  unlikely, but it is unverified).
- Probe recalls fire neuron usage stats (same class of perturbation as
  any answer phase; corpus receipt covers label+content only and is
  unchanged).
- ~190 offline LLM calls total, $0 marginal (CLI subscription).
