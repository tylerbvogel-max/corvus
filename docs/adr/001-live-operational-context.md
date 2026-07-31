# ADR 001: Keep live operational context local, typed, and separate from memory

- Status: accepted by research gate
- Date: 2026-07-30
- Roadmap record: `mind-live-operational-context-research`
- Session: `019fb4b7-f943-7a83-bba9-578965665291`
- Scope: research decision only; this ADR ships no production subsystem

## Decision

Corvus should expose a dedicated, read-only **local operational-state MCP
surface** beside durable memory.

The local surface will accept only registered subject identifiers, execute
allowlisted probes, and return typed point-in-time evidence. It will not accept
arbitrary commands, URLs, process queries, or paths. Shell tools remain the
probe substrate and fallback for unsupported environments; they are not the
primary agent contract. Capability Capsules remain durable capability and
memory projections and must not carry runtime observations.

An operational observation is never a fact about the present after the probe
finishes. Action-bearing workflows must re-observe at the decision or execution
boundary. Remembered service conventions may define what to inspect, but they
must appear as expectations, never as observations.

## Why

The 2026-07-30 startup specimen showed that durable memory correctly recalled
the Corvus service topology but could not establish current state. The agent
needed separate systemd, listener, process, HTTP, tenant, schema, timer, and
proxy probes.

The live specimen also produced contradictions that a single green status
would have hidden:

- `corvus-mind.service` was active with PID 273 and port 8005 bound; `/health`
  returned 200 and `/tenant` returned `corvus-mind`.
- Port 8004 successfully proxied `/health` and `/tenant` to Corvus Mind while
  `master-corvus-kernel-final.service` reported inactive with no loaded unit.
- The application could query its database while
  `select version_num from alembic_version` failed because the relation did not
  exist. Schema authority therefore cannot be inferred from generic health.
- The installed user unit and the repository unit differed: the repository
  version included an Alembic `ExecStartPre`; the installed unit did not.

These are not memory failures. They are evidence that durable conventions and
perishable observations have different lifecycles and trust semantics.

## Frozen experiment

The protocol was frozen before planting fixtures:

| Fixture | Expected decision |
|---|---|
| healthy | use |
| loaded but stopped | start |
| active but HTTP 503 not-ready | diagnose |
| healthy endpoint with wrong tenant | do-not-use |
| service manager permission-denied while other probes succeed | abstain |

Each fixture ran five times through two arms:

1. Shell-only comprehensive discovery: sequential supervisor, listener,
   process, health, and tenant probes.
2. Typed-surface prototype: one logical agent query, with the same allowlisted
   probes executed concurrently and internal probe count disclosed.

Tool-call count is measured at the agent boundary. Internal probes are reported
separately so aggregation is not treated as free work. Elapsed time includes
the coding-harness command round trips and is not a production latency
prediction.

## Results

| Fixture | Shell calls | Typed calls | Shell median | Typed median | Wrong actions |
|---|---:|---:|---:|---:|---:|
| healthy | 5 | 1 | 1,936 ms | 910 ms | 0/10 |
| stopped | 4 | 1 | 1,668 ms | 665 ms | 0/10 |
| active-not-ready | 5 | 1 | 1,933 ms | 936 ms | 0/10 |
| wrong-tenant | 5 | 1 | 1,918 ms | 893 ms | 0/10 |
| inaccessible-manager | 5 | 1 | 1,960 ms | 957 ms | 0/10 |

Across 25 trials per arm:

- Wrong-action rate was 0% in both arms.
- Evidence completeness was 100% in both arms.
- The typed contract reduced logical agent calls by 75% to 80%.
- Fixture median elapsed time fell by 51% to 60%.
- The typed arm still performed four or five internal probes; it improved
  orchestration and semantics, not the physical cost of observation.

This is deliberately modest evidence. The experiment did **not** show a
correctness advantage over a careful shell workflow. It showed equivalent
decisions with fewer agent round trips and a portable failure vocabulary.

### Representative planted receipts

Healthy:

```text
systemd: LoadState=loaded ActiveState=active MainPID=20337
listener: 127.0.0.1:18051 pid=20337
health: {"status":"ready"} HTTP 200
tenant: {"tenant_id":"corvus-mind"} HTTP 200
decision: use
```

Stopped:

