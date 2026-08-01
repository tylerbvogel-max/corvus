# ADR 002: One receipt envelope, operation-specific claim profiles

- Status: Accepted research verdict (Gate 2)
- Date: 2026-07-31
- Session: `019fb4b7-f943-7a83-bba9-578965665291`
- Roadmap: `corvus-long-horizon`
- Record: `mind-self-validating-operation-receipts-research`
- Settled input: [ADR 001: Live operational context](001-live-operational-context.md)

## Verdict

Corvus should use **one versioned operation-receipt envelope with
operation-specific claim profiles and probe definitions**.

The envelope owns shared semantics: target identity, attempts, causality,
timestamps, freshness, terminal status, latency, redaction, limitations,
diagnostic retention, and the reproducible verifier. A profile owns which
claims are required for a particular operation and what observations satisfy
them. Existing Action Bus, proposal, roadmap-reconciliation,
architecture-conformance, and deployment evidence remain domain artifacts;
they are referenced or embedded as evidence for claims where they genuinely
prove one. They do not become competing top-level success contracts.

Command acceptance is not readiness. Process liveness is not application
readiness. Application readiness is not dependency readiness. Correct tenant
and capability identity is not an optional annotation. A healthy frontend is
not an end-to-end success when its API proxy is broken. Initial readiness is
not durable success.

The candidate is therefore a **hybrid contract**, not a generic bag of
booleans and not a family of unrelated receipt schemas.

## Why Gate 1 constrains this decision

ADR 001 established that operational observations are point-in-time,
read-only facts; action-bearing decisions require a live observation with a
maximum age of zero; and `wrong-tenant`, `unknown`, `unreachable`,
`permission-denied`, and contradictory states must survive without being
rounded into readiness. This receipt contract records those observations and
their causal order. It does not move runtime state into neurons, capability
capsules, or durable memory.

## Claim ladder

Every operation profile selects from the following ordered layers. Passing a
lower layer never implies a higher one.

| Layer | Claim | Representative positive evidence | What it does not prove |
| --- | --- | --- | --- |
| 1 | Command acceptance | supervisor or API accepted the request and assigned identity | process exists or work began |
| 2 | Process liveness | expected PID/unit is active | listener, usable app, or correct tenant |
| 3 | Application readiness | expected listener and application-level readiness probe pass | dependencies or end-to-end path |
| 4 | Dependency readiness | required database, proxy, queue, or downstream service passes its profile probe | correct tenant/capability or user path |
| 5 | Tenant/capability identity | live response matches the expected tenant, service, capability, and instance constraints | useful user action succeeds |
| 6 | End-to-end user path | the named user-visible operation completes against the intended target | it stays healthy afterward |
| 7 | Short-window durability | required lower claims still pass after the profile's observation window | long-term availability |

Profiles may omit irrelevant layers, but they may not silently equate them.
A deployment profile normally requires all seven. A pure transactional
mutation may not require a listener, but it still needs domain-state and
identity claims beyond Action Bus acceptance.

## Existing receipt inventory

### Action Bus

`ActionResult` provides an action ID, bus state, payload, audit object, and
error. It is a strong command/transaction receipt and supports idempotency and
parent-child causality. It is not a complete operation receipt.

The handler contract creates a sharp counterexample: `neuron.refine` can
return a domain audit containing `skipped`, while the bus marks any normally
returned handler transaction `applied`. Thus `applied` means the handler and
its transaction completed; it does not universally mean the requested domain
effect occurred.

A live read-only query on 2026-07-31 found 3,389 action rows, all in state
`applied`, and zero applied rows whose `result_json` contains `skipped`. That
is useful counterevidence: the semantic gap is present in code but was not
observed in persisted history. The first inspection query also failed because
it guessed a nonexistent `payload` column; `\d actions` showed the actual
column is `input_json`. Both the failed query and correction are retained as
research limitations.

