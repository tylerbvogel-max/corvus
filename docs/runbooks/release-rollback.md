# Runbook: bad release and rollback

## Symptom

A release is deployed and the service will not serve, or serves wrongly. The
distinguishing question is whether a restart can fix it.

- **Schema BEHIND the build** — the container fixes itself. `start_backend.sh`
  applies reviewed migrations before startup, so a deploy that raced its
  migration heals on restart. Confirmed by drill.
- **Schema AHEAD of the build** — no restart will fix it. This is the real
  rollback case: a later release migrated the database forward and you are
  rolling back to a build that does not know that revision. Alembic cannot
  locate it, and schema authority refuses to serve.

## Detection

```
backend/venv/bin/python backend/scripts/verify_deployment.py \
  --url <deployed-url> --database-url <db> --expect-revision <head> \
  --container <name> --expect-source-revision <git-sha>
```

Exit 2 with liveness/readiness/recall/jobs/slo all ERROR plus a `schema` FAIL
naming both revisions is the signature of a database ahead of its build.

## First response

1. Do **not** stamp `alembic_version` by hand to make startup succeed. Serving
   a schema the build was not written for corrupts quietly; fail-closed is the
   last gate before that.
2. Restore the pre-release backup, then redeploy the previous artifact.
3. Verify with the command above — including `--expect-source-revision`, so
   "we rolled back to the build we meant to" is checked rather than assumed.

## Drill — EXECUTED 2026-08-03

Built the artifact locally with a provenance label, deployed it against a
disposable database migrated to head, then planted the failure. Observed:

```
pre-release backup                      0.1s   (231 KB, pg_dump -Fc)
planted: alembic_version -> 028_future_release
container                               Exited (255)
  ERROR alembic: Can't locate revision identified by '028_future_release'
verify_deployment                       exit 2, schema FAIL naming both revisions
recovery: dropdb + createdb + pg_restore  2.4s
          redeploy + service answering   16.4s
          TOTAL                          18.8s
post-recovery verify_deployment         exit 0, 7/7 including provenance
```

## Two defects this drill found

**Migration 027's downgrade was broken on fresh databases.** The partial index
`ix_neurons_dormant_at` was created inside the column guard, and the baseline
migration runs `Base.metadata.create_all` — which builds `neurons` from the
CURRENT models, so `dormant_at` already exists when 027 runs, the guard skips,
and the index is never created. Live databases had it; freshly-migrated ones
silently did not, and downgrade then failed dropping an index that was never
made. Fixed by guarding on index existence; upgrade → downgrade → upgrade now
round-trips on a fresh database.

**An SLO no healthy fresh deployment could meet.** `recall-availability`
requires at least one query in the performance window, so a just-deployed
instance read as BREACHED. Now a tenant that has never served reports
`unknown`; one that served before and stopped still breaches, which is the
failure the objective exists for.

## Limitations

- The drill rolls back the DATABASE and redeploys the SAME artifact. Rolling
  back to a genuinely older image was not exercised; the failure mode proven is
  schema-ahead-of-build, which is the condition that makes image rollback hard.
- Recovery time is for a 231 KB disposable database on one machine. It is a
  floor, not a production estimate — restore time scales with data.
- `028_future_release` was stamped by hand rather than produced by a real
  later migration. The container's refusal is identical either way, but no
  actual forward migration was written and reverted.
