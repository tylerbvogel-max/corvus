# Step 05 — Adaptive recall depth verdict

**Verdict: measured negative; do not ship. Fixed `top_k=10` remains the
production policy.**

The score cut worked mechanically and failed behaviorally. All three
pre-registered thresholds produced broad, nonconstant realized-depth
distributions, but every threshold scored below the fixed-depth control.
The strongest cut (`0.95`) produced a statistically clear paired loss.

## Controlled protocol

- Conversation 0, all 199 questions, verifier-split answer policy.
- Exact Anthropic CLI provider: Opus distill, Sonnet answer/judge, Haiku
  verifier; no provider aliases and zero fallbacks.
- Full-lifecycle corpus ingested once, then frozen as a PostgreSQL custom
  archive and restored before every arm.
- Frozen corpus: 273 neurons, 2,135 edges, corpus SHA-256
  `060606dbebf77ef95af5118c69ed023c49897ad07ca857e464b655524256dc19`.
- Frozen database archive SHA-256:
  `cd5fd53422e0299ed29f48470ebb020d89f2913a41dccfcc42d65ed665970e31`.
- Floor `k=2`, ceiling `k=10`; cut field was the final ranked `combined`
  score emitted by Step 01 telemetry.

The fixed arm and each threshold arm answered from the same database state.
This avoids the firing-history drift that would occur if the arms ran
sequentially without restore.

## Headline result

| Arm | Overall | Δ vs fixed | Mean k | Median k | Range | Gains | Losses | Exact McNemar p |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Fixed | 67.34% | — | 10.00 | 10 | 10–10 | — | — | — |
| 0.90 | 65.83% | −1.51 pp | 6.13 | 5 | 2–10 | 2 | 5 | 0.4531 |
| 0.925 | 64.32% | −3.02 pp | 5.26 | 5 | 2–10 | 5 | 11 | 0.2101 |
| 0.95 | 61.31% | −6.03 pp | 4.18 | 4 | 2–10 | 3 | 15 | 0.0075 |

Realized-k distributions:

- `0.90`: `{2:20, 3:28, 4:23, 5:31, 6:9, 7:18, 8:11, 9:9, 10:50}`
- `0.925`: `{2:35, 3:34, 4:27, 5:27, 6:18, 7:12, 8:7, 9:7, 10:32}`
- `0.95`: `{2:65, 3:33, 4:25, 5:27, 6:15, 7:14, 8:8, 9:5, 10:7}`

The threshold did not collapse back to ten. Step 05 tested a real policy.

## Per-category deltas

| Arm | Multi-hop | Temporal | Open-domain | Single-hop | Adversarial |
|---|---:|---:|---:|---:|---:|
| 0.90 | −9.37 pp | 0.00 pp | 0.00 pp | 0.00 pp | 0.00 pp |
| 0.925 | −6.24 pp | −2.70 pp | −15.39 pp | −4.28 pp | **+4.26 pp** |
| 0.95 | −12.50 pp | 0.00 pp | −15.39 pp | −8.57 pp | 0.00 pp |

The roadmap's counter-hypothesis won. At `0.925`, trimming raised adversarial
accuracy from 89.36% to 93.62% (two gained, zero lost) but reduced every
breadth category. Open-domain fell from 30.77% to 15.38%. At `0.95`, the
adversarial gain disappeared while breadth losses grew.

The strict-refusal mechanism did not convert cleaner context into a general
win. Exact `"No information available"` replies were 61 for fixed, 61 at
`0.90`, 71 at `0.925`, and 62 at `0.95`.

## Wiring and receipt gates

- Required BEFORE smoke: all four arm files plus summary landed; 256 receipts,
  zero fallbacks.
- Required AFTER smoke: all four arm files plus summary landed; adaptive-depth
  decisions were present in retrieval telemetry; 257 receipts, zero fallbacks.
- Full measurement: ingest 44 receipts; fixed 540; `0.90` 537; `0.925` 537;
  `0.95` 533. All 2,191 measurement receipts had `fallback_from=null`.
- The partial eval schema had no proposals table, so no reviewerless proposal
  could queue.

One initial BEFORE command was terminated by the command-runner time limit
after ingest and before result files. Its isolated artifact directory remains
append-only. The successful smoke was rerun under a persistent user unit.

Two unusually long CLI children self-resolved just before targeted termination;
both `kill` attempts returned “No such process.” No evaluator child was
manually terminated, no result was spliced, and all completed arms have their
full receipt sets.

## Limitations

- One 199-question conversation is paired and controlled, but model generation
  remains nondeterministic. The 0.90 and 0.925 paired deltas are not individually
  significant; the monotonic overall degradation and the significant 0.95 loss
  support the policy verdict.
- The cut used the final combined ranking score because that is the full score
  distribution Step 01 persisted. A raw-cosine policy would be a different
  experiment, not a reinterpretation of this one.
- The 25-question BEFORE/AFTER scores are wiring noise and were not used for
  the verdict.

## Product disposition

Remove the experimental runtime policy and keep fixed `top_k=10`. Preserve the
analyzer, this verdict, result artifacts, receipt logs, and frozen database
archive. Step 06 may address the actual breadth failure through cross-session
assembly; Step 05 should not be retuned until it flatters.