```text
systemd: LoadState=loaded ActiveState=inactive MainPID=0
listener: absent
health and tenant: connection refused
decision: start
```

Active but not ready:

```text
systemd: ActiveState=active MainPID=20338
listener: 127.0.0.1:18052 pid=20338
health: {"status":"not-ready"} HTTP 503
tenant: {"tenant_id":"corvus-mind"} HTTP 200
decision: diagnose
```

Wrong tenant:

```text
systemd: ActiveState=active MainPID=20342
listener: 127.0.0.1:18053 pid=20342
health: {"status":"ready"} HTTP 200
tenant: {"tenant_id":"corvus-aero"} HTTP 200
decision: do-not-use
```

Inaccessible manager:

```text
systemd: Failed to connect to bus: Operation not permitted
listener, process, health, tenant: independently healthy for corvus-mind
typed status: permission-denied
decision: abstain
```

### Capsule staleness receipt

A healthy observation was snapshotted at `2026-07-31T03:35:59.940Z` with the
initial guessed TTL of 15 seconds. The service was stopped. At
`2026-07-31T03:36:12.451Z`, 12.511 seconds later:

```text
cached capsule status: ready, still within guessed TTL
live systemd: inactive/not-found, MainPID=0
live listener: absent
live health: connection refused
```

The 15-second hypothesis is rejected. A TTL bounds age, not truth; a state
transition can invalidate an observation immediately. Capability Capsule
transport makes the ambiguity worse because export and consumption are
separate events.

## Minimal typed contract

```json
{
  "schema": "corvus.operational-state.v1",
  "subject": {
    "id": "corvus-mind",
    "kind": "local-service"
  },
  "expectation": {
    "source": "durable-convention",
    "tenant_id": "corvus-mind",
    "listener": "127.0.0.1:8005"
  },
  "observation": {
    "status": "ready",
    "observed_at": "RFC3339 timestamp",
    "expires_at": "same as observed_at for action-bearing use",
    "freshness": "point-in-time",
    "source": "live-local-probes",
    "remembered_convention": false
  },
  "evidence": [
    {
      "probe": "supervisor",
      "outcome": "observed",
      "detail": {
        "active_state": "active",
        "main_pid_matches_listener": true
      }
    },
    {
      "probe": "readiness",
      "outcome": "observed",
      "detail": {
        "http_status": 200
      }
    }
  ],
  "contradictions": []
}
```

Required statuses:

- `ready`: all required probes agree and tenant identity matches.
- `stopped`: the registered supervisor is loaded but inactive and no listener
  exists.
- `not-ready`: process/listener exists but the readiness contract rejects use.
- `wrong-tenant`: a reachable target identifies as another tenant.
- `unknown`: probes completed but evidence is insufficient or the subject is
  not registered.
- `unreachable`: an expected source or endpoint could not be contacted.
- `permission-denied`: the observer lacks authority for a required probe.
- `contradictory`: authoritative probes disagree, including PID/listener or
  supervisor/listener disagreement.

`unknown`, `unreachable`, `permission-denied`, and `contradictory` are terminal
for action selection: the caller abstains rather than inventing current state.

## Freshness rules

1. Every probe result carries `observed_at`; the aggregate carries the oldest
   required-probe timestamp.
2. Action-bearing state has a default maximum age of zero. Re-probe immediately
   before a start, stop, restart, migration, write, or tenant-sensitive call.
3. A caller may request a non-zero display age, but that is explicitly
   `cached-observation`, never `current`. No display cache constant is accepted
   by this ADR.
4. Remembered conventions carry their own `verified_at` and source, but never
   satisfy a live probe.
5. Partial failures remain visible alongside successful evidence. One failed
   probe does not erase the others.

## Security and tenancy boundary

The local client owns:

- systemd or platform-equivalent supervisor inspection;
- listener-to-process binding;
- local readiness/liveness calls;
- local schema-authority checks;
- local timer/job inspection;
- frontend-to-backend proxy checks.

The cloud may receive only the typed, redacted observation when policy and task
require it. By default, raw evidence remains local.

The contract must never expose:

- environment variables or secrets;
- usernames;
- full command lines or arbitrary process listings;
- unrelated processes or listeners;
- unrestricted filesystem paths;
- memory content.

