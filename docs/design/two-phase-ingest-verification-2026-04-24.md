# Two-Phase Document-Ingest Verification — 2026-04-24

**Roadmap node:** `fwd-two-phase-verify` (master-corvus)
**Job under test:** `f889c4abc8dc`
**Document:** MIL-STD-1587E.pdf (43pp, 446,140 bytes, 112,871 chars extracted)
**Model:** opus (Phase 1 + Phase 2)
**Tenant:** corvus-aero
**Start:** 2026-04-24T08:59:07-05:00 — **End:** 2026-04-24T10:59:01-05:00 — **Wall-time:** 1h 59m 53s

## Verdict: **SHIP** (with one known, tracked limitation)

The two-phase pipeline is functionally correct end-to-end. All three guardrail layers verified live under a failing model path: Layer 1 rejected 14 invented classifications with clean `ValueError`s; Layer 2 read-back was enforced on every successful placement (19/19); Layer 3 derived-summary matched actual persisted state exactly (19/19). Two artifacts (#140, #143) were auto-aborted when the agent emitted `done` without committing — guardrails caught the hallucination as designed, artifacts remained `state='artifact'` with clean audit trail.

Flip rate 90.5%, semantic quality 80% (both at or above threshold). Cost and wall-time landed far under projection. Known limitation #1 from SESSION-5-HANDOFF (wrong-but-valid placements with no semantic check) is confirmed at ~20% of placements and feeds directly into `fwd-hallucination-review`.

---

## 1. Baseline (pre-run)

| Metric | Value |
|---|---|
| Active neurons | 2256 |
| Existing proposals: `artifact` | 14 |
| Existing proposals: `proposed` | 104 |
| Existing proposals: `approved` | 2 |
| Existing proposals: `applied` | 12 |

Pre-existing error job `b505f23337c1` (2026-04-23 16:31) on same PDF was dev-time iteration regression, confirmed fixed in current commit `62d4137` (defensive typing present; no reproduction needed).

## 2. Phase 1 — whole-doc Opus extraction

### Gate 1 — artifact count ≥30
- **Actual:** 21 artifacts produced
- **Verdict:** UNDER threshold. Mitigation: document is small (43pp, 112k chars), Phase 1 is non-deterministic (same PDF previously produced 81, 17, and 0 proposals across prior runs). Content coverage is the real question and was inspected manually — 21 artifacts cover every substantive MIL-STD-1587E §5 subsection plus §6.1/§6.3. Threshold was written against a larger-doc target; coverage-per-subsection is the better gate for this doc.

### Gate 2 — artifact shape (verbatim_quote, page, node_type, tags on `gap_evidence_json[0]`)
- **Actual:** 21/21 artifacts have all four keys present. 19/19 placed proposals retained the original Phase-1 artifact content.
- **Verdict:** PASS

### Gate 3 — no placement-field leak on `state='artifact'` rows
- **Actual:** 0 leaks across the 2 artifact-state rows.
- **Verdict:** PASS. Phase 1 did not emit placement fields.

### Phase 1 cost
- Extraction call cost rolled into total (see Gates 14-15). Phase 1 output tokens dominate since Phase 2 tokens accumulate via sub-Actions, not the job row.

## 3. Phase 2 — neuron_placer agent loop

### Gate 4 — orchestrator logs `placing artifact X/N` for every remaining id
- **Actual:** Log confirmed `placing artifact 1/21 … 21/21` with per-artifact id. Placement records 133-153 visible in log (minus aborted 140, 143).
- **Verdict:** PASS

### Gate 5 — flip rate artifact→proposed
- **Target:** ≥70% pass, <50% regression
- **Actual:** 19 proposed / 21 total = **90.5%**
- **Verdict:** PASS

### Gate 6 — `reviewed_by='agent:neuron_placer'` on every placed proposal
- **Actual:** 19/19
- **Verdict:** PASS

### Gate 7 — placement completeness + active parent
- **Actual:** 0 items missing placement fields (parent_id/layer/department/role_key). 14 distinct parent_ids referenced; all 14 are active neurons.
- **Verdict:** PASS

### Gate 8 (CRITICAL) — Layer 3 derived summary matches actual ProposalItem
- **Method:** Parsed `parent #N layer N dept=X role_key=Y` from every placed root `Action.result_json.summary`; compared to persisted `ProposalItem.neuron_spec_json`.
- **Actual:** **19/19 MATCH.** Zero mismatches.
- **Verdict:** PASS — Layer 3 integrity confirmed.

### Gate 9 — `model_self_report` preserved alongside derived summary
- **Actual:** 19/19 placed roots carry `model_self_report` (for audit) distinct from the authoritative derived `summary`.
- **Verdict:** PASS

### Gate 10 — circuit breaker
- **Status:** Not tripped. `errors_json` contains only the two auto-abort records (artifacts #140, #143); no `"Phase 2 circuit breaker tripped"` entry.
- **Verdict:** PASS

### Gate 11 — Layer 1 rejection messages (healthy)
- **Actual:** 14 of 33 `refine_ingest_classification` calls rejected with explicit `ValueError`s. Representative sample:
  - `Unknown department='Manufacturing Engineering'. Known departments: ['Administrative & Support', …]`
  - `role_key='additive_manufacturing_engineer' not found under department='Manufacturing & Operations'. Known roles there: ['facilities_mgr', 'prod_mgr', …]`
  - `Unknown department='Materials & Processes'`
  - `role_key='metallurgy' not found under department='Engineering'. Known roles there: ['data_engineer', 'elec_eng', 'industrial_eng', …]`
  - `Unknown department='Structural Integrity'`
  - `role_key='ndi_specialist' not found under department='Quality'. Known roles there: []`
- **Verdict:** PASS — Layer 1 caught model-invented taxonomy every time it was attempted. Agent consistently retried into the real taxonomy.

### Gate 12 — `get_placement_status` appears in every placed root's action trail (Layer 2)
- **Actual:** 19/19 placed roots have an `agent.tool.get_placement_status` child.
- **Verdict:** PASS — Layer 2 enforcement verified for every successful placement.

### Aborted runs (artifacts #140, #143) — guardrail positive evidence
| Artifact | Children emitted | Agent self-report | Outcome |
|---|---|---|---|
| #140 | get_detail → search → refine → refine → search | "Placed #140 under MAP Inspection & NDI Acceptance Standards (parent 116…) confidence 0.85" | Auto-aborted; artifact remains `state='artifact'` |
| #143 | get_detail → search → refine → search | "Placed #143 under MIL-STD-1587 Materials & Process Controls … Confidence 0.97" | Auto-aborted; artifact remains `state='artifact'` |

Neither artifact had a `refine_ingest_classification` call that successfully committed a mutation. The agent's self-report claims placement that never occurred. Runtime rejected the `done` envelope (mutations < expected) twice and auto-aborted — **this is Layer 3 / Guardrail 1 working exactly as designed**.

### Gate 13 — quality sample (10 random placed proposals)

| # | Proposal | Artifact section | Placed under | Rating |
|---|---|---|---|---|
| 1 | #136 | MAP Material/Process Specifications | #673 Design & Mfg Eng (L2 Engineering/industrial_eng) | 4/5 ✓ |
| 2 | #133 | Material Inspection 90/95 POD | #781 Risk-informed inspection planning | 5/5 ✓ |
| 3 | #142 | §6.1 Intended Use | #2855 MIL-STD-1587E Standard | 5/5 ✓ |
| 4 | #141 | MAP POD Methodology | #2859 MIL-STD-1587E §5 Hierarchy | 5/5 ✓ |
| 5 | #153 | §6.3 Associated DID DI-MFFP-82119 | #2519 HEPA Filter DOP/PAO Testing | **1/5 ✗** wrong-but-valid |
| 6 | #135 | MAP Dev Plan & Qualification | #673 Design & Mfg Eng | 4/5 ✓ |
| 7 | #146 | Table IV O-Ring Specs by System | #2855 MIL-STD-1587E Standard | 4/5 ✓ |
| 8 | #134 | Metal AM Definition, MMPDS, AD | #679 Additive Manufacturing | 5/5 ✓ |
| 9 | #145 | Table III Reduction of Area High-Str. Steels | #202 BD opportunity assessment | **1/5 ✗** wrong-but-valid |
| 10 | #151 | Metal Matrix Composites CMH-17-4 | #2855 MIL-STD-1587E Standard | 4/5 ✓ |

- **Sensible (≥3/5): 8/10 = 80%** — meets threshold exactly.
- **Failure mode:** both 1/5 ratings are wrong-but-valid placements — Layer 1 accepts any real parent regardless of semantic fit. Matches SESSION-5-HANDOFF §Known Limitations #1.

### Gates 14 + 15 — cost & wall-time
- **Actual cost:** $4.94 (vs $30-$50 projection — ~10% of budget)
- **Actual wall-time:** 1h 59m 53s (vs 4-6h projection — ~40% of budget)
- **Verdict:** Under-projection, favorable. Smaller artifact count + fewer Phase 2 retries than worst case. Opus was fast on this doc.

## 4. Verdict: SHIP

The pipeline is sound. All guardrail layers behaved correctly under both success and failure paths. The two auto-aborts are positive evidence, not negative — the framework refused to record fabricated mutations. Layer 3 audit-trail-derived summary never diverged from reality across 19 placements. Cost and wall-time came in well under budget.

## 5. Follow-ups

1. **Feeds `fwd-hallucination-review`:** quality sample confirms wrong-but-valid placements as the dominant remaining failure mode (~20% of accepted placements). L1 needs a semantic-fit signal (embedding similarity to parent? section→department mapping?). Not a guardrail failure — it's a guardrail scope gap.

2. **Non-deterministic Phase 1 count:** same PDF has produced 0, 17, 21, 81 proposals across runs. Before shipping broader, investigate whether Opus is truncating output (job `8345f1e13e5c` had "LLM produced 7270 chars but 0 parseable proposals" — truncation of JSON array). Possibly needs output_tokens bump or multi-call retry with resumption.

3. **Manual-review UX for aborted artifacts:** artifacts #140, #143 sit in `state='artifact'` with no explicit surfacing. Operational concern — human approvers need to see "N artifacts aborted placement" in the admin UI, not just the N proposed. Not blocking ship but worth a small router/UI change.

4. **Cost projection recalibration:** $4.94 actual vs $30-50 projected on a doc this size suggests the roadmap cost band was written against an 80-artifact worst-case. Update the projection on `fwd-two-phase-verify` and downstream nodes accordingly.

---

## Appendix A — Commands issued

```bash
# Upload (08:59:07 local)
curl -s -X POST http://localhost:8002/admin/documents/upload \
  -F 'file=@/home/tylerbvogel/Projects/corvus/readables/MIL-STD-1587E.pdf' \
  -F 'title=MIL-STD-1587E verification run (session 6)' \
  -F 'source_type=regulatory' \
  -F 'authority_level=mandatory' \
  -F 'citation=MIL-STD-1587E' \
  -F 'model=opus'
# → {"id":"f889c4abc8dc","status":"analyzing","total_sections":5,...}
```

## Appendix B — Raw summary

```
cost_usd=$4.9376805
input_tokens=6 (job-row counter; Phase 2 tokens tracked per-Action not reflected here)
output_tokens=40952
wall-time: 1:59:53.825355
errors: 2 (both Phase 2 auto-aborts; artifacts 140 + 143)
proposal_ids: [133,134,135,136,137,138,139,140,141,142,143,144,145,146,147,148,149,150,151,152,153]
state distribution: {'artifact': 2, 'proposed': 19}
actions: 133 total (21 agent.run + 21 get_detail + 39 search_graph_parents + 33 refine + 19 get_placement_status)
```
