# ADR 003: Governed local operation packages, not executable skills

- Status: Accepted research verdict (Gate 3)
- Date: 2026-07-31
- Session: `019fb4b7-f943-7a83-bba9-578965665291`
- Roadmap: `corvus-long-horizon`
- Record: `mind-executable-playbooks-research`
- Settled inputs: [ADR 001: Live operational context](001-live-operational-context.md), [ADR 002: Self-validating operation receipts](002-self-validating-operation-receipts.md)
- Scope: architecture research only; this ADR ships no production executor

## Verdict

Corvus should support a narrow class of **declarative operation packages
interpreted by a local client and bound to compiled-in typed handlers**.

The package is data, not code. It may select a registered operation, constrain
typed inputs and targets, declare bounded effects and policy, and bind the
operation-receipt profile. It may not contain shell, Python, JavaScript, URLs,
filesystem paths, unit names, environment values, or arbitrary steps. The
local client—not the Corvus backend, memory graph, skill compiler, or MCP
server—resolves the registered target, obtains harness/user approval, exercises
local permissions, invokes the typed handler, and emits ADR 002 receipts from
ADR 001 live observations.

Call the product concept an **operation package**, not an executable skill.
Skills remain descriptive procedural knowledge. An imported skill may mention
an operation ID, but it cannot install, activate, authorize, or invoke one.

The first bounded pilot is exactly one local developer operation:

```text
ensure_running(surface="corvus-mind")
```

Its only legal target is a locally registered Corvus Mind developer surface;
its only legal effects are starting registered missing components and
restoring state that the same invocation changed. It cannot accept arbitrary
commands, services, units, tenants, URLs, paths, or remote hosts.

Corvus remains a harness-neutral memory provider. This decision does not add
an agent loop, scheduler, planner, general workflow engine, remote execution
plane, or Hermes-style runtime.

## Why this is the selected model

The current system already has four separate kinds of authority, and none may
be silently collapsed:

1. Memory and skills describe learned knowledge.
2. The Action Bus validates and audits Corvus database mutations.
3. Roadmap admission authorizes a session to mutate a mapped project at a
   specific ledger revision.
4. The harness and operating system authorize local effects.

A local interpreter can require all four where they apply without pretending
that one grants the others. A backend or memory-owned executor would invert
that boundary: retrieved content or a service connection would become adjacent
to machine authority.

There is also a bootstrap constraint. The current Corvus-Mind MCP server is a
thin local stdio client of the backend on port 8005. An operation intended to
start that backend cannot depend on the backend already being reachable. The
execution boundary therefore has to remain in an independently installed local
client.

## Current boundary inventory

### Skill projection

`skill_compiler.py` composes stable, evidence-gated lesson clusters into
`mind-*` prose using Opus. It treats the graph as source and the skill as build
output, records source-neuron lineage, retracts stale renderings, and creates an
informational graph shadow through the Action Bus.

`skill_projection.py` writes the rendered Markdown into a canonical local
directory and the declared Claude Code, Codex, and OpenCode skill directories.
The current projection is direct filesystem output; it has no executable
package schema, target capability registry, dry-run contract, approval step,
or OS-effect receipt. The separate `mind-skill-projection-isolation` record is
therefore a real prerequisite for safe interoperability, but skill projection
is not an execution substrate even after that fix.

Trusting a lesson enough to publish prose is not evidence that its procedure is
safe, complete, current, reversible, portable, or authorized to run.

### MCP tools

The backend graph MCP currently exposes nine graph/query operations:
`query_graph`, `verify_citations`, `impact_analysis`, `neuron_detail`,
`browse_departments`, `graph_stats`, `cost_report`, `reconciliation_report`,
and `discover_clusters`.

The harness-facing Corvus-Mind MCP exposes five tools: `recall`, `remember`,
`forget_document`, `roadmap_context`, and `roadmap_admit`. It is deliberately a
thin stdio-to-HTTP adapter. `remember` and `forget_document` are governed memory
mutations; `roadmap_admit` records planning authority. None grants operating
system authority.

Adding `service.start(command=...)` to MCP would create a privileged mutation
surface reachable wherever the tool is registered and would fail the Corvus
Mind bootstrap case. MCP may later expose a request/inspection facade for an
already local operation client, but it is not the authority owner or execution
engine selected here.

### Action Bus and proposal governance

