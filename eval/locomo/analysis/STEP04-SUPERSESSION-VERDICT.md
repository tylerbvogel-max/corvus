# Step 04 — Identity-fact supersession: verdict

**Session c31541a4 → this session (d41a8f69), 2026-07-26. Roadmap record:
`mind-identity-fact-supersession` (corvus-long-horizon, admitted @ rev 16).**

The fix shipped. But the node's headline evidence was misattributed, and the
real bug is uglier than filed — three defects, not one. Receipts below.

## 1. The node's premise, corrected

The record claims conv 9's final graph held "13 inactive, all 13 via
`staleness.superseded`", quoting 'Calvin is a musician', 'Dave joined a rock
band', 'Calvin interested in Japanese culture', 'Calvin lives in Japanese
mansion', 'Dave wants to visit Tokyo'.

**All five quoted labels are `consolidation.fuse` absorptions, not staleness
supersessions.** conv 9's artifact log holds exactly 13 fuse actions and 10
staleness actions; staleness never touches `is_active` (only fuse's
`_absorb` flips it), so the "13 inactive" ARE the 13 fuses — near-duplicate
absorptions in which each durable fact survives in its canonical ('Calvin is
a musician' → 'Calvin pursues music', 'Dave joined a rock band' → 'Dave is
in a band'). Mostly correct behaviour, wrong exhibit. The forensics session
read `is_active=false ∧ superseded_by set` off the live graph and attributed
the whole set to the staleness lane.

The bug is real anyway — the receipts just live elsewhere.

## 2. What the staleness lane actually does (code, 068bcf2)

`run_staleness` → `scan_contradictions` (embedding "contradiction zone"
prefilter → Haiku batch classify: consistent / contradictory / ambiguous;
both non-consistent verdicts become findings) → `_resolve_contradiction`:
same-scope lesson pair ⇒ **older `created_at` loses, unconditionally** —
`superseded_by` set, utility halved. The resolver never read the
classification it was handed. Three defects follow:

1. **Ambiguous ⇒ superseded.** Live receipt in the standing corpus
   (`corvus_locomo`, ebcad162): finding 1, severity `info`, classification
   `ambiguous` — 'Melanie shared wedding dress photo' ↔ 'Melanie's wedding
   in a greenhouse', two compatible durable facts — resolved `superseded`,
   casualty demoted to 0.25.
2. **Recency arbitrates everything**, including pairs where both claims are
   standing ('screenplay is her first' vs 'writing second screenplay' —
   recency picks an extraction, not a truth).
3. **Repeat-fire.** Supersede-with-history keeps the loser active, so every
   janitor pass re-flags the same live pair, mints a fresh finding, and
   re-halves the same neuron's utility: observed 0.5 → 0.25 → 0.125 in the
   certificate runs. 13 of the 55 logged actions are repeat-fires.

## 3. The 55 historical supersessions, classified

Sources: per-pass janitor reports archived in the certificate summary JSONs
(`results/conv*-summary-strict-full-lifecycle.json`) + artifact-dir
`janitor-actions.jsonl`. The per-run eval databases are gone, so labels were
recovered from those append-only artifacts: 42 unique pairs (55 actions − 13
repeat-fires), 29 with both labels, 31 with the casualty's label. Full
per-pair rationale: `step04_historical_classification.json` in the step04
forensics artifact dir (20260726T155942Z).

| pair relation (unique pairs) | n |
|---|---|
| compatible — no contradiction, casualty demoted anyway | 13 |
| state_update — defensible perishable-state supersession | 9 |
| near_duplicate — dedup lane's job, vocabulary survives | 6 |
| standing_conflict — real conflict, recency invalid arbiter | 1 |
| unknown — labels unrecoverable | 13 |

Casualty durability where knowable: **21 durable vs 10 perishable.** Of 29
fully-classifiable pairs, only 9 (31%) were the designed behaviour. Misfires
span conv 0–9: systemic, exactly as the node predicted — just not via the
conv-9 identity examples it quoted.

Note on the certificate count: the record's "55" includes 6 actions from an
aborted conv-6 run (20260720T152320); the certificate conv-6 arm
(20260720T212845) logged zero janitor events. Certificate-proper total: 49
actions / 37 unique pairs. Classification covers all 55 regardless.

## 4. The fix

The node suggested reusing FusionPlan validator machinery; in code that
machinery has no perishable/durable distinction to reuse. What it does have
is the principle — *unresolved conflicts abstain for human context*
(`decide_disposition`). The fix applies that principle inside the existing
lanes, no parallel heuristic:

- **Classifier** (`integrity/conflict_monitor.py`): the existing Haiku batch
  call now emits, per contradictory pair, `resolution_hint`:
  `state_update` (same perishable time-varying state; the later observation
  naturally replaces the earlier) vs `standing_conflict` (standing claims;
  recency cannot decide truth). Stored in `detail_json`. Garbage hints
  coerced to None.
- **Scan-side dedup** (`_drop_already_flagged`): candidate pairs that
  already carry a contradiction finding are dropped before classification —
  kills repeat-fire at the source and stops re-burning Haiku on settled
  pairs.
- **Resolver** (`mind_janitors._resolve_contradiction`): supersedes ONLY on
  `contradictory` + `state_update`. Ambiguous, standing conflicts, and
  legacy hint-less findings → `resolution='needs_review'`, status stays
  open for the integrity inbox (same pattern as lived-experience
  precedence), no mutation. A finding whose loser is already superseded
  resolves as `already_superseded` without re-demotion.

Fail-closed by construction: no hint ⇒ no automatic retirement.

## 5. Acceptance evidence

- **Planted-pair regression, both directions** —
  `backend/tests/test_staleness_supersession.py`, 7 tests green:
  perishable contradicted pair still superseded (utility 0.5 → 0.25, edge
  written, finding resolved); durable standing conflict survives untouched
  and lands in review; ambiguous never supersedes; legacy hint-less finding
  fails closed; repeat-fire does not re-demote; scan dedup is
  order-agnostic; classifier hint plumbing rejects garbage. Adjacent
  integrity/dedup/lint suites: 32 green.
- **Replay over the historical supersessions** — replayed via the real
  classify lane over the 29 both-label pairs (content died with the eval
  DBs; label-text fidelity, stated in the receipts): **0 of 29 would still
  supersede.** 26 come back `consistent` (they would not even become
  findings — the historical harm chain usually started with a classifier
  over-fire that the old resolver made fatal), 2 `ambiguous` and 1
  `contradictory/standing_conflict`, all three routed to review. Plus 13
  of 55 actions blocked structurally by the repeat-fire guard regardless
  of classification. Receipts: `step04_replay_receipts.json`.
- **Smokes** — BEFORE (`smoke-supersession-before`, artifact
  20260726T155821Z) and AFTER (`smoke-supersession-after`, artifact
  20260726T162005Z), conv 0, 4 sessions / 25 questions, full-lifecycle,
  `LOCOMO_CONCURRENCY=2`. Both green: harness completed, all four arm
  files + summary landed, 262/261 LLM receipts with 0 fallbacks, 0
  proposals queued (56/54 applied). Neither smoke fires the janitor — the
  200-per-week cadence first triggers at session 8 — so the BEFORE arm is
  uncontaminated by construction and the staleness lane was exercised by
  the live probes below instead. Smoke score deltas are noise at this
  sample size and are not evidence of anything.
- **Live janitor probes** (step04 forensics artifact dir,
  `live-janitor-probe/`) — stage 1: full `run_janitors` pass under the new
  code against the AFTER smoke corpus. Janitor visibly fires
  (consolidation proposal + staleness actions logged); zero supersessions;
  the organic sunset-painting/lake-sunrise pair — the exact pair the OLD
  code auto-superseded in the standing corpus (finding 2 there) — now
  routes to review as `ambiguous`. Stage 2 (`probe2-receipt.json`):
  planted pairs through the production scan → classify → resolve pipeline,
  isolated in their own department so the top-40 prefilter cut cannot drop
  them. Perishable plant (Osaka trip planned → cancelled): classified
  `contradictory/state_update`, superseded live, utility 0.5 → 0.25.
  Durable plant (nurse vs firefighter): `contradictory/standing_conflict`,
  untouched, parked in review — Haiku's own reasoning: "no temporal
  dimension … stable characteristic (primary profession)". PROBE2 GREEN
  on all four checks.

Latent coverage gap noted, not fixed here: the conflict scan classifies
only the top-40 most-similar contradiction-zone pairs per pass (similarity-
descending truncation in `extract_pairs_in_range`), so lower-similarity
contradictions may never be judged. Filed for Tyler's triage; separate
mechanism from this node.

## 6. What this does NOT change

No certificate re-run, no headline change: LoCoMo damage was already
measured negligible (13 tokens reachable only through inactive nodes, 1 of
conv 9's 70 non-adversarial misses). This node was filed on trust grounds —
on a real tenant the old lane silently demoted standing facts toward the
utility floor and marked them superseded-with-history. Carry-forwards to
Steps 05/06 are unaffected.
