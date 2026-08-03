# Runbook: distill backlog

## Symptom

Episode logs accumulate faster than the distiller consumes them. Memory stops
gaining new lessons while everything reports healthy — nothing errors, the
graph simply stops growing.

## Detection

- `GET /metrics/mind/slo` → `distill-backlog` breached
- `GET /distill/status` → `ready` count
- `GET /metrics/mind/jobs` → whether `distill` is failing or merely slow

## First response

1. Distinguish failing from slow. A failing distiller has an error receipt; a
   slow one has a healthy receipt and a growing backlog.
2. The usual cause is Opus spend through the Claude CLI — check subscription
   headroom at `GET /metrics/mind/subscription`.
3. The distiller takes up to 10 sessions per run every 30 minutes. A backlog
   that exceeds that rate for hours will not self-clear.

## NOT DRILLED — and the threshold is a guess

The SLO threshold of **20 sessions** is a labelled hypothesis: 10 sessions per
run × two consecutive runs. Observed backlog when it was set: 0. Nothing has
falsified it, so treat a first breach as information about the threshold as
much as about the system.

To drill it, plant surplus episode logs in the episode directory and watch the
backlog rise; that has not been done.
