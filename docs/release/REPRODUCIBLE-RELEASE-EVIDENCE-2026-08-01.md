# Reconciliation — 07 Reproducible supply chain and release proof

Roadmap record: `durability-reproducible-release` (corvus-long-horizon)
Session: `44fb171c-7704-4155-8374-098d2d6e2d2b`, claude-code
Worktree: `~/Projects/corvus-wt/reproducible-release`, branch `wt/reproducible-release`
Base commit: `e3f9e3774ac6fe710702108d3f38403b3ccf986e`
Date: 2026-08-01
Ledger revisions observed: 106 → 111 (re-admitted five times; a parallel session
was mutating the ledger throughout)

**Status: substantially complete, with two criteria constrained by unmet prereqs
and one item deliberately deferred. Nothing is committed — the work sits
uncommitted in the worktree for review.**

---

## 1. Scope correction recorded up front

The record declares four prereqs. **Two of them are not done:**

| prereq | status |
|---|---|
| `durability-bootstrap-recovery` (01) | done |
| `durability-deterministic-ci` (02) | done |
| `durability-frontend-contracts` (05) | **planned** |
| `durability-operational-envelope` (06) | **planned** |

The record's own `reviewTrigger` asks for exactly this note. Consequences are
tracked per-criterion in §3.

### Where repo reality differed from the record's prompt

The prompt was written from the 2026-07-30 architecture review and has drifted:

- *"the review found no pyproject/lock, pre-commit, tox, or pytest.ini"* —
  **`backend/pytest.ini` now exists**, almost certainly from record 02. The rest
  of that list was accurate. `pre-commit` and `tox` remain absent and were **not**
  added: no verification criterion requires them, and a second test entry point
  would directly undercut record 02's single-lane contract.
- *"The Python requirements mix exact pins and broad `>=` ranges"* — confirmed
  and quantified: 42 exact pins, 11 open ranges, and no transitive closure at all.
- The container provider contradiction was confirmed in full, and was worse than
  described: `docker-compose.yml` also mounted the host's `~/.claude` into
  `/root/.claude`, i.e. personal credentials into a path the runtime user
  cannot read.

### Two defects the record did not anticipate

- **`scipy` was imported but undeclared.** `backend/app/routers/performance.py:10`
  does `from scipy import stats`, and scipy appeared nowhere in
  requirements.txt. It resolved only as an accidental transitive of
  sentence-transformers — a latent import error one upstream release away.
- **The browser smoke never gated anything.** `demo-smoke.yml` runs Playwright,
  but only on `push` to `demo`/`main`. No pull request has ever been gated on
  browser evidence. It also pinned `node-version: 20`.

---

## 2. Measured evidence

### Dependency lock

| | before | after |
|---|---|---|
| entries | 53 | 118 |
| open `>=` ranges | 11 | 0 |
| artifacts hash-pinned | 0 | 118 |

65 previously-unconstrained transitive packages (torch, transformers,
tokenizers, huggingface-hub, …) are now pinned. `anthropic==0.84.0` was removed:
never imported anywhere, and contrary to the standing policy that Anthropic
calls go through the Claude CLI, never the SDK.

Resolver is `uv`; pip-tools 7.x was tried and **fails against current pip**
(`ImportError: cannot import name 'stdlib_pkgs'`).

Gate proven both directions:
```
lock --check, unmodified        -> exit 0 ("lock is current")
pyproject + tenacity (planted)  -> exit 1 ("requirements.txt is stale")
restored                        -> exit 0
```

### Two-build reproducibility

Two builds from the same commit, the **second with `--no-cache`**:

| compared | result |
|---|---|
| installed package set | **IDENTICAL** — 120 packages |
| frontend bundle (sha256 per file) | **IDENTICAL** — 9 files |
| `/app` tree excl. dist (sha256 per file) | **IDENTICAL** — 256 files |

Build A 185s, both exit 0, 5.7GB each.

Note: docker *image IDs* differ between the two builds and are not a meaningful
equality test — layer metadata carries per-build timestamps. Content equality is
the claim, and it is what was measured.

### Artifact scan

```
corvus-mind:dryrun-a   -> exit 0   artifact scan clean (user=corvus)
corvus-mind:regression -> exit 1   (original defect reinstated)
    env CLAUDE_CLI_PATH references /root but the image runs as 'corvus'
    CLAUDE_CLI_PATH is not an executable reachable by 'corvus'
```

### Removing the apt layer did not break native deps

Verified **inside the built image**: `psycopg2, scipy, numpy, torch,
sentence_transformers, fitz, igraph, leidenalg, docx, bs4, jwt, fastapi,
alembic` all import, and `app.main` imports. torch bundles its own libgomp;
psycopg2-binary bundles libpq.

### Secret scan