The Action Bus registers namespaced Pydantic inputs, records actor and lineage,
supports idempotency keys and parent/child actions, optionally holds an action
pending approval, and runs database handlers inside a savepoint. It owns graph
and database mutation audit.

It does not own OS effects, target readiness, cancellation, effect deadlines,
repair, or short-window durability. As ADR 002 established, `applied` means the
handler transaction completed; it does not universally prove the requested
domain outcome.

Proposal approval resolves reviewer identity from authentication, checks for
staleness, and applies the approved proposal and child actions transactionally.
The tiered write gate may auto-route low-authority observational graph writes,
while higher-authority changes remain human-gated. That policy governs memory
writes only. Neither informational memory authority nor an auto-applied
proposal may activate an operation package.

### Roadmap admission

The canonical ledger is projected into an owner-only, atomically replaced
local cache. Admission receipts bind a session to an unfinished record and
ledger revision. Shared lifecycle hooks classify mapped-project mutations and
block missing, ambiguous, or stale admission.

Synthetic unadmitted mutations were blocked in the real shared hook for all
three supported adapters:

| Harness | Observed behavior |
| --- | --- |
| Claude Code | shared hook returned `permissionDecision=deny` at ledger revision 62 |
| Codex | shared hook returned `permissionDecision=block` at ledger revision 62 |
| OpenCode | the real plugin consumed the same block and threw before tool execution |

A paired Codex `pwd` probe returned no block, and the planted target file was
absent afterward. The operation client must require a positive, current
admission for a mapped-project operation; it must not inherit the ordinary
hook's fail-open behavior if the cache or hook is unavailable.

### Systemd and service scripts

The repository unit for `corvus-mind.service` declares tenant `corvus-mind`,
port 8005, an Alembic `ExecStartPre`, uvicorn, and restart-on-failure. Timer
units call the distiller every 30 minutes, janitor every 6 hours, auditor every
12 hours, and compiler every 24 hours.

Live inspection found `corvus-mind.service` loaded, enabled, and active with
PID 273; `/health` returned HTTP 200 and `/tenant` returned `corvus-mind`.
However, the installed unit did not match the repository specimen: it used the
shared `deploy/memory-tenant.env` and lacked the repository's Alembic
`ExecStartPre`. That drift is direct evidence against treating a checked-in
script or remembered unit definition as current target truth. ADR 001 live
resolution has to precede every effect.

The current Codex sandbox also demonstrated that supervisor access is
permission-context-sensitive: an ordinary command context reported
`Failed to connect to bus: Operation not permitted`, while the specifically
authorized read-only `systemctl --user show` probe succeeded. Textual command
identity is not sufficient authority evidence.

### Existing operational contracts

- ADR 001 exclusively owns live status, target identity, point-in-time
  freshness, contradictions, and abstention states.
- ADR 002 exclusively owns the receipt envelope, terminal status algebra,
  claims, attempts, causality, redaction, retention, and verifier identity.
- Action Bus, proposal, roadmap reconciliation, architecture conformance, and
  future deployment evidence remain domain artifacts referenced by ADR 002
  claims where they genuinely prove one.
- No canonical production deployment receipt was found. This decision does not
  create one.

## Four-model comparison

| Model | Evidence-backed strengths | Decisive weakness | Decision |
| --- | --- | --- | --- |
| Typed MCP operations | Strong typed discovery; fits existing tool registration and harness permission prompts | The current Mind MCP depends on the service it would need to start; putting OS mutation in stdio or backend MCP expands privilege and couples authority to tool registration | Rejected as execution owner |
| Declarative manifests interpreted locally | Package is inspectable and digest-pinned; effects stay inside harness/OS policy; portable contract can bind per-OS providers; mock passed the failure matrix | Requires a deliberately small local client and one tested provider per platform | **Selected** |
| Generated scripts checked into a trusted package | Familiar review, version control, and bootstrap independence | Scripts are executable code, permit arbitrary effects, drift after installation, and must reinvent target, approval, idempotency, receipt, and rollback semantics per harness | Rejected |
| Prose-only skills using harness-native tools | Safest universal fallback; Gate 1 shell arm made 0 wrong decisions in 25 controlled trials | Repeats four or five agent round trips, relies on careful ad hoc orchestration, and cannot guarantee a common receipt or replay contract | Retained as default and fallback |

The positive verdict is intentionally narrow. Prose plus native tools remains
superior for unbounded, exploratory, rare, or judgment-heavy work. Operation
packages are justified only when the workflow is repeated, high-friction,
deterministic, bounded, identity-sensitive, and testably repairable.

