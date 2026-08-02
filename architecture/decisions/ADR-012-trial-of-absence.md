# ADR-012: Demote the attribution proxy to triage; trials of absence decide

- Status: Accepted
- Recorded: 2026-08-02

## Context

Delivery plasticity (mind-delivery-plasticity) wired the distiller's
attribution verdicts to an actuator: pathways whose deliveries earned no
`load_bearing` verdicts were attenuated, and eventually proposed for
retirement, on the strength of those verdicts alone. The Goodhart review
(mind-recurrence-watch) found three structural defects:

1. **Survivor-bias ratchet** — culling unrewarded deliveries raises the
   channel's future measured base rate, which lowers the condemnation
   threshold, which eases the next cull. Self-tightening.
2. **Evidence-rate asymmetry** — attenuated pathways gather exculpatory
   evidence at probe speed (1-in-k) while condemnation statistics
   accumulate at full speed.
3. **Proxy-internal audit** — "zero false kills" was checked against
   rewards, which *are* distiller verdicts: the proxy graded itself.
   Prevention-style lessons are structurally invisible to transcript
   judgment — a prevented mistake leaves no trace.

## Decision

Goodhart's law is fatal when a proxy decides outcomes and harmless when it
merely allocates attention. So the binomial tail (the proxy) only
**nominates** trial candidates. Attenuation is reinterpreted as a
**controlled trial of absence** — withhold from most sessions, probe the
rest, log everything — whose endpoints are both exogenous to the verdict
loop:

- a **probe reward** restores the pathway (dishabituation, already shipped);
- a **verified recurrence** of the withheld lesson's documented failure
  restores the pathway *and* books a first-class verified-false-kill
  incident receipt. Recurrence detection reuses the deeds-corroborated
  trust pattern: the distiller nominates, and a deterministic gate admits
  only citations that token-match both the actual episode events and the
  lesson's own failure text.

Consequent re-groundings:

- **Zero-false-kills** is computed from recurrence events, never from
  reward absence.
- **Tier-2 retirement proposals** must carry the trial as a causal claim
  (withheld count and window, probe outcomes, recurrence count zero) and
  are refused otherwise; the human countersigns an experiment's result,
  not the distiller's opinion.
- The **base-rate drift monitor** contemplated in the Goodhart review is
  deliberately **not built**: with the proxy demoted to triage, a
  tightening threshold merely starts more cheap, reversible trials.

## Consequences

- A biased nomination now wastes a cheap, reversible trial instead of
  farming cobras; the ratchet becomes benign.
- Prevention value is measurable only through withholding, so withheld
  logging (the `withheld` sibling list on Injection records) is
  load-bearing infrastructure, not telemetry garnish.
- Trials lengthen the path from attenuation to retirement; nothing
  retires without observed withholding, and a recurrence anywhere in the
  pipeline (pending proposal included) supersedes it.
- The recurrence judge can miss (rephrased failures, unlogged surfaces):
  recurrence coverage is necessary evidence for retirement, never
  sufficient — the human countersign stays. A miss is the safe direction:
  no harm detected, trial continues, floor intact.

## Alternatives considered

- Keep verdict-driven retirement and bolt on a base-rate drift monitor
  (rejected: monitors the ratchet instead of removing its authority).
- Give up on habituation and leave delivery-yield improvement human-driven
  (rejected: reinstates perception-without-reflex).
- Judge recurrences with a second model instead of a deterministic token
  gate (rejected: replaces one unguarded judge with another).