Receipt mapping: Action Bus state becomes a command/transaction claim. Its
operation-specific audit payload remains attached evidence. A profile must
add any required state, identity, projection, user-path, and durability
claims.

### Proposal application

Proposal application composes a root action and child Action Bus mutations in
one database transaction. Child failures abort the transaction, so it is
strong evidence of atomic graph mutation. Cache and projection refreshes run
after commit, however, so proposal `applied` does not prove those derived
views or an end-to-end recall path.

Receipt mapping: retain the root/child action chain and transaction outcome;
add post-commit projection or user-path claims when the operation promises
them.

### Roadmap reconciliation

`corvus.roadmap-reconciliation/v1` already carries disposition,
`verificationPassed`, claims, evidence, limitations, disclosures, verifier,
and acceptance metadata. It is a human-accepted settlement record, not a raw
operational probe receipt. Its evidence entries are not typed per probe,
freshness window, target, latency, or attempt.

Receipt mapping: a reconciliation receipt references the final operation
receipt IDs and summarizes the accepted verdict. The generic receipt must not
replace or duplicate roadmap reconciliation.

### Architecture conformance

The architecture conformance artifact is intentionally domain-specific. It
contains schema and source-state metadata, coverage totals, freshness,
violations, and evidence checks, and its strict verifier has a meaningful
nonzero exit path.

Receipt mapping: preserve the conformance artifact and digest as evidence for
an `architecture.conformance` claim. The operation envelope supplies target,
attempt, lifecycle, redaction, retention, and terminal semantics around it.

### Deployment evidence

No canonical, versioned deployment-receipt schema was found in the repository
during Gate 2. Current deployment evidence is distributed across supervisor
state, listeners, HTTP probes, logs, and hand-authored verification receipts.
That absence is evidence, not permission to invent a second contract.

Receipt mapping: a deployment operation uses the common envelope with a
deployment profile requiring the relevant seven layers. Gate 3 playbooks
should emit that profile rather than define their own success schema.

## Frozen planted-failure protocol

Before execution, `/tmp/corvus-gate2-protocol.md` froze the candidate verdict,
status rules, expected outcomes, ports, and guessed constants. An isolated
stdlib HTTP server at `/tmp/corvus_gate2_fixture_server.py` supplied planted
targets. No production service, database row, or application code was
changed.

Research constants were deliberately guessed for discrimination:

- listener deadline: 2 seconds;
- cached observation maximum age: 5 seconds;
- short durability window: 3 seconds;
- proposed raw diagnostic cap: 16 KiB per probe.

These are not production defaults. Per-operation calibration and tenant
retention policy remain unresolved.

## Planted results

| Fixture | Observed evidence | Receipt verdict |
| --- | --- | --- |
| Active process, closed port | `corvus-gate2-closed-final` stayed `active/running` with PID 27828; port 18101 had no listener at the first probe or after 2.2 seconds | `timed_out` — acceptance and liveness passed; readiness never arrived by the frozen deadline |
| Wrong tenant | Unit and listener were live; `/health` returned HTTP 200 in 4.083 ms with `status=ready` but tenant `nourish-together`, not `corvus-long-horizon` | `failed` — explicit, nonretryable identity mismatch for this attempt |
| Healthy frontend, broken proxy | Frontend unit was active; HTML returned 200 in 2.258 ms; required `/api/ping` returned 502 `proxy_failed` in 5.237 ms | `partial` — independently useful frontend claims passed, required end-to-end dependency path failed |
| Stale cached response | HTTP 200 returned the expected service and tenant in 2.352 ms, but `observed_at=1000000000`; measured age was 785,501,434 seconds against the frozen 5-second maximum | `unknown` — stale evidence cannot establish current readiness and no fresh replacement was obtained |
| Permission-denied probe | Live `/health` passed in 2.235 ms; the required protected identity read exited 1 with `Permission denied` | `unknown` — the app may be ready, but required identity evidence was unavailable |
| Service death after readiness | Attempt 7 returned HTTP 200 with correct target in 1.190 ms; the supervisor then delivered a planted SIGKILL; within the durability window the unit was `failed`/`Result=signal`, port 18106 was closed, and HTTP returned 000 | `failed` — initial readiness passed and short-window durability failed |