## Authority gate

An operation gains executable eligibility only through this chain:

```text
human-authored package proposal
  -> static schema/effect review
  -> owner countersignature and immutable digest
  -> explicit local install in inactive state
  -> explicit activation for a target profile
  -> current roadmap admission when mapped work is affected
  -> harness policy and user approval
  -> compiled-in local typed handler
  -> ADR 001 observation, bounded effect, ADR 002 receipt
```

Every link is required where applicable. No downstream link can manufacture a
missing upstream one.

These sources are permanently non-authoritative for execution:

- retrieved memories and injected neurons;
- assistant identity, self-model, or charter content;
- imported `SKILL.md` packages or registry reputation;
- generated or compiled prose;
- source-neuron authority level by itself;
- model confidence, tool availability, or prior successful execution;
- a package name or version without the locally activated digest.

Those artifacts may propose or explain an operation. Only a separately
reviewed operation package can select a handler, and only locally installed
code can register that handler. A package may never register code itself.

Revocation, digest drift, unknown publisher, absent activation, unavailable
admission, or unavailable approval fails closed. Memory recall may fail open
for a conversation; execution authority may not.

## Candidate package contract

The manifest is declarative and versioned. Its minimum fields are:

```json
{
  "schema": "corvus.operation-package/v1",
  "operationId": "local.service.ensure-running",
  "version": "1.0.0",
  "handler": "local.service.ensure-running.v1",
  "inputSchema": {
    "surface": {"type": "enum", "values": ["corvus-mind"]}
  },
  "allowedTargetProfiles": ["local-dev/corvus-mind"],
  "effectCapabilities": ["service.start.registered"],
  "preconditions": ["live-state.fresh", "tenant.matches", "admission.current"],
  "approvalPolicy": "harness-policy-plus-risk-tier",
  "idempotency": "caller-key-bound-to-package-input-target",
  "timeoutPolicyRef": "durability-operational-envelope profile",
  "repairHandler": "local.service.restore-prestate.v1",
  "receiptProfile": "local.service.ensure-running/v1",
  "publisher": "governed publisher identity",
  "digest": "immutable content digest",
  "lifecycleState": "inactive | active | superseded | revoked"
}
```

The manifest cannot carry a command or executable hook. The handler identifier
must already exist in the client's closed registry. Provider code translates a
typed effect into systemd, launchd, Windows Service Manager, container, or an
explicit `unsupported` result; it does not expose its command substrate.

### Typed inputs and target resolution

Inputs validate before target lookup. The pilot accepts one enum value only.
The local target registry maps the logical surface to expected tenant,
capabilities, supervisor instance, listener, readiness profile, and permitted
dependency subjects. Caller-supplied unit names, URLs, and paths are invalid.

Resolution consumes a fresh ADR 001 observation with maximum age zero. A
collision, wrong tenant, contradictory state, unavailable required probe, or
permission denial prevents effects. A foreign or pre-existing target is never
stopped as “repair.”

### Preconditions and dry-run

Preconditions include package activation, digest integrity, handler support,
target registration, tenant identity, current admission where required,
harness approval capability, and provider permission.

Dry-run executes resolution and live precondition probes, then returns a
bounded plan artifact and an ADR 002 receipt for `service.ensure_running.plan`.
That profile may prove that the plan was valid; it must not claim readiness,
effect completion, or durability. The plan expires with its observations and
must be recomputed before execution.

### Idempotency and replay

The caller supplies an idempotency key bound to the package digest, normalized
inputs, resolved target identity, caller/session, and risk profile.

A replay never repeats an already accepted effect merely because the same key
arrived. It also never returns an old green receipt as current truth. It makes
a fresh ADR 001 observation and emits a new verification receipt causally
linked to the immutable original. If the target changed after the original
success, replay returns non-success `CURRENT_STATE_CHANGED` without another
effect; a deliberate new convergence attempt needs a new key or an explicitly
authorized retry.

### Bounded effects

The plan enumerates the exact registered subjects it may touch and the maximum
number of effects. The pilot may start an inactive registered dependency and
the registered Corvus Mind service. It cannot install or edit a unit, reload
the manager, migrate the database, change configuration, kill an unknown PID,
touch a foreign tenant, or invoke arbitrary shell.

The receipt lists planned and executed effects separately. An effect omitted,
denied, or only requested is not recorded as completed.

### Timeout, cancellation, and retry

