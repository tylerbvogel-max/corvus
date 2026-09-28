# Runbook: skill compiler failure

## Symptom

`~/.claude/skills/mind-*` stop refreshing. Sessions keep loading whatever was
last compiled, so behaviour silently ages instead of breaking — the graph moves
on and the projection does not.

## Detection

- `GET /metrics/mind/jobs` → `compile` `failing` or `late` (24h cadence)
- `corvus-job-alert` fires with the exception and remediation
- `GET /compile/status` shows eligible clusters without a matching manifest
- `GET /compile/models` shows admitted, challenged, and declined model receipts
- Structured logs: `job=compile`

## First response

1. Compilation spends Opus through the Claude CLI. Check the CLI answers before
   suspecting the compiler: a logged-out CLI fails here first.
2. Re-run and diff the output — the compiler is a projection, not an append, so
   running twice is safe and produces the same files:
   `curl -sf -X POST localhost:8005/compile/run`
3. Unexpected churn in `~/.claude/skills/` after a re-run means the graph
   changed, not that the compiler is broken.
4. A dormant lesson is excluded from compilation by design (synaptic
   downscaling). Missing content is not necessarily a failure — check
   `dormant_at` before treating it as one.
5. If a skill was archived after a model changed to `challenged`, inspect its
   exact quoted observation and falsifier in `/compile/models`. It will not
   republish from the same source fingerprint. A malformed reflection catalog
   stops publication; restore the last known good catalog rather than deleting
   it and resetting admission history.

## NOT DRILLED

No live failure has been planted for this job. Its failure path is the shared
`scheduled_run` wrapper drilled on the janitor, so the receipt and alert
mechanics are proven; what is unproven is anything specific to the compiler.