Safe process evidence is limited to registered subject ID, PID equality,
executable basename, expected listener, and redacted exit/status codes. Tenant
identity is mandatory. A wrong-tenant result is never degraded to generic
health.

The MCP tool accepts registered subject IDs, not shell commands or arbitrary
URLs. Platform probe providers are implementation details behind the same
contract.

## Architecture comparison

| Design | Correctness | Latency | Portability | Security | Maintenance |
|---|---|---|---|---|---|
| Dedicated local read-only MCP | Explicit failures and contradictions; 0/25 wrong actions in fixture | One agent round trip; 51–60% lower fixture median | Stable contract; provider required per OS | Strong allowlist and redaction boundary | Moderate, bounded registry and provider tests |
| Capability Capsule extension | Can carry typed fields but immediately becomes stale; failed 12.511-second transition test | Cheap to consume, expensive/ambiguous to refresh | Transportable | Risks exporting machine state and confusing durable trust | Low code change, high semantic debt |
| Shell-only | 0/25 wrong actions when comprehensive | Four or five agent round trips | Harness and OS specific | Easy to over-collect command lines, env, and unrelated processes | No Corvus subsystem, repeated bespoke agent logic |

## Relationship to `durability-operational-envelope`

`durability-operational-envelope` owns producing trustworthy signals:

- separate liveness and readiness endpoints;
- schema authority;
- structured logs, metrics, SLOs, alerts, and runbooks;
- timer/job receipts and last-success state;
- deployment verification.

The follow-on implementation owns consuming and normalizing those signals:

- local subject registry and probe providers;
- typed observation/failure/contradiction contract;
- MCP read surface;
- allowlist, redaction, and tenant checks;
- frozen fixtures proving observation semantics.

The follow-on must consume the operational envelope when available and must not
reimplement its telemetry, alerts, job receipts, or readiness internals.

## Rejected alternatives

### Extend Capability Capsules

Rejected. Capsules are deterministic, signed projections for memory and
capability transport. Adding runtime state would make a valid signature appear
to certify current truth and would export machine-local detail across the wrong
boundary. The immediate-stop fixture showed that even a still-within-TTL
capsule can be wrong.

### Leave discovery entirely to shell tools

Rejected as the primary interface, retained as fallback. A careful shell
workflow was correct in the controlled fixtures, which is important dissenting
evidence. However, it requires repeated harness-specific orchestration and has
no enforced redaction, tenant, failure, or contradiction vocabulary.

## Limitations and failed attempts

- The first Python prototype ran inside a restricted command sandbox. D-Bus,
  netlink, and loopback access failed with `Operation not permitted`; its
  parallel path then hung until interrupted. The successful comparison used
  direct harness-approved probes. This is evidence that capability detection
  and bounded probe timeouts are acceptance requirements.
- Five repetitions per fixture are a bounded engineering sample, not a
  statistical reliability claim.
- Only Linux/systemd was exercised. macOS, Windows, containers, and remote
  clients need explicit provider or unsupported-state behavior.
- The fixtures used small local HTTP servers, not injected faults inside the
  production Corvus process.
- Both arms achieved the same correctness and evidence completeness. The
  positive verdict rests on reduced agent round trips, normalized semantics,
  redaction, and portability—not demonstrated correctness lift.
- The initial 15-second TTL was a guessed constant and was falsified. No
  positive cache TTL is accepted for action-bearing observations.

## Bounded follow-on

Create one implementation record for a local read-only operational-state MCP
surface. It is accepted only when:

1. The tool accepts registered subject IDs only; arbitrary commands and URLs
   are impossible.
2. The v1 contract implements every status and point-in-time freshness rule in
   this ADR.
3. Linux/systemd probes cover supervisor, listener/PID binding, readiness,
   tenant identity, schema authority, timers/jobs, and configured proxy health.
4. Secrets, usernames, full command lines, paths, unrelated processes, and
   memory content are absent from outputs and logs.
5. Healthy, stopped, not-ready, wrong-tenant, permission-denied, unreachable,
   unknown, and contradictory fixtures pass.
6. The tool re-probes before action-bearing use and does not persist
   observations into neurons or Capability Capsules.
7. Existing shell discovery remains a documented fallback.
8. Scope consumes, but does not duplicate,
   `durability-operational-envelope`.

