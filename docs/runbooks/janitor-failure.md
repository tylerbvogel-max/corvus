# Runbook: janitor / reconsolidation failure

## Symptom

Graph maintenance stops: duplicates accumulate, staleness goes unaudited, decay
stops running. Nothing user-visible breaks immediately, which is what makes it
dangerous.

## Detection

- `GET /metrics/mind/jobs` → `janitor` `failing` or `late`
- `corvus-job-alert` fires with the exception and remediation
- Structured logs: `job=janitor` correlates every line a run emitted

## First response

1. The receipt names which pass raised. Passes are independently selectable, so
   bisect rather than guess:
   `POST /janitor/run?consolidation=false&staleness=false&decay=false&lint=false&plasticity=false`
   then re-enable one at a time.
2. Decay and plasticity ride the **distilled-session clock**, not wall time. A
   run that reports "no sessions distilled since last run" is correct, not
   broken.
3. Reconsolidation proposals are human-gated. A backlog of proposals is not a
   janitor failure; it is a countersign backlog.

## Drill — PARTIALLY EXECUTED 2026-08-02

**Runner-level failure, drilled.** A systemd drop-in pointed the janitor unit
at a nonexistent route:

```
Result: success -> exit-code/22        (before this record, curl -s hid it)
OnFailure fired corvus-job-alert@corvus-mind-janitor.service
alert carried: job, tenant, owner, cadence, last success (preserved), remediation
```

**Body-level failure, NOT drilled live.** A job whose body raises is covered by
unit test only (`test_a_failing_run_records_the_exception_and_keeps_the_prior_success`).
The two paths differ: a body failure writes an error receipt, a runner failure
does not — so `GET /metrics/mind/jobs` still reads healthy after a runner
failure until the job goes `late`. The alert covers that window; the endpoint
does not. Worth closing if this failure mode ever bites for real.
