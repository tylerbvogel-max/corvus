# Maintenance outcomes

The scheduled receipt is the maintenance health authority. HTTP 200 means a
batch returned a report, **not** that every item succeeded. Existing endpoint
completed-batch status codes and report fields remain compatible; `/distill/run`,
`/auditor/run`, `/janitor/run`, and `/compile/run` add `maintenance`.

## Returned batches

`maintenance` and the receipt's `detail.batch` share this contract:

| Field | Meaning |
| --- | --- |
| `outcome` | `ok`, `partial`, `error`, or `no-work` |
| `unit` | Counted work unit, described below |
| `attempted` | `succeeded + failed` |
| `succeeded` | Completed work units, not necessarily graph mutations |
| `failed` | Units with returned operational failures |
| `skipped` | Known units not attempted, including caps and explicit skips |
| `counts_known` | True for a validated returned report |
| `reason` | Fixed `returned-item-failure` code on failure, otherwise null |

`partial` means both successes and failures; `error` means failures without
successes. Zero attempts is `no-work`, never a failure. A successful scan
finding nothing to change is completed work, not a failed mutation.

- Distillation counts sessions. `ready - processed` is skipped this run,
  including the limit backlog. Policy-rejected lesson candidates are not
  session-processing failures.
- Auditing counts critic reviews, including probes and controls. Verdict
  validation failures count as failures; critic-cap exclusions count as
  skipped. Evidence-clock deferral has zero known eligible reviews. Candidate
  selection is not itself a review, and unselected corpus rows are not counted.
- Janitor counts passes, not neurons or judge calls. Any returned consolidation
  error or unjudged delivery verdict makes that pass fail, even if other work
  within the pass succeeded. Evidence-clock skips count as skipped passes.
- Compiler counts operations: cluster composition, stale-artifact retraction,
  manifest-ghost reconciliation, and a rendered charter. Failed compositions
  are failures even when the compiler proceeds. Cached/capped clusters and a
  charter without a rendered path are skipped. Counts do not prove transaction
  atomicity or crash recoverability.

Malformed reports fail closed with a fixed diagnostic; validation uses explicit
exceptions and also runs under `python -O`. A thrown batch exception produces
`error` with `counts_known: false`: the wrapper cannot reconstruct how much work
committed before the interruption and does not fabricate item counts.
The four scheduled HTTP routes return a handled 503 with fixed detail
`maintenance-job-failed` after recording the error. They do not rethrow the
original exception into the ASGI server's traceback logger. Internal callers
of `scheduled_run` still receive the original exception. Request validation
and authentication run outside this job boundary and are not remapped.

## Health and observability

Both partial and complete failure map to existing health status `failing`.
The scheduled-jobs SLO already counts `failing + late`; it therefore includes
returned failures without changing its metric taxonomy. `no-work` is a healthy
scheduling check and advances `last_success`, whose meaning is the last healthy
batch completion, not the last graph write. Failure preserves that timestamp.
Unreadable or unsupported receipt outcomes are failing rather than healthy.

Structured completion/failure events use the actual receipt outcome. The
wrapper persists only allowlisted metadata, counts, and fixed error categories;
it does not log exception messages or tracebacks. The HTTP boundary prevents
uncaught batch exceptions from being logged by Uvicorn. Distiller and auditor
caught errors likewise use fixed codes. This is not a claim that every downstream
provider's own logging, unrelated application route, or historical log is sanitized.
Receipts intentionally trade raw error text for content-safe diagnostics.

The frontend job-health adapter passes this endpoint through; no current
frontend component calls it. No UI success badge or generated OpenAPI response
model exists for these untyped reports to migrate. Consumers should use job
health or `maintenance.outcome`, not HTTP success, as the health signal.

## Boundaries

Systemd serializes its own service unit only. Manual HTTP calls and multiple
workers are not serialized by a timer. Receipt replacement, database commits,
and filesystem markers are not one atomic transaction. This repair does not
provide leases, duplicate prevention, rollback recovery, or crash-window repair.
Nor does a returned failure necessarily trigger systemd OnFailure when HTTP
returns 200. Existing runner alerts and periodic receipt/SLO inspection remain
distinct signals. Personal services and runtime data are not deployment proofs.

The regression `tests/test_maintenance_outcomes.py::test_real_http_server_never_logs_batch_exception`
starts a real ephemeral Uvicorn listener, exercises all four routes with
synthetic exceptions, captures server logs, checks persisted receipts, and
proves a subsequent healthy no-work request. It uses neither a database nor
a provider. Run it outside sandboxes that prohibit loopback listeners.
