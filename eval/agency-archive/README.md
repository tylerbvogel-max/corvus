# Agency Lab archival certificate

Agency Lab and Mission Control were retired on 2026-07-26 after the roadmap
ledger absorbed durable intent, prerequisites, verification, outcomes, and
human acceptance. Coding-agent harnesses already own worker selection,
subagents, permissions, and execution.

This archive preserves the useful trust findings without retaining an agent
economy in the product:

- lock claims before verification;
- separate fact, inference, evidence, limitations, and disclosures;
- require a verifier independent from the accepting human;
- never let execution silently close durable roadmap intent;
- propagate failed upstream evidence to dependent conclusions.

The retired live database contained only synthetic/calibration data:

| Table | Rows |
|---|---:|
| policies | 2 |
| worker profiles | 7 |
| experiments | 3 |
| score events | 16 |
| capital transactions | 16 |
| venture plans / revisions | 5 / 5 |
| work orders / events | 10 / 16 |
| permission leases | 10 |

No work order referenced a roadmap ledger. All five ventures were draft
`live-intake-*` or `haiku-intake-*` calibration fixtures. The implementation
remains recoverable from git commit `186df8e`.

The surviving contract is `corvus.roadmap-reconciliation/v1`, stored directly
on a roadmap record. It keeps claims, confidence, limitations, disclosures,
evidence, verifier identity, human acceptance, and next action—without modeling
workers, wages, capital, leases, or dispatch.
