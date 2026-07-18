# NVM Incident Fixture — mind-reconsolidation-kernel golden specimen

Frozen 2026-07-17T09:46:47-05:00 from live `corvus_mind`. Integrity: `sha256sum -c MANIFEST.sha256`.
Do NOT re-run `freeze_fixture.sh` — live traffic moves the numbers (the node prompt's
795/807/405 had already drifted to 809/321/419 by freeze time). The committed JSON is the fixture.

## The incident

A single fact — "source ~/.config/nvm/nvm.sh, nvm use 22.22.0, project convention on this
machine" — existed as 16 neurons across Environment/Projects scopes. Graph lint detected the
duplicates; consolidation absorbed them; the process exposed every failure this kernel must fix.

Core 6-member component: **#47, #51, #57 (canonical), #177, #1151, #1161**.
Older absorbed lineage: #71, #85, #109, #116, #126, #128, #156, #172, #209, #211.
Proposals: **#1067** (component fusion, approved-never-applied), **#1069/#1071** (scope lint,
approved-never-applied), **#1099** (user-directed consolidation, applied 2026-07-17 12:25 UTC).

## Frozen failure receipts (all verified live before freeze — see invariants.json)

1. **Approved ≠ applied**: #1067/#1069/#1071 `state='approved'`, `applied_at IS NULL` while
   recall kept serving originals. #1099 later did the same work under a different plan.
2. **Stat inheritance is wrong under every naive rule**: summed firing rows 809, max single
   member distinct queries 321, union distinct queries **419**. Sum double-counts co-delivery;
   max discards non-overlapping history. Union-distinct is the only correct invocation rule.
3. **Stale internal wiring**: 12 conducting activation edges (pyramidal/stellate) remain
   *inside* the component between canonical #57 and absorbed inactive members; only 5
   non-conducting evidence-links exist. Degree/centrality over these is fiction.
4. **Stale embedding**: #1099 rewrote #57's label+summary+content (neuron_refinements
   2026-07-17 07:25:31) but no embedding regeneration exists in any history table. #57's
   embedding hash (invariants.json) is the pre-refinement vector.
5. **Pair-verdict incoherence** (mind_pair_verdicts.json): #57/#177 and #51/#57
   duplicate-mis-scoped, #51/#177 complementary; #47/#211 duplicate-mis-scoped while
   #57/#211 genuinely-scoped. Pairwise verdicts do not compose into a component model.
6. **Winner-biased fusion**: canonical selected by max(invocations, utility, id); member
   stats/content not reconstructed; #57 kept its own invocations (318) — not the union (419).

## Files

- `invariants.json` — frozen headline assertions (member IDs, 809/321/419, edge counts,
  active set, unapplied proposals, per-neuron content+embedding SHA256).
- `neurons.json` — full rows incl. embeddings/entities for all 16 members.
- `neuron_edges.json` — every edge touching any member (135 rows, internal + external).
- `neuron_firings.json` — all firing rows for members (860 incl. lineage; core-6 = 809).
- `synaptic_learning_events.json`, `memory_change_log.json`, `neuron_refinements.json` —
  utility/attribution/change provenance for replay-based inheritance tests.
- `mind_pair_verdicts.json` — the 9 incoherent pair verdicts.
- `autopilot_proposals.json`, `proposal_items.json`, `actions.json` — proposal lifecycle state.
- `queries.json` — trimmed projection (id, first 300 chars, intent, ts) of the 427 distinct
  queries referenced by member firings; defines the exact frozen query-ID sets.

Contract tests in `backend/tests/test_reconsolidation_contract.py` load this fixture into a
throwaway database and assert the failure modes above until the kernel fixes them.
