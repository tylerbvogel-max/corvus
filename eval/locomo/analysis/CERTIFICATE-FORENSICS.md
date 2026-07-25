# LoCoMo Certificate Forensics

Analysis of the 10-conversation certificate completed 2026-07-21.
Memory arm **65.36** over 1,986 questions (nospread 65.86, embed-only 65.41,
full-context baseline 72.00).

**Scope:** analysis only. No harness changes, no reruns, no production changes.
All evidence files were read append-only; every number below is reproducible from
the scripts in this directory.

**Reproduce:** `python3 coverage.py`, `python3 conv9_presence.py` (read-only
`corvus_locomo`), plus the inline slices quoted per finding. `lib.py` loads and
alignment-checks all four arms × ten conversations.

**Publication note:** this file contains aggregate statistics only, with one
exception — Finding 6 quotes two Corvus-generated fact labels as mechanism
evidence. No LoCoMo question text or gold answers appear here. `eval/locomo/analysis/`
is **not** currently covered by `eval/locomo/.gitignore` (which ignores
`locomo10.json` and `results/`); decide whether to commit this file before pushing
to the public remote.

**Validity check first:** the certificate is not a judging artifact. Of the memory
arm's 330 substantive (non-refusal) non-adversarial misses, 17 (5.2%) contain every
content token of the gold answer; the equivalent figure for the baseline arm is 22
of 238 (9.2%). Judge strictness penalises the *baseline* harder, so the 6.64pp gap
is, if anything, conservative. Judge-noise ceiling on the memory arm: **0.86pp**.

---

## Ranked findings

| # | Finding | Expected value | Effort |
|---|---------|----------------|--------|
| 1 | Uncalibrated abstention is the entire gap to full context | **+4 to +8pp** | Medium |
| 2 | `top_k=10` saturates on 100% of queries — no confidence signal exists | enabler for #1 | Low |
| 3 | "Hard conversations" are refusal outliers, not temporal/reasoning outliers | diagnostic | Low |
| 4 | Cross-session composition is the residual accuracy frontier | +1.5 to +2pp | High |
| 5 | Lexical + entity lanes are net-zero churn (candidate 1 **demoted**) | ~0pp | — |
| 6 | Maintenance tax is indistinguishable from re-run churn (candidate 2 **demoted**) | ~0pp, 1 trust bug | Low |

---

## Finding 1 — Over-refusal is larger than the entire gap to full context

**Claim.** The memory arm's deficit against full-context is not a retrieval or
reasoning deficit. It is a policy deficit: the answerer emits "No information
available" on non-adversarial questions at more than twice the baseline rate, and
that excess alone is worth more points than the whole gap.

**Numbers.**

| arm | non-adv n | acc | refusal rate | wrongful refusals |
|---|---|---|---|---|
| memory | 1540 | 59.48 | **19.29%** | **294** |
| nospread | 1540 | 60.06 | 18.70% | 287 |
| embed-only | 1540 | 58.83 | 20.19% | 310 |
| baseline | 1540 | 75.71 | **8.96%** | **136** |

- Excess wrongful refusals, memory vs baseline: **158 questions = 7.96pp of suite**.
- Total suite gap to baseline: **6.64pp = 132 questions**. The over-refusal pool is
  *larger than the gap it needs to close*.
- Of the 294 wrongful refusals, baseline answers **166 (56.5%)** correctly and also
  refuses 90 (30.6%). 166 questions = **8.36pp** is the honest recoverable pool.
- When memory does answer, it is decent: 73.45% on answered non-adversarial
  questions vs baseline's 83.02%. Two roughly equal loss pools — ~166 from
  refusing, ~119 from answering badly.

**The refusals are not informed by retrieval difficulty.** If abstention tracked
genuine evidence gaps it would rise with question hardness. It is flat:

| evidence spread | n | mem acc | **refusal %** | baseline acc |
|---|---|---|---|---|
| 1 evidence session | 1207 | 65.45 | **19.06** | 80.45 |
| 2+ evidence sessions | 329 | 38.30 | **19.15** | 58.66 |