### Retry and counterevidence receipts

Six earlier durability attempts did **not** establish post-readiness death.
Timer-based targets exited before the probe crossed its execution boundary,
and several `curl --...` invocations ran in the restricted probe context while
`ss` could observe the host listener. Those attempts are `unknown` or failed
preconditions, not evidence of durability failure. The seventh attempt used
the authorized `curl -sS` probe, positively observed readiness, then planted
SIGKILL. The final receipt links all attempts and preserves the earlier
failures rather than overwriting them.

This counterevidence is part of the verdict: verifier identity and permission
context belong in the receipt, because the same textual probe can have a
different observation boundary.

## Candidate schema

The following is the normative Gate 2 candidate shape. `v0-candidate` is not a
production compatibility promise.

```json
{
  "schema": "corvus.operation-receipt/v0-candidate",
  "receiptId": "implementation-selected immutable id",
  "operation": {
    "type": "service.start",
    "profile": "deployment.local-service",
    "profileVersion": "candidate-1",
    "requestId": "optional upstream idempotency or action id"
  },
  "target": {
    "environment": "local-dev",
    "tenant": "corvus-long-horizon",
    "service": "corvus-mind",
    "instance": "systemd-user:corvus-mind.service",
    "capabilities": ["memory-recall"]
  },
  "lifecycle": {
    "requestedAt": "RFC3339 UTC timestamp",
    "acceptedAt": "RFC3339 UTC timestamp or null",
    "firstReadyAt": "RFC3339 UTC timestamp or null",
    "completedAt": "RFC3339 UTC timestamp",
    "durabilityCheckedAt": "RFC3339 UTC timestamp or null"
  },
  "attempts": [
    {
      "attempt": 1,
      "trigger": "initial | retry | rollback | durability-check",
      "startedAt": "RFC3339 UTC timestamp",
      "endedAt": "RFC3339 UTC timestamp",
      "status": "succeeded | partial | failed | unknown | timed_out | rolled_back",
      "claims": [
        {
          "id": "application.readiness",
          "layer": "command_acceptance | process_liveness | application_readiness | dependency_readiness | target_identity | end_to_end | durability",
          "required": true,
          "status": "pass | fail | unknown | not_run",
          "probe": {
            "kind": "http | supervisor | socket | database | action | artifact | user_path",
            "argv": ["curl", "-sS", "--max-time", "1", "http://127.0.0.1:8005/health"],
            "timeoutMs": 1000,
            "verifierIdentity": "local-user/session boundary",
            "profileInputDigest": "sha256 digest"
          },
          "observed": {
            "value": {"httpStatus": 200, "status": "ready"},
            "at": "RFC3339 UTC timestamp",
            "source": "live probe",
            "targetIdentity": "identity observed by this probe"
          },
          "freshness": {
            "maxAgeMs": 0,
            "ageMs": 0,
            "status": "fresh | stale | indeterminate"
          },
          "latencyMs": 1.19,
          "evidence": {
            "digest": "sha256 digest of redacted bounded evidence",
            "durableRef": "optional governed artifact reference",
            "ephemeralRef": "/tmp/session-scoped diagnostic reference or null"
          },
          "redactions": ["authorization header removed"],
          "limitations": []
        }
      ],
      "logs": {
        "status": "captured | unavailable | permission_denied | not_needed",
        "excerptDigest": "sha256 digest or null",
        "ephemeralRef": "session-scoped path or null",
        "truncated": false,
        "redactions": []
      },
      "limitations": []
    }
  ],
  "verdict": {
    "status": "succeeded | partial | failed | unknown | timed_out | rolled_back",
    "reasonCode": "stable machine-readable reason",
    "requiredClaims": ["claim ids"],
    "passedClaims": ["claim ids"],
    "failedClaims": ["claim ids"],
    "unknownClaims": ["claim ids"]
  },
  "causality": {
    "parentReceiptId": "optional operation receipt",
    "previousAttemptReceiptId": "optional prior receipt",
    "rollbackReceiptId": "optional rollback operation receipt",
    "domainReceiptRefs": ["action:3530", "architecture:sha256:..."]
  },
  "retention": {
    "durableClass": "audit-summary",
    "diagnosticClass": "ephemeral-session",
    "policy": "tenant policy id",
    "expiresAt": "timestamp or null"
  },
  "redactions": [],
  "limitations": [],
  "verifier": {
    "name": "profile runner",
    "version": "immutable version",
    "profileDigest": "sha256 digest",
    "reproduce": {
      "entrypoint": ["bounded", "typed", "argv"],
      "requiredInputs": ["explicit non-secret inputs"],
      "secretInputs": ["names only, never values"],
      "expectedExitMapping": {"0": "profile evaluated; inspect receipt verdict", "nonzero": "verifier fault"}
    }
  }
}
```