Initial run produced 5 findings, **all false positives** — four
`yggdrasil:yggdrasil@localhost` dev defaults and one deliberate honeypot canary.
The rule was narrowed to remote hosts only, and the canary carries an inline
`supply-chain: allow` pragma. Result: clean, while a planted remote database URL
carrying an inline password (`admin:<password>@prod-db.example.com`) is still
caught.

A small proof the rule is live: the first draft of *this document* quoted that
planted URL in full, and the scan failed on its own reconciliation. The sentence
above was reworded rather than suppressed.

### Dependency audit — real findings

**15 known advisories across 6 packages.** Fix versions measured 2026-08-01:

| package | advisories | fix |
|---|---|---|
| idna 3.11 | PYSEC-2026-215 | 3.15 |
| mcp 1.26.0 | PYSEC-2026-3481/3482/3483 | 1.27.2 / 1.28.1 |
| pydantic-settings 2.13.1 | GHSA-4xgf-cpjx-pc3j | 2.14.2 |
| pygments 2.19.2 | PYSEC-2026-2987 | 2.20.0 |
| pytest 9.0.2 | PYSEC-2026-1845 | 9.0.3 |
| starlette 0.52.1 | PYSEC-2026-161/248/249/2280/2281 | 1.0.1 → 1.3.1 |

**These were not remediated.** Reasoning, stated plainly so it can be overruled:
this session's mandate was reproducibility; a parallel session was mid-refactor
on the same repo; and starlette is constrained by `fastapi==0.135.1`, so moving
to starlette 1.x is a framework migration, not a lock edit. Bumping six packages
under those conditions trades a known state for an unverified one.

Instead the debt is explicit and non-regressing:
`backend/supply-chain-allowlist.json` accepts exactly these 15, each with its
fix version and a `review_by` of 2026-09-01. Gate proven:

```
all 15 accepted                          -> exit 0
one entry removed (unaccepted advisory)  -> exit 1
review_by backdated to 2020-01-01        -> flagged stale, exit 1
restored                                 -> exit 0
```

### Release dry-run — executed end to end

Against host PostgreSQL on a disposable `corvus_migration_dryrun`, serving on
port **8055** so the live `corvus-mind.service` on 8005 was never touched
(confirmed `active` and HTTP 200 afterwards).

1. Disposable DB migrated to prior release `022_roadmap_ledgers` — **4s, exit 0**.
   This traversed migration `017_drop_corvus_tables`, so the previously-recorded
   "fresh DB breaks at 017" failure **is fixed** (record 01). That memory is stale.
2. `pg_dump -Fc` pre-deploy backup — 164K at revision 022.
3. Deployed `corvus-mind:dryrun-a`; `start_backend.sh` migrated **022 → 025**;
   healthy in **30s**.
4. Verification passed 3/3 (health, schema at head, artifact revision matches).
5. Planted bad release (`026_bad_release`, drops `neurons.standard_date`):
   schema advanced to 026, column dropped, and the app then **failed to boot**.
   Verification returned **exit 1**, failing health and schema.
6. Recovery: stop → drop/recreate → `pg_restore` → redeploy previous artifact →
   verify. **Total 40s**, verification 3/3 clean.

#### Limitations, recorded rather than smoothed over

- **Data loss is real.** A `post-backup-canary` neuron written after the dump did
  not survive (17 → 16 rows). RPO equals time since the last dump. There is no
  PITR/WAL archiving in this rehearsal.
- **The "rollback" is restore-then-forward-migrate, not a schema downgrade.**
  The bad migration's `downgrade()` intentionally raises, which is realistic:
  destructive migrations are usually irreversible in practice.
- **The provenance check passed on the bad-release image.** It was built `FROM
  corvus-mind:dryrun-a` and inherited its OCI labels, so a derived image is
  currently indistinguishable from its base. See §5.
- 40s is a warm-image, localhost, single-node number. It is a floor, not an SLO.
- PG15+ requires the disposable DB be created **owned by the app role**;
  otherwise migrations fail with `permission denied for schema public`. Cost one
  failed attempt before it was diagnosed.

---

## 3. Verification criteria