Deadlines use a monotonic clock and policy constants owned by
`durability-operational-envelope`. Observations obtained before timeout remain
in the ADR 002 attempt. A timeout is never converted to success by a live
process.

Cancellation is checked before each effect and during bounded waits. A
pre-effect cancellation performs no rollback. A post-effect cancellation
attempts the declared repair path and keeps the original `CANCELLED` attempt.

Retries append attempts and re-observe all action-bearing state. Only stable
error metadata may mark an error retryable. Wrong identity, collision,
revocation, digest mismatch, permission denial, and cancellation are not
automatic retries. The pilot has no automatic retry budget.

### Rollback and repair

Rollback is another typed operation and another attempt. It may restore only
state changed by the current invocation. `rolled_back` requires a live
revalidation of the declared pre-state. An attempted, timed-out, denied, or
failed rollback leaves the aggregate `failed` or `unknown` as ADR 002 requires.

Some operations have no safe automatic inverse. Their package must declare a
bounded repair or explicitly declare human remediation; absence of a safe
inverse prevents automatic execution at higher risk tiers.

### Evidence and audit lineage

Every invocation emits the ADR 002 envelope and its operation-specific claim
profile. It includes package and handler digests, target identity, caller and
approval references, admission revision, pre-state observation, planned and
executed effects, attempts, timestamps, monotonic latencies, freshness,
redactions, limitations, rollback links, and a reproducible verifier.

Raw stdout, stderr, journal data, HTTP bodies, and stack traces remain bounded,
redacted, session-local diagnostics. Durable audit holds structured summaries
and digests. Neither raw diagnostics nor runtime receipts enter neurons,
Capability Capsules, skills, or memory injection.

### Stable error classes

The pilot vocabulary is:

- `INVALID_INPUT`
- `PACKAGE_NOT_AUTHORIZED`
- `PACKAGE_REVOKED`
- `PACKAGE_DIGEST_MISMATCH`
- `HANDLER_UNSUPPORTED`
- `TARGET_NOT_REGISTERED`
- `TARGET_IDENTITY_MISMATCH`
- `UNIT_NAME_COLLISION`
- `PRECONDITION_UNKNOWN`
- `ADMISSION_REQUIRED`
- `ADMISSION_STALE`
- `PERMISSION_DENIED`
- `DEADLINE_EXCEEDED`
- `CANCELLED`
- `EFFECT_FAILED`
- `DURABILITY_FAILED`
- `ROLLBACK_FAILED`
- `CURRENT_STATE_CHANGED`
- `RECEIPT_VERIFICATION_FAILED`

Each class declares retryability and effect certainty. Unknown, partial,
blocked, timed-out, and failed cases retain their exact class.

## Package lifecycle

Packages move through:

```text
draft -> reviewed -> countersigned -> installed/inactive -> activated
      -> superseded or revoked -> archived
```

- Draft and review artifacts have no execution rights.
- Countersigning binds the reviewed content digest and publisher identity.
- Installation never activates a package.
- Activation names the local target profiles and allowed callers.
- Every update creates a new digest and repeats review and activation.
- Revocation disables new invocations before any descriptive reference is
  retracted.
- Archive preserves manifests, review decisions, receipts, and lineage without
  leaving a runnable package.

Signature format, key rotation, transparency, and tamper-evident audit storage
remain owned by `durability-operational-envelope` and the reproducible-release
work. Gate 3 requires immutable digest binding but does not guess a production
signing system.

## Disposable fixture

The protocol was frozen in `/tmp/corvus-gate3-protocol.md` before execution. A
stdlib-only mock manager and local interpreter in
`/tmp/corvus_gate3_fixture.py` implemented one closed handler and one registered
surface. It did not import the Corvus application, change a database row,
install a package, or touch a real unit. The package digest was:

```text
sha256:bb612720b50a64b97e8ee95602712aabfd420dc4c880dc585336ec953d783f56
```

The final verifier exited 0 with no failed invariants:

| Fixture | Final verdict | Preserved evidence |
| --- | --- | --- |
| first invocation | `succeeded / READY` | main start count exactly one |
| same-key replay | `succeeded / ALREADY_READY_REPLAY` | new linked fresh receipt; start count remained one |
| already ready, new key | `succeeded / ALREADY_READY` | no additional start |
| replay after planted service death | `failed / CURRENT_STATE_CHANGED` | no second effect; stale success not reused |
| partial prior state | `succeeded / READY` | only missing dependency started |
| unit/name collision | `failed / UNIT_NAME_COLLISION` | no effect |
| wrong tenant | `failed / TARGET_IDENTITY_MISMATCH` | no effect on foreign target |
| permission denial | `failed / PERMISSION_DENIED` | no effect and no invented rollback |
| readiness timeout | `rolled_back / TIMEOUT_ROLLED_BACK` | original attempt remained `timed_out`; pre-state restored |
| cancellation after effect | `rolled_back / CANCELLED_ROLLED_BACK` | original `CANCELLED` attempt retained |
| mid-operation failure | `rolled_back / EFFECT_FAILED_ROLLED_BACK` | original failure retained; pre-state reverified |
| planted rollback failure | `failed / ROLLBACK_FAILED` | final state remained not-ready, never called restored |
| death after readiness | `failed / DURABILITY_FAILED` | initial readiness did not imply durability |

The final research artifacts had these digests:

```text
protocol  3e30238b028d38b0dbf026c3f417edba34ba7b1dd50b98c084fb4a8c85798e24
fixture   88127c840acd2b8e264b1abdeb39c79ddac317fc4688578a10b76d69bbdb163b
opencode  689044fd9b0b9afe42a398eaed482e8ab19d2e441e1876f8053daeb2ffa703e7
```

The fixture digest above describes the final file used for the recorded
matrix. The package digest is computed from the candidate manifest itself.

## Failed attempts and counterevidence

The first full matrix exited 1 with three failures:

- a denied start was incorrectly labeled `rolled_back` even though no effect
  occurred;
- the same denial path wrote a synthetic rollback log;
- cancellation was collapsed into generic `EFFECT_FAILED`.

Root cause: the initial exception path did not distinguish effect certainty or
give cancellation a stable class. The research fixture was corrected so an
unchanged pre-state records no rollback, and cancellation remains explicit.
No production code was changed.

The frozen protocol also expected an exact same-receipt return for a same-key
replay. That expectation was rejected after applying ADR 001: an immutable old
receipt can prove the earlier effect, but it cannot prove current readiness.
The final design suppresses the duplicate effect while emitting a new, linked,
fresh verification receipt. A planted post-success stop returned
`CURRENT_STATE_CHANGED`, not green.

Counterevidence for the positive verdict remains important:

- Gate 1's careful prose-plus-shell workflow had 0 wrong actions in 25 trials,
  so operation packages have not shown a correctness advantage over a careful
  agent.
- The selected model adds a local client, package lifecycle, target registry,
  and per-platform providers.
- Checked-in systemd files remain useful source artifacts even though they are
  insufficient current-state or execution contracts.
- The mock proves contract discrimination, not production reliability.

## Guessed constants and unresolved assumptions

The disposable fixture guessed a 50 ms logical readiness deadline, 10 ms poll
interval, 20 ms durability window, 4 KiB diagnostic cap, and zero automatic
retries. None is a production default. `durability-operational-envelope` must
choose measured profile values.

Other unresolved assumptions:

- production package signature and publisher-key formats;
- crash recovery and receipt atomicity across client termination;
- concurrency and idempotency locking across processes;
- systemd, launchd, Windows, container, and remote-host provider behavior;
- safe handling of manager restarts and unit-file drift;
- noninteractive approval UX and accessibility;
- receipt persistence and retention policy;
- version negotiation between package, client, state provider, and receipt
  kernel;
- whether a start timeout should default to rollback for the real Corvus unit;
- real deadline, retry, and durability distributions.

## Commercial and product boundary

The pilot audience is Corvus maintainers and early design partners installing
the local memory provider. The commercial value is not “agents can run
scripts.” It is reducing repeated activation and support friction while giving
the user a portable, truthful receipt.

Workflows that may justify a separately reviewed operation package after the
pilot are:

- install, activate, verify, update, and revoke the local Corvus adapter;
- ensure the registered Corvus surface and its declared dependencies are
  running;
- run a bounded backup, migration, or restore only after the durability,
  approval, and recovery contracts settle;
- perform an explicitly requested, reversible repair identified by
  `corvus doctor`.

Workflows that remain harness-native or prose-only are:

- arbitrary coding, shell, git, browsing, testing, and deployment work;
- exploratory diagnosis and incident judgment;
- remote customer-infrastructure orchestration;
- secret retrieval, credential handling, or environment inspection;
- destructive database, filesystem, service, or tenant operations without a
  separately reviewed narrow package and recovery proof;