### Schema invariants

1. The final verdict is computed from the selected profile's required claims;
   callers cannot supply `succeeded` independently.
2. `succeeded` requires every required claim to be fresh and `pass`.
3. A required `unknown` claim prevents success. It does not become `fail`.
4. `partial` requires at least one passed, independently useful claim and at
   least one required failed claim. Profiles declare whether partial has
   operational meaning.
5. `timed_out` requires a recorded deadline and preserves all observations
   obtained before it.
6. Retry attempts append. They do not replace or mutate prior attempts.
7. `rolled_back` requires a linked rollback attempt and positive revalidation
   of the declared pre-state. A requested or attempted rollback is not enough.
8. Failure logs never change a failed claim into success. Unavailable or
   permission-denied logs are explicit limitations.
9. Wall timestamps are UTC RFC3339 for audit ordering; latency and deadline
   measurements use a monotonic clock. Exact clock source remains an
   implementation decision.
10. Secrets, environment dumps, database URLs, tokens, and unbounded logs are
    prohibited from durable or memory-tier evidence.

## Timeout, eventual readiness, retry, and rollback

An operation may move through `accepted` and `started` lifecycle facts without
having a terminal success status. If readiness arrives before the recorded
deadline, the successful later attempt records `firstReadyAt` and the earlier
negative probes remain. If the deadline expires first, the attempt is
`timed_out`, even when the process remains live.

Retries are separate attempts with a trigger and causal link. An eventual
success can make the overall operation `succeeded` only if the profile permits
retry and the final required durability window passes. The receipt still
discloses the number and outcomes of prior attempts.

Rollback is a new operation, not a label attached to a failure. The original
operation remains failed. The aggregate may say `rolled_back` only when the
rollback receipt proves the declared pre-state was restored. If restoration
or its verification fails, the aggregate remains `failed` with an explicit
rollback limitation.

## Durable audit versus ephemeral diagnostics

Durable audit evidence contains the structured receipt, bounded redacted
observations, digests, governed references, verifier/profile identity, and
limitations. It is suitable for an audit store or a roadmap reconciliation
reference.

Ephemeral diagnostics contain raw stdout/stderr, journal excerpts, stack
traces, request/response bodies, and process details needed to debug the
attempt. They remain local to the session, are redacted before any excerpt is
promoted, are bounded before capture, and expire under tenant policy. The
research harness used `/tmp` for this purpose.

Neither runtime observations nor raw receipts belong in neurons, capability
capsules, or memory injection. A later evidence-gated lesson may cite a stable
contract conclusion, but it must not carry volatile state or secrets.

