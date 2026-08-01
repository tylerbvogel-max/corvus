# Deterministic CI evidence — 2026-07-31

This is the implementation receipt for Roadmap Ledger record
`durability-deterministic-ci`. It records failures as observed; it does not
convert environmental failures into passing evidence.

## Measured baseline

- Backend collection before taxonomy changes: 1,031 tests in 78 top-level
  `test_*.py` files, collected in 12.24 seconds. The older review estimate was
  approximately 1,019 tests in 74 files.
- A bounded full run on the host completed 1,029 passed and 2 intentionally
  skipped migration tests in 16.02 seconds.
- The same code in the constrained coding sandbox reproduced the historical
  non-termination at async/thread boundaries.

## Citation-relevance deadlocks

Minimal commands selected these tests independently with an outer 30-second
bound and pytest faulthandler dumps:

- `test_guards_attach_relevance_payload`
- `test_escalation_judges_only_verify_band`

Both exited 124. The worker stack was idle at
`concurrent/futures/thread.py:_worker` on `work_queue.get`, while the main
thread remained in `asyncio/base_events.py:_run_once` under the
`pytest_asyncio` runner. In the full file, the first assertion printed
`PASSED`, then event-loop teardown stopped at `asyncio/runners.py:close`.

The tests patched `citation_relevance.embed_batch` inside a real
`asyncio.to_thread` worker. That is not hermetic: it still depends on worker
scheduling and executor shutdown. The repair replaces `asyncio.to_thread` at
the async boundary with an inline awaitable while retaining the fake embedder.
Production embedding remains off the event loop.

Post-fix constrained run: 16/16 citation-relevance tests passed with the new
30-second per-test timeout active.

## Reconsolidation-boundary stop

The isolated reconsolidation group reached 69 passing tests, then
`TestWriteGateLexicalLane::test_sub_fuse_sim_paraphrase_queues_via_lexical_lane`
printed `PASSED` and stopped during event-loop teardown. Faulthandler again
showed `asyncio/runners.py:close`; `_nearest_active_lesson` had submitted the
mocked embedder through `loop.run_in_executor`.

The repair patches that loop's `run_in_executor` boundary with an inline
awaitable for the three lexical-lane tests. Post-fix constrained run: 3/3
passed. The full reconsolidation files remain in the hermetic lane; no file or
test is excluded.

## Taxonomy and lane receipts

After implementation, 1,034 backend tests classify exactly once:

- 1,029 hermetic (the unmarked default policy);
- 1 in-process integration test;
- 3 database tests, including one opt-in snapshot test;
- 1 explicitly authorized live-provider test.

The required composite selection therefore collects 1,030 tests. The separate
slow/evaluation lane collected and passed 34 deterministic LoCoMo harness
contract tests; it did not start a benchmark. The real live-provider lane made
an Opus-grade configured-provider round trip and passed in 6.49 seconds.

Every pytest lane has a 30-second default per-test timeout, lane-specific job
bounds, all-thread stack signaling before forced termination, JUnit XML,
pytest debug logs, combined output, timing metadata, and failure-tail output.
CI adds an outer job timeout and uploads artifacts on failure.

## Database isolation

The migration lane refuses databases whose names do not start with
`corvus_test_` or `corvus_migration_`. A context-managed fixture resets
`public` before and in `finally` after each test. A planted
`ci_isolation_probe` table was absent after the context exited. After the final
gate pass, the disposable database had zero public tables and no probe; it was
then dropped, and a catalog query confirmed zero matching databases.

## Required-gate consecutive proof

Three consecutive fresh-source workspaces passed with zero flakes:

| Run | Required backend | Database | Architecture | Frontend | Wall clock |
|---|---:|---:|---|---|---:|
| 1 | 1,030 passed | 2 passed, snapshot deselected | fresh, byte-identical | type/build passed | 62s |
| 2 | 1,030 passed | 2 passed, snapshot deselected | fresh, byte-identical | type/build passed | 62s |
| 3 | 1,030 passed | 2 passed, snapshot deselected | fresh, byte-identical | type/build passed | 63s |

Each workspace had fresh source, caches, build output, test artifacts, and an
isolated copy of the installed lockfile dependency tree. `npm ls --all`
returned exit 0 for that tree. The attempted networked `npm ci` proof was
terminated after active registry sockets stopped making progress; an offline
retry failed explicitly because `react-refresh-0.18.0.tgz` was not cached.
Accordingly, local dependency acquisition is not claimed as evidence. The
GitHub gate still performs `npm ci` on a clean networked runner.

## Architecture-gate incidents

The first clean proof failed because `architecture.json` stored the absolute
checkout root. After making the root repository-relative, the second attempt
revealed nondeterministic Tarjan SCC ordering from set iteration. Sorting graph
neighbors, roots, and final components fixed both. Three consecutive extractor
runs then produced identical hashes:

- `architecture.json`: `862e1b14b8d15a1253f43ffb54dbd5442291db5fa05720e53ea79fa410f082e5`
- `conformance.json`: `915037a52b03aa09bc85bdde276328b964aa032efe9153e2bdcd2192dc90f301`

Strict conformance observed 456/456 classified units, zero unclassified units,
zero phantom boxes, zero violations of 15 machine invariants, current source
freshness, and zero regressions beyond accepted drift baselines.
