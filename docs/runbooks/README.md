# Corvus runbooks

Six failure modes, each with a detection signal, a first response, and — where
the criterion demanded it — a drill that was actually executed rather than
imagined. A runbook nobody has run is a hypothesis about how the system fails.

Owned by roadmap record `durability-operational-envelope` (06), criterion 6.

| Runbook | Drill executed | Result |
|---|---|---|
| [Database unavailable](database-unavailable.md) | 2026-08-02 | liveness held, readiness failed correctly |
| [Schema mismatch](schema-mismatch.md) | 2026-08-02 | startup failed closed, exit 3 |
| [Provider failure](provider-failure.md) | 2026-08-02 | job failed loudly, receipt named the provider |
| [Backup restore](backup-restore.md) | 2026-08-02 | 39 MB restored in 7.5s, served real recall |
| [Distill backlog](distill-backlog.md) | not drilled | threshold is an unfalsified hypothesis |
| [Janitor / reconsolidation failure](janitor-failure.md) | 2026-08-02 (partial) | runner-level failure drilled; body-level by test only |
| [Skill compiler failure](skill-compile-failure.md) | not drilled | shared job wrapper is proven; compiler specifics are not |
| [Bad release and rollback](release-rollback.md) | 2026-08-03 | detected at exit 2; recovered in 18.8s, verified 7/7 |

## The one command

```
backend/venv/bin/python backend/scripts/verify_deployment.py \
  --url http://localhost:8005 --repo /path/to/corvus
```

Six checks — liveness, readiness, recall, jobs, SLO, atlas — each reported even
after one fails. Exit 0 verified, 1 a check failed, 2 a check could not run.

## Where the signals live

| Question | Endpoint |
|---|---|
| Is the process alive? | `GET /health` — touches no dependency, by design |
| Are dependencies satisfied? | `GET /ready` — 503 when not, with remediation |
| Are scheduled jobs healthy? | `GET /metrics/mind/jobs` |
| Are we meeting our objectives? | `GET /metrics/mind/slo` |
| What happened, correlated? | structured JSON logs; grep `request_id` or `job` |

## Drill hygiene, learned the hard way

A drill backend started with default settings **writes into the live receipts
directory** (`~/.corvus-mind/job-receipts/`). The provider-failure drill did
exactly that and left the live system reporting a failing `auditor` job that
had never actually run; it had to be deleted by hand afterwards.

Set `CORVUS_JOB_RECEIPTS_DIR` to a scratch path when drilling anything that
runs a job:

```
CORVUS_JOB_RECEIPTS_DIR=/tmp/drill-receipts DATABASE_URL=... uvicorn app.main:app --port 8009
```
