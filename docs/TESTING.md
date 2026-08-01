# Deterministic test lanes

Corvus classifies every collected backend test into exactly one primary lane.
An unmarked backend test defaults to `hermetic`; tests that need anything else
must declare the marker. Collection fails if a test declares multiple primary
lanes. The source of truth is `backend/pytest.ini`, enforced by
`backend/tests/conftest.py` and executed through
`backend/scripts/run_test_lane.py`.

| Lane | Command from `backend/` | Dependencies | Merge policy |
|---|---|---|---|
| Hermetic | `venv/bin/python scripts/run_test_lane.py hermetic` | No database, network, provider, or external service | Required |
| Integration | `venv/bin/python scripts/run_test_lane.py integration` | In-process application only; external I/O replaced | Required |
| Database | `CORVUS_TEST_DATABASE_URL=postgresql+asyncpg://.../corvus_test_ci venv/bin/python scripts/run_test_lane.py database` | Disposable PostgreSQL database whose name starts `corvus_test_` or `corvus_migration_` | Required migration smoke |
| Kernel replay | `REPLAY_DB=corvus_test_kernel_replay venv/bin/python scripts/run_test_lane.py kernel-replay` | Existing disposable `corvus_test_*` database; the schema is dropped and rebuilt | Opt-in; inputs guarded on every PR |
| Evaluation | `venv/bin/python scripts/run_test_lane.py evaluation` | Frozen local fixtures; no provider or database | Scheduled/opt-in |
| Live provider | `CORVUS_RUN_LIVE_PROVIDER=1 CORVUS_LIVE_PROVIDER_MODEL=opus venv/bin/python scripts/run_test_lane.py live-provider` | Explicitly authorized model plus working credentials/authenticated CLI | Opt-in only |

The required workflow selects hermetic and integration tests in one collection
with `venv/bin/python scripts/run_test_lane.py required-backend`. The composite
selection avoids paying the roughly 16-second import/collection cost twice;
the two primary lane markers remain separately runnable for diagnosis.

## Expensive proofs, cheap guards

The `kernel-replay` lane is the reconsolidation kernel's only end-to-end proof
against real SQL, the real one-step lifecycle and the real action bus. It is
deliberately NOT in the merge gate: it destroys and rebuilds a schema, its
database must be created out of band, and it costs roughly 100 seconds.

That exemption is only defensible because its INPUTS are guarded on every PR.
`backend/tests/kernel_matrix_fixtures.py` holds the review packets the replay
feeds the kernel, and `backend/tests/test_kernel_replay_fixture_guard.py`
(hermetic, milliseconds, no database) asserts they still pass `validate_packet`
and still imply the dispositions the replay asserts. It also keeps a
pre-evidence-frame plain-text packet as a negative control, so a guard that
stopped guarding fails instead of passing.

This pattern exists because the replay silently rotted for weeks when evidence
frames became mandatory and no lane ran it (`kernel-replay-ungated`,
2026-08-01). If you add another expensive opt-in proof, guard its inputs the
same way.

The evaluation command above runs deterministic harness contract tests. It
does not start LoCoMo, LoCoMo-Plus, or any scored benchmark. Benchmark entry
points remain separate, slow operations.

## Time and failure contracts

- Every pytest test has a 30-second default timeout. Evaluation and provider
  tests declare a 120-second per-test bound where appropriate.
- Each lane runner has an independent job bound. On a job timeout it requests
  an all-thread faulthandler dump, allows a 10-second shutdown grace period,
  then kills the process group.
- Every run writes JUnit XML, pytest debug logs, combined output, duration and
  exit metadata under `.artifacts/test-lanes/<lane>/`. A failure also writes a
  compact failure tail.
- CI jobs have an outer `timeout-minutes` bound and upload lane artifacts on
  failure. Live-provider selection without authorization or a configured model
  fails; it does not turn into a successful skip.

## Database isolation

`test_migration_smoke.py` refuses any database not explicitly named as
disposable. Its fixture drops and recreates `public` before each test and again
in `finally` after each test. The required lane runs only the empty-bootstrap
migration smoke. The representative snapshot upgrade is tagged `snapshot` and
requires an explicitly supplied `CORVUS_TEST_SNAPSHOT_DUMP`.

## Required pull-request gate

`.github/workflows/required-merge-gate.yml` runs on every pull request without
path filtering so a required check cannot disappear. Its parallel jobs cover:

1. architecture evidence and fitness conformance;
2. hermetic and in-process integration backend tests in one collection;
3. disposable-PostgreSQL migration smoke;
4. frontend TypeScript checking and production build under Node 22.22.0.

The final `required-merge-gate` job depends on all four and is the stable branch
protection target. Release-tag testing remains defense in depth, not the first
meaningful failure detector.

## Thread-boundary testing rule

Production embedding calls stay off the event loop. Hermetic tests replace the
async executor boundary itself and run the already-faked synchronous function
inline. Patching only the function inside a real worker is not hermetic: in an
environment that constrains worker scheduling, the assertion can pass while
event-loop teardown waits forever for an executor lifecycle it does not own.