The 16 KiB research cap is guessed. Gate 2 does not choose a production byte
limit or retention duration because there is no measured log distribution or
settled tenant policy.

## Rejected alternatives

### One generic receipt with generic success fields

Rejected. A generic envelope cannot decide whether a proposal requires cache
refresh, a deployment requires a proxy path, or architecture conformance
requires zero violations. Without profiles it becomes a permissive bag of
claims and allows lower-layer success to masquerade as user success.

### Operation-specific receipt schemas only

Rejected. Action, proposal, deployment, and conformance receipts would drift
on status, freshness, retry, rollback, identity, redaction, and retention.
Cross-operation tooling could not distinguish `unknown` from `failed`
reliably.

### Boolean `success`

Rejected. It erases partial progress, unavailable evidence, timeouts,
rollback state, and failures after initial readiness.

### Raw logs as the receipt

Rejected. Logs are unbounded, frequently secret-bearing, verifier-dependent,
and do not provide typed claims or freshness semantics.

### Runtime receipts in Corvus memory

Rejected by ADR 001 and the memory-integrity boundary. Runtime evidence is
volatile operational state, not institutional knowledge.

## Mapping to adjacent roadmap work

### `durability-operational-envelope`

That record should own policy choices this research intentionally leaves
open: per-profile deadlines and durability windows, diagnostic size and
retention policy, tamper-evidence/signing requirements, clock requirements,
and the durable audit store. It should adopt this envelope and status algebra,
not define another receipt contract.

### Gate 3

Gate 3 should define executable operational playbooks as operation-specific
profiles and typed probe recipes that emit this envelope. Its success criteria
must evaluate required claims, including target identity and durability. Gate
3 must not invent a playbook-specific terminal status or evidence store.

### Bounded implementation record

Evidence warrants one follow-on implementation record: freeze a versioned
JSON Schema plus a pure receipt validator/aggregator and conformance fixtures
for the six planted failures. It must not implement probe execution,
deployment automation, UI, persistence, or Gate 3 playbooks. It should depend
on this Gate 2 verdict and `durability-operational-envelope`, because policy
constants and storage boundaries must settle before production compatibility
is promised.

## Unresolved assumptions and limitations

- Receipt ID format is unresolved; immutability and collision resistance are
  required, but UUID versus ULID is not decided.
- Authoritative tenant/capability identity sources are profile-specific and
  remain to be enumerated by Gate 3.
- Tamper evidence, signatures, and signer identity are deferred to
  `durability-operational-envelope`.
- Production deadlines, durability windows, diagnostic byte caps, and
  retention durations are unresolved. Every numeric value used in Gate 2 was
  guessed and labeled.
- The research HTTP fixtures model the required distinctions but do not
  benchmark production load, concurrent deployments, network partitions, or
  multi-host clock skew.
- The Action Bus semantic gap was demonstrated by code inspection, not by a
  persisted `applied` + `skipped` row; the live database contained none.
- No canonical deployment receipt existed to test for backward compatibility.
- Permission and sandbox boundaries affected the durability verifier itself.
  The final causal receipt succeeded only after the verifier identity and
  authorized probe form were explicit.

## Acceptance check

- One architecture verdict: **generic envelope plus operation-specific claim
  profiles**.
- Seven operational layers remain distinct.
- Candidate schema covers claims, probes, observations, timestamps, target
  identity, freshness, status, latency, redaction, limitations, retention,
  causality, and a reproducible verifier.
- All six prescribed failures were planted and produced the frozen non-success
  verdicts.
- Partial, timeout, retry, eventual readiness, rollback, failure logs, and
  verifier faults have explicit semantics.
- Durable audit evidence is separated from ephemeral diagnostics; secrets and
  unbounded logs are excluded from memory.
- Adjacent durability work and Gate 3 consume this contract without competing
  schemas.

Gate 2 ends here. No production verification framework or Gate 3 playbook was
implemented.
