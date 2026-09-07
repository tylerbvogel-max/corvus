# Supply Chain and Release Contract

Companion to [SCHEMA-RECOVERY.md](SCHEMA-RECOVERY.md). That document owns schema
authority; this one owns *what gets built, from what, and what has to be true
before it ships*.

Measured evidence for the session that established this contract lives in
[release/REPRODUCIBLE-RELEASE-EVIDENCE-2026-08-01.md](release/REPRODUCIBLE-RELEASE-EVIDENCE-2026-08-01.md).

## One dependency authority

`backend/pyproject.toml` is the authority. It lists only what source actually
imports or what the process shells into.

`backend/requirements.txt` is a **generated lock** — fully pinned, hash-verified,
including the whole transitive closure. Never hand-edit it.

```bash
# change a dependency
$EDITOR backend/pyproject.toml
python backend/scripts/lock_dependencies.py            # regenerate the lock
python backend/scripts/lock_dependencies.py --check    # what CI asserts
python backend/scripts/lock_dependencies.py --upgrade  # deliberate version bump
```

The resolver is `uv`. pip-tools was tried first and rejected: pip-tools 7.x
imports `pip._internal.utils.compat.stdlib_pkgs`, which current pip no longer
exports, so it only runs if you pin pip itself backwards. A lock tool that needs
its own tool pinned is not a lock strategy.

`--check` seeds its scratch compile from the committed lock, because `uv` reads
an existing output file as resolution *preferences*. Without that seeding the
check reports staleness the moment any upstream publishes a new release, which
would train people to ignore it.

### What the lock changed

| | before | after |
|---|---|---|
| entries | 53 | 118 |
| open `>=` ranges | 11 | 0 |
| artifacts pinned by hash | 0 | 118 |

The 65 newly-pinned packages are the transitive closure that was previously
unconstrained — `torch`, `transformers`, `tokenizers`, `huggingface-hub` and the
rest floated free on every build.

`scipy` was **imported at `backend/app/routers/performance.py:10` but absent from
requirements.txt**; it resolved only as an accidental transitive of
sentence-transformers. It is now declared.

The `anthropic` SDK was pinned but never imported anywhere — Anthropic calls go
through the Claude CLI subprocess in `llm_provider.py`, never the SDK. It is not
in the authority list, so it is no longer installed.

## Supported runtimes

| runtime | version | enforced by |
|---|---|---|
| Python | `>=3.11,<3.12` | `requires-python` in pyproject; `python:3.11-slim` base |
| Node | 22.22.0 | Dockerfile frontend stage; `frontend-type-build` CI lane |

The container previously built the frontend on `node:20-alpine` while CI
type-checked it on 22.22.0 — the artifact was never built by the toolchain that
gated it.

Both base images are pinned **by digest**. Tags are mutable; a tag pin means two
builds of the same commit can silently disagree.

## Removed system packages

The runtime image has no `apt-get` layer at all. Debian package versions were
unpinned, making that layer the largest source of build-to-build drift. Each
package was checked before removal, and the resulting image was verified to
import every native dependency:

| package | why it was removable |
|---|---|
| `gcc`, `libpq-dev` | only needed to compile psycopg2 from source; we install `psycopg2-binary`, whose manylinux wheels bundle libpq |
| `curl` | only used by `HEALTHCHECK`; replaced with the interpreter already in the image |
| `nodejs`, `npm` | the image never installed the Claude CLI, so Node served nothing at runtime; the frontend builds in a separate stage |

Verified in the built image: `psycopg2, scipy, numpy, torch,
sentence_transformers, fitz, igraph, leidenalg, docx, bs4, jwt, fastapi,
alembic` all import, and `app.main` imports. torch ships its own `libgomp`.

## Provider posture: nothing baked in

The image ships with **no LLM provider configured**, matching the product
direction (cloud graph, user-supplied brain, zero server-side LLM spend).

`CLAUDE_CLI_PATH` used to be baked as
`/root/.config/nvm/versions/node/v20.20.0/bin/claude`. That was wrong three
ways at once: the image runs as `corvus` and cannot read `/root`, the image
never installed that CLI, and the path pinned a Node version the build no longer
used. `docker-compose.yml` compounded it by mounting the host's `~/.claude` into
`/root/.claude` — personal credentials, into a path the runtime user cannot read.