- memory promotion, proposal review, self-model changes, roadmap
  reconciliation, or policy judgment;
- model routing, context compression, scheduling, subagents, user channels,
  or an agent loop;
- arbitrary imported plugins, scripts, or `SKILL.md` execution.

If the narrow workflows do not measurably reduce setup time or support burden,
the operation-package program should stop after the pilot and prose plus native
tools should remain the product boundary.

## Downstream authority boundaries

### `mind-governed-skill-interoperability`

Imported and exported skills remain untrusted descriptive artifacts. They may
declare a required operation ID as data for preview, but cannot ship a handler,
activate a package, satisfy a publisher review, or invoke an operation.
Scripts inside a skill package remain scanned data. Skill trust and execution
authority are separate ledgers.

### `mind-harness-memory-provider-adapter`

The provider contract must not grow generic execution verbs. The host harness
continues to own the agent loop, tools, approvals, context, scheduling,
subagents, and channels. If an adapter needs local setup, it may depend on a
separate local operation client; the memory-provider interface itself remains
capture, recall, feedback, lifecycle, and receipts.

### `mind-corvus-doctor`

Doctor consumes ADR 001 state and ADR 002 receipts. It diagnoses and proposes
bounded remediation. It must never turn a diagnosis into an automatic repair.
An approved repair references a separately activated operation package and
produces its own receipt.

These boundaries supply authority without starting any downstream record.

## Rejected alternatives

### Typed MCP as the privileged execution surface

Rejected. Tool registration is not execution authority, the current backend
dependency creates a bootstrap cycle, and a privileged stdio server would
expand the local attack surface. A future MCP facade may request an operation
from an already installed client but may not bypass package activation,
admission, approval, or local permission.

### Generated checked-in scripts

Rejected. Reviewable source code is valuable, but generated scripts are still
arbitrary executable code. The observed installed-versus-repository systemd
drift shows that version control alone does not resolve current target identity
or installed state. Scripts also fragment receipts and rollback behavior.

### Prose-only for every workflow

Rejected as the universal answer, retained as the baseline and fallback. It is
correct when carefully executed and is superior for unbounded work, but it
leaves repeated installation and recovery flows bespoke and receipt-poor.

### Compile trusted skills directly into operations

Rejected categorically. Evidence-gated memory is evidence for a lesson, not
authorization for machine effects. No trust score, assistant identity, skill
compiler output, or imported provenance can cross that wall organically.

## Bounded follow-on

Evidence warrants one separately reviewable implementation record for the
single local `ensure_running(surface="corvus-mind")` pilot. It must depend on
the production forms of ADR 001 and ADR 002 and the operational-envelope
policy. It must implement:

1. one versioned package schema with no arbitrary code fields;
2. one compiled-in handler and one registered local target profile;
3. digest-pinned install/activate/revoke states with explicit owner approval;
4. plan-only dry-run and fresh target resolution;
5. idempotent effect suppression plus fresh linked replay verification;
6. harness/admission/OS permission checks that cannot self-grant;
7. bounded cancellation, timeout, repair, and ADR 002 receipt emission;
8. trust, tamper, first-call, replay, stale-replay, partial-state, collision,
   wrong-identity, permission, timeout, cancellation, mid-failure, rollback,
   rollback-failure, durability, redaction, and unsupported-provider tests;
9. a rollback plan that removes this registration and package without changing
   skills, memories, the backend MCP, or unrelated services.

It must not implement a generic interpreter, external package marketplace,
arbitrary operation loader, production skill compiler, remote execution plane,
privileged MCP tool, or agent runtime.

## Acceptance check

- One verdict: governed declarative operation packages interpreted locally and
  bound to compiled-in typed handlers.
- Four execution models compared; three rejected as execution owners.
- Memories, identity, self-model, imported skills, and compiled prose cannot
  acquire execution rights.
- Inputs, target resolution, preconditions, dry-run, idempotency, bounded
  effects, timeout, cancellation, retry, repair, errors, evidence, approval,
  and lifecycle are explicit.
- ADR 001 and ADR 002 remain the only state and receipt contracts.
- All prescribed planted cases and additional stale-replay, rollback-failure,
  and post-readiness-death cases retained non-success accurately.
- Commercial and non-executable boundaries are explicit.
- The three downstream records receive authority boundaries without starting.
- No production executor, compiler, operation MCP, service change, agent loop,
  or downstream implementation was created.

Gate 3 ends here.