Accuracy collapses 27pp across that split while the refusal rate moves 0.09pp.
Same story against distillation yield of the evidence sessions (min facts/turn
buckets `<0.45 / <0.60 / <0.80 / >=0.80` → refusal 20.2 / 21.5 / 16.5 / 22.9%) —
no monotone relationship. The system is refusing at a roughly constant ~19% rate
independent of whether it actually had the evidence.

**Direct proof that the facts were present (conv 9, live graph).** `corvus_locomo`
retains conv 9's final state: 354 lesson neurons, 341 active. Measuring how much of
each gold answer's content vocabulary exists anywhere in the active corpus:

- **75.8% of conv-9 refusals (25 of 33) are on questions whose gold vocabulary is
  fully present in the stored corpus.** Baseline answers 17 of those 25 correctly.
- Null control (required, since token presence could be vacuous): real conv-9 golds
  score mean coverage **0.910** / 77.8% full against conv 9's corpus; golds from
  conversations 0–8 score **0.424** / 20.2%. Dropping the 100 most frequent corpus
  tokens: 0.886 vs 0.361. The metric discriminates.

**Single-variable confirmation (write-gate starvation, conv 0).** The Phase A r1
runs starved the write gate, halving the corpus. Same conversation, same code,
same questions:

| run | corpus | non-adv refusal | overall | adversarial |
|---|---|---|---|---|
| raw r1 | 117 neurons | **36.8%** | 52.76 | 89.4 |
| raw r2 | 261 neurons | **13.2%** | 70.85 | 78.7 |

−23.6pp refusal ↔ +18.1pp overall ↔ −10.7pp adversarial, 48 questions gained
against 12 lost. Refusal rate *is* the score, and the adversarial guardrail moves
inversely on the same dial.