| # | criterion | verdict |
|---|---|---|
| 1 | Node 22.22.0 and one Python range across local/CI/Docker | **MET** — Dockerfile 20→22.22.0, demo-smoke 20→22.22.0, `requires-python = ">=3.11,<3.12"`; v20.20.0 defaults removed from `llm_provider.py`, `sweep.sh`, README |
| 2 | Lock-driven; two clean builds equivalent | **MET** — measured identical across packages, bundle, and file tree |
| 3 | Artifact free of credentials, `/root` assumptions, unreachable CLI path | **MET** — scan clean, regression image fails with the exact findings |
| 4 | Required PR checks: backend lanes, migration smoke, architecture fitness, frontend type/build/browser smoke, security | **MET in configuration, UNVERIFIED in execution** — all six components wired into `required-merge-gate`; not executed, since these are GitHub-hosted workflows and this branch is unpushed. Each component command was run locally. |
| 5 | Publishing conditionals verified against real remotes | **MET** — `publish-pypi` proven dead (only remote is `tylerbvogel-max/corvus`) and retired with rationale in-file |
| 6 | Scans on source and artifact; SBOM and provenance attached | **MET** — secret/audit/artifact scans all exercised with negative controls; CycloneDX 1.5 SBOM (118 components, `--output-reproducible`) and provenance record generated |
| 7 | Dry-run deploys the exact artifact against an upgraded disposable DB and passes the single operational verification command | **MET, with an interim command** — `verify_deployment.py` was written *because* record 06 owns the real one and is still `planned`. This is a stopgap 06 should absorb. |
| 8 | Rollback/restore exercised after a planted bad release, recovery time and limitations recorded | **MET** — 40s, limitations above |

**Acceptance criteria** (all four): a release is now the promotion of a tested
immutable artifact (release.yml calls the merge gate, then builds once and scans
that build); runtime and dependency versions are explicit and reproducible;
controls are automated and attached to artifacts; and no tag can be cut without
the gate passing. The fourth is enforced by workflow structure, not yet observed
firing in CI.

---

## 4. CI seam with the `kernel-replay-ungated` session

That session is adding a merge-gate lane for `tests/replay_nvm_throwaway.py`.
**We both edit `.github/workflows/required-merge-gate.yml`.**

- **The four existing test lanes in `backend/scripts/run_test_lane.py`
  (`hermetic`, `integration`, `required-backend`, `database`) were not touched.**
  That file is unmodified by this session.
- Two gate components were **added alongside** the existing four jobs:
  `frontend-browser-smoke` and `supply-chain`.
- **The one line we will both want is the aggregator.** It was:
  ```yaml
  run: test "$RESULTS" = "success,success,success,success"
  ```
  A literal equality test means *every* branch that adds a gate component must
  edit that exact line — a conflict by construction. It is now count-independent
  (asserts nothing is failure/cancelled/skipped), so adding a lane no longer
  requires touching it.

  **Reconciling: take this version.** If the other branch adds a job and keeps
  the equality form, the merged workflow fails with 6+ components against a
  4-component string. Their new job still needs adding to the `needs:` list —
  that part is an ordinary additive merge.
- I also rewrote `release.yml` and touched `demo-smoke.yml` (added
  `workflow_call`, Node 22.22.0). Neither is expected to be in their path.
- `backend/tests/replay_auditor_honeypot.py` gained a two-line comment pragma.
  Their lane targets `replay_nvm_throwaway.py`, a different file in the same
  directory — worth a glance, not a conflict.

---

## 5. Handoff — concrete next steps

Ordered by dependency, not by size.

1. **Push the branch and let the gate run once.** Criterion 4 is configured but
   never executed. The nested `workflow_call` chain (`release.yml` →
   `required-merge-gate.yml` → `demo-smoke.yml`) is two levels deep and valid
   under GitHub's four-level limit, but has not been observed. Expect the first
   run to be slow — `frontend-browser-smoke` now installs Chromium per PR.
2. **Reconcile with `kernel-replay-ungated` before merging** — §4. Take the
   count-independent aggregator.
3. **Remediate the 15 advisories** in `backend/supply-chain-allowlist.json`
   before 2026-09-01, or the gate starts failing by design. Suggested split: the
   five easy ones (idna, pygments, pytest, pydantic-settings, mcp) in one pass
   with a lane run behind them; **starlette needs its own record** — it is a
   fastapi framework migration, not a bump.
4. **Record 06 should absorb `verify_deployment.py`.** It is deliberately
   minimal and marked interim in its own docstring. The real contract needs
   readiness-vs-liveness semantics and SLOs, which 06 owns.
5. **Close the derived-image provenance hole.** The artifact scan cannot
   currently tell an image from one built `FROM` it. Options: sign artifacts, or
   have the build stamp a digest of its own inputs rather than inheriting
   labels. Small, and it makes "we deployed the tested build" real.
6. **Decide on PITR.** The rehearsal proved dump-and-restore works and proved it
   loses everything written since the dump. If that RPO is unacceptable for the
   hosted product, WAL archiving belongs on the roadmap; if it is acceptable,
   say so explicitly so nobody re-litigates it.
7. **Optional cleanup:** `docker rmi corvus-mind:dryrun-a corvus-mind:dryrun-b`
   frees ~11GB. They were left in place so the evidence above can be re-checked.

### Not done, deliberately

- No `pre-commit`, no `tox`. Not required by any criterion, and a second test
  entry point would undercut record 02's single-lane contract.
- No dependency version bumps (§2).
- Nothing committed or pushed. The worktree is dirty by design; review first.