`llm_provider.py` now *resolves* the CLI: explicit `CLAUDE_CLI_PATH`, then
`PATH`, then the highest nvm-installed copy, then `""` (provider simply
unavailable). `eval/locomo/sweep.sh` mirrors that order exactly — it probes the
same binary the app calls, which is why the old hardcoded v20.20.0 default was a
latent repeat of the 16-hour false-negative recorded in that script's comment.

Using the host CLI is a **local dev** posture. In a container, bring an API key.

## Scans, SBOM, provenance

```bash
python backend/scripts/supply_chain.py secrets                     # source
python backend/scripts/supply_chain.py audit                       # locked deps
python backend/scripts/supply_chain.py sbom                        # CycloneDX 1.5
python backend/scripts/supply_chain.py artifact corvus-mind:TAG    # built image
python backend/scripts/supply_chain.py provenance --image corvus-mind:TAG
```

All are pip-installable (`pip-audit`, `cyclonedx-bom`, plus stdlib). That is
deliberate: `syft`/`trivy`/`gitleaks`/`cosign` are not installed on the dev
machine, and a control that only ever runs on a CI runner is one nobody can
reproduce when it fires.

**The artifact scan is the one source scanning cannot replace.** It asserts the
image runs as an unprivileged user, that no environment value references
`/root` or another user's home, that no value matches a credential pattern, and
that any configured provider CLI path is actually executable *by the runtime
user*. Reinstating the original defect makes it fail with exactly that finding.

**Secret-scan suppressions are inline**, via a `supply-chain: allow` pragma on
the offending line or in the comment block directly above it — so every
suppression appears in the diff that introduces it rather than in a baseline
file. The motivating case is the deliberate canary in
`backend/tests/replay_auditor_honeypot.py`.

**Vulnerability findings are gated by `backend/supply-chain-allowlist.json`.**
Anything not listed fails the build. Each entry carries the measured fix version
and a `review_by` date, and the gate fails once an entry lapses — a permanent
exception list is just a suppressed alarm.

## Retired publishing paths

`release.yml` carried a `publish-pypi` job gated on
`github.repository == 'tylerbvogel/corvus'`. The only configured remote is
`tylerbvogel-max/corvus`, so **the condition could never be true and the job had
never run once**. It also built its package from a pyproject heredoc inlined in
the workflow, declaring unpinned dependencies — the opposite of the contract
above.

It is retired in a comment block at the foot of `release.yml`, with the evidence
and the steps to revive it, rather than silently deleted.

## Release dry-run

Required before a tag. See the evidence document for a worked run.

1. Build twice from the same commit (second with `--no-cache`) and diff the
   installed package set, the frontend bundle hashes, and the `/app` file tree.
2. Create a disposable `corvus_migration_*` database at the *previous* release's
   Alembic revision. Create it owned by the application role — PG15+ does not
   grant `CREATE` on `public` to non-owners, and migrations fail with
   `permission denied for schema public` otherwise.
3. `pg_dump -Fc` before deploying.
4. Pull and deploy the exact registry reference from release `provenance.json`.
   `registry_reference` names the immutable manifest; `image_id` is the Docker
   config digest that must match the running container's `.Image`.
   `start_backend.sh` migrates to head.
5. Run the single verification command:
   ```bash
   python backend/scripts/verify_deployment.py --url URL \
     --database-url ... --expect-revision ... \
     --container ... --expect-source-revision "$(git rev-parse HEAD)" \
     --expect-image-id sha256:... --frontend --packaged-atlas
   ```
6. Plant a bad release, confirm detection, then restore and redeploy. Record the
   recovery time and what was lost.

The release workflow publishes the scanned image to GHCR, then a separate runner
pulls it by registry digest and starts it against a disposable database. Release
creation requires that runner's deployment receipt, including exact image identity
and the frontend served by the container. Dry runs also publish a candidate image
and verify it, but do not create a GitHub release. Candidate tags identify workflow
run and attempt; deployments use the immutable digest from provenance.

The image sets `CORVUS_FRONTEND_DIST=/app/frontend/dist`. Source checkouts use
their sibling `frontend/dist` by default. An explicitly configured directory without
`index.html` fails startup rather than silently omitting the interface.
The two committed architecture JSON artifacts are also shipped, with
`CORVUS_ARCHITECTURE_DIR=/app/architecture`; the release probe checks their API.