**Engineering implication.** Make abstention a function of retrieval evidence
rather than a flat prompt policy: gate the refusal on the retrieved set's score
distribution (top-1 margin, score mass above threshold, gold-entity coverage of the
question's entities) instead of asking the answerer to self-assess. The naive
undiscriminating dial — simply answering more, i.e. moving to baseline's refusal
posture — is worth +166 non-adversarial and −118 adversarial = **+48 questions
(+2.4pp)**. A calibrated gate should beat that line materially, because the current
refusals carry *no* correlation with evidence quality (the flat 19.06/19.15 split
above), which is exactly the headroom a calibrated policy exploits. Expected
**+4 to +8pp** depending on how much of the 166 is separable.

**Testable hypothesis.** On the ~19% of non-adversarial questions currently
refused, retrieval score distributions will be statistically indistinguishable from
those on answered questions. If true, the answerer is refusing on prompt disposition
alone and any evidence-based gate is an improvement. This is checkable offline the
moment #2 lands.

---

## Finding 2 — `n_hits` is constant 10; there is no confidence signal to calibrate on

**Claim.** Retrieval returns exactly `top_k=10` on **1,986 of 1,986** questions —
every category, every arm, correct and incorrect alike (mean n_hits = 10.00 in all
ten category×outcome cells).

**Consequence.** Three things follow. (a) The pre-filed question "did n_hits
correlate with adversarial over-assertion?" is unanswerable *by construction* — the
variable has zero variance. (b) There is no adaptive-k behaviour: a question with
one strong hit and a question with ten weak ones are delivered identically.
(c) Finding 1's calibrated gate has nothing to read today.

**Engineering implication.** Emit per-query retrieval telemetry — score vector,
top-1 margin, count above threshold — into the eval record and the `queries`
stage-telemetry path. Low effort, and it is the prerequisite for #1 rather than a
scoring change in itself.

---

## Finding 3 — Hard conversations are refusal outliers, not reasoning outliers

**Claim.** Conversation difficulty is explained almost entirely by refusal rate,
and the specific "conv 3 has a temporal-anchoring weakness" hypothesis is wrong.

**Numbers.** Pearson **r = −0.882** between non-adversarial refusal rate and
conversation score across the ten conversations.

| conv | mem | baseline | gap | non-adv refusal % |
|---|---|---|---|---|
| 3 | 53.85 | 68.46 | −14.61 | **27.6** |
| 4 | 59.92 | 70.25 | −10.33 | **26.4** |
| 9 | 62.75 | 73.53 | −10.78 | 20.9 |
| 2 | 70.98 | 74.09 | −3.11 | **13.8** |
| 5 | 71.52 | 74.05 | −2.53 | **14.6** |
| 6 | 72.11 | 70.00 | **+2.11** | 18.7 |

The two worst conversations are the two heaviest refusers. Conv 6 is the only
conversation that beats full context.

**Conv 3's temporal score (40.0) is a refusal artifact.** Its temporal refusal rate
is **47.5%**, and **19 of its 24 temporal misses are refusals**, not wrong dates.
Suite-wide, date-valued answers are memory's *strongest* answer type and the only
one where it matches full context:

| gold answer type | n | memory | baseline | mem refusal % |
|---|---|---|---|---|
| **date** | 303 | **69.0** | **68.7** | 13.9 |
| number | 13 | 69.2 | 84.6 | 7.7 |
| short phrase | 597 | 62.1 | 80.1 | 22.3 |
| long phrase | 627 | 52.1 | 74.8 | 19.3 |

Temporal *reasoning* is not the weakness. Do not spend effort on date arithmetic.

**Ruled out as the driver:** distillation compression. Conv 3 distils at 0.576
facts/turn and refuses 27.6%; conv 5 distils *lower* at 0.533 and refuses 14.6%
while scoring 71.52. Across all ten conversations, facts/turn spans a narrow
0.533–0.665 and does not track refusal.

**Engineering implication.** Treat per-conversation non-adversarial refusal rate as
the primary eval canary — it predicts the score at r = −0.88 and is computable
without a judge. Diagnostic value; folds into #1's fix.

---

## Finding 4 — Cross-session composition is the residual frontier, and misses are under-assembly

**Claim.** After abstention, the remaining real deficit is composing facts that
live in different sessions. The pre-filed under-assembly claim is corroborated
with counts.

**Numbers** (non-adversarial, joined to the dataset's evidence pointers):

| evidence turns required | n | memory | baseline |
|---|---|---|---|
| 1 | 1127 | 66.10 | 81.46 |
| 2 | 225 | 44.89 | 63.56 |
| 3 | 83 | 44.58 | 56.63 |
| 4+ | 101 | **32.67** | 55.45 |

Single-session evidence 65.45 vs cross-session 38.30 — a 27pp cliff. Full context
shows the same shape (80.45 → 58.66), so roughly half is task-intrinsic; memory's
*excess* deficit over baseline widens from 15.0pp to 20.4pp.

**Under-assembly, quantified (conv 9, live corpus).** Of conv 9's 17 wrong
multi-hop answers, **11 (64.7%) have the gold answer's full vocabulary present in
the stored corpus** — and of those 11, **9 are substantive wrong answers against
only 2 refusals**. The system holds the components and fails to compose them. The
same split across all conv-9 non-adversarial substantive errors: 25 of 37 (67.6%)
had the facts in corpus.

**Engineering implication.** Query decomposition / second-hop retrieval for
questions whose first-pass hits are entity-adjacent but don't jointly cover the
question's entities. Pool is 329 cross-session questions; closing half the
memory-vs-baseline gap there is ~34 questions = **+1.7pp**. High effort, and it
should be sequenced *after* #1 — 15.8–25.3% of these questions are currently being
refused rather than attempted, so the assembly work would be measured through a
noisy abstention layer.

---

## Finding 5 — DEMOTED: lexical near-dup over-fire is not costing points

**Pre-filed candidate 1 is not supported.**

**Dedup write-gate volume correlates *positively* with score.** Pearson
**r = +0.289** between duplicate-skip rate and conversation score (n = 9; conv 6's
summary lost its `lifecycle` block when the 20260720T212845Z run aborted). The
extremes run the wrong way for the hypothesis: conv 5 skipped **19.8%** of
candidate facts and scored 71.52 (2nd best); conv 4 skipped **4.8%** and scored
59.92 (2nd worst).

**The lexical + entity lanes are net-zero.** Memory (lanes on) vs embed-only
(lanes off) flip 207 questions — 10.4% of the suite — for a net of **−1 question
(−0.05pp)**, McNemar χ² = 0.0.

| category | lanes help | lanes hurt | net |
|---|---|---|---|
| multi-hop | 37 | 27 | **+10** |
| single-hop | 40 | 36 | +4 |
| temporal | 13 | 16 | −3 |
| open-domain | 5 | 6 | −1 |
| adversarial | 8 | 19 | **−11** |

The only directional signal is adversarial (χ² = 3.7, p ≈ 0.054): the extra lanes
supply off-target hits that push the answerer into over-assertion. That is a real
but small effect, and it buys back nearly the same number of multi-hop points.

**Related ablation result:** memory vs nospread is 39/49 discordant, net −10,
χ² = 0.92 (not significant). Spread activation is not earning its keep on LoCoMo,
but neither is it hurting. All three memory arms agree on 87.26% of questions;
560 questions are missed by all three, and **283 of those are answered correctly by
full context** — that 14.25pp is the true recoverable ceiling, of which Findings 1
and 4 are the two components.

**Engineering implication.** Do not tune the lanes for points. The only defensible
micro-change is suppressing the lexical lane for refusal-eligible queries, worth
~11 adversarial questions at the cost of ~10 multi-hop. Not worth doing.

---

## Finding 6 — DEMOTED: the maintenance tax is re-run churn; one real trust bug found

**Pre-filed candidate 2 (janitor fusion date loss): mechanism confirmed, magnitude
negligible.**

**The tax is inside the noise floor.** Conv 0, raw-r2 → full-lifecycle: 14 lost,
10 gained, **net −4 questions (−2.0pp)**. But two adjacent re-runs churn just as
hard on their own — raw-r2 → consolidation-r2 is 21 lost / 18 gained (net −3), and
consolidation-r2 → full-lifecycle is 20 lost / 19 gained (net −1). Roughly 20
questions flip in each direction per re-run from distillation nondeterminism alone.
A −4 net cannot be separated from that.

**The date-melting mechanism is real but rare.** Across conv 0, 3 and 9 there are
32 `consolidation.fuse` actions. Exactly **one** dropped a date from the surviving
label — canonical `Dave restores vintage cars` absorbing `Dave restored a classic
car in 2022` (3.1% of fuses; and label-level only, content may retain it).
Extrapolated over the suite's 102 fuses that is ~3 affected facts out of 3,581.
Not a scoring factor.

**Supersession damage is also negligible.** Conv 9's 13 superseded neurons leave
only 13 tokens reachable exclusively through inactive nodes; exactly **1 of conv 9's
70 non-adversarial misses** needs one.

**However — a genuine trust bug.** All **13 of 13** conv-9 supersessions fired on
non-perishable identity facts: `Calvin is a musician`, `Dave joined a rock band`,
`Calvin interested in Japanese culture`. Staleness is treating durable attributes as
perishable state. It costs ~0 points on LoCoMo because the vocabulary survives
elsewhere, but on a real tenant it silently deactivates true standing facts. This is
a memory-integrity issue, not a benchmark issue, and it should be filed on those
grounds.

**Weak residual signal worth noting, not acting on.** Conv 0's category trajectory
declines monotonically as lifecycle machinery engages — temporal 78.4 → 75.7 → 70.3
and multi-hop 62.5 → 59.4 → 56.2, while adversarial *rises* 78.7 → 83.0 → 83.0.
That is the specificity-loss signature (fused labels get more generic → answers get
vaguer and refusals rise → adversarial improves). But n = 47 temporal / 32 multi-hop
in a single conversation, and only conv 0 has a raw arm, so it is suggestive at
best. It is also the same dial as Finding 1, which is where the effort belongs.

---

## What I would do next, in order

1. **Emit retrieval telemetry** (Finding 2). Cheap, unblocks everything.
2. **Test the flat-refusal hypothesis offline** — compare score distributions on
   refused vs answered non-adversarial questions. One rerun's telemetry, no code risk.
3. **Replace prompt-disposition abstention with an evidence-gated one** (Finding 1).
   This is the +4–8pp move and the only change that touches the 6.64pp gap.
4. **File the identity-fact supersession bug** (Finding 6) on trust grounds.
5. **Cross-session assembly** (Finding 4) — real, but sequence it after #3 so it is
   measured through a calibrated abstention layer rather than a noisy one.

Do not spend effort on: temporal/date arithmetic (Finding 3), retrieval lane tuning
(Finding 5), janitor fusion conservatism for score reasons (Finding 6).
