# Request audit logging

`backend/app/middleware/audit.py` provides best-effort records in `audit_log`.
It is an operational aid, not a complete security audit or a certification.
NIST, CMMC, SOC 2 and FedRAMP references elsewhere are framework context; they
do not certify this implementation or its deployment.

## Coverage and ordering

The middleware records POST, PUT, PATCH and DELETE requests that reach it and
produce a response, including HTTP error responses. It excludes `/corvus/frame`,
`/health` and `/corvus/latest-frame`. GET, HEAD and OPTIONS are not recorded;
there is no sensitive-read audit policy implemented here. Rejections in outer
middleware may never reach this layer. Unhandled downstream exceptions and
failures while reading the body produce no record here. Streaming responses
are recorded when their status is available, before full stream consumption;
later stream failures are not reflected in the record.

Records contain method, path (up to 500 characters, no query string), status,
client IP, user agent (up to 500 characters), body summary and elapsed handler
time. HTTP failures use `HTTP <status>` as the error detail, not response text.
The elapsed time excludes body reading and audit persistence.

## Body privacy contract

Nonempty summaries are valid JSON with the input byte count and either a
`body` shape or an `omitted` reason. Empty bodies produce a null database field.
The request bytes passed to the application are unchanged.

- Dictionaries and lists are traversed recursively. Recognized secret fields
  become `[REDACTED]` without inspecting their contents. Names are case-insensitive
  and hyphens normalize to underscores. The explicit set is `password`, `secret`,
  `token`, `api_key`, `apikey`, `authorization`, `access_token`, `refresh_token`,
  `client_secret`, `private_key`, `cookie` and `set_cookie`.
- All other keys become positional names such as `field_0`; all scalar values
  become type markers. This deliberately loses labels and values, because even
  an ordinary `message` field or a dictionary key can contain private material.
  Unknown credential names remain safe through this omission policy.
- Top-level scalar JSON is omitted with `non_container_json`. Invalid UTF-8,
  malformed/non-JSON bodies, nonstandard NaN/Infinity and parser recursion
  failures are omitted with `invalid_json`. There is no raw-text fallback.
- Bodies over 65,536 bytes are not decoded or parsed (`body_size_limit`). Shape
  traversal visits at most 256 nodes, counting the root and redaction markers;
  exceeding that budget omits the summary (`node_limit`). At depth 8, with the
  root at depth 0, a fixed `[depth limit]` marker replaces the subtree.
- Serialized summaries over 2,000 UTF-8 bytes are replaced with metadata
  (`summary_size_limit`). No partial JSON, partial credential or raw prefix is
  retained. These are conservative fixed budgets, not measured tuning targets.

The size limits bound audit parsing, traversal and storage. The middleware still
buffers the request with `request.body()` for downstream replay; this is **not**
an upload limit or a denial-of-service boundary. Body shape, byte counts, paths,
IP addresses and user agents can still be identifying. Do not put credentials in
URLs or user agents. This contract does not sanitize other application/proxy
logs, change audit access controls, or repair existing stored records. Historical
records require a separately authorized retention/remediation decision.

## Persistence and failure behavior

Database operations use async I/O but the commit is **awaited before returning
the response**. Audit latency therefore contributes to request latency. There
is no background task, durable retry queue, dedicated audit timeout or atomic
transaction with the request's business writes.

A persistence exception emits only `Audit log write failed; record not persisted`.
It does not interpolate the exception or log its traceback, because database
errors can contain SQL parameters. The original HTTP response is returned;
the missing record is not retried. A later request can persist normally. A
process interruption can also lose the record. This is best-effort availability,
not fail-closed security or long-duration reliability evidence.

## Reproduction

`backend/tests/test_audit_middleware.py` sends synthetic canaries through the
actual ASGI middleware, verifies downstream bytes, commits summaries to a
temporary SQLite sink, and reads them back. It plants a database failure whose
exception contains the canary and verifies sanitized logging and recovery.
It also covers coverage exclusions, HTTP errors, recursive shape, malformed
inputs and resource limits. The sink replaces the PostgreSQL session factory;
these tests do not prove PostgreSQL availability or full application access
control. Run from `backend/` with the project's Python environment:

```bash
TENANT_ID=corvus-mind PYTHONPATH=. python -m pytest tests/test_audit_middleware.py -q
TENANT_ID=corvus-mind PYTHONPATH=. python -O -m pytest tests/test_audit_middleware.py -q
```

For a real loopback HTTP/PostgreSQL round trip, provision a fresh disposable
database, set `DATABASE_URL` explicitly to its `postgresql+asyncpg` loopback URL
with a `corvus_test_*` name, and run:

```bash
TENANT_ID=corvus-mind PYTHONPATH=. python -m alembic upgrade head
TENANT_ID=corvus-mind PYTHONPATH=. python -m alembic check
TENANT_ID=corvus-mind PYTHONPATH=. python scripts/verify_audit_redaction.py
```

The verifier refuses missing, non-loopback and non-disposable database URLs
using explicit checks that survive `python -O`. It starts and stops its own
ephemeral loopback HTTP server, persists synthetic rows using the real session
factory/model, checks them and tests failure/recovery. It leaves the synthetic
rows in the disposable database for inspection; use a fresh database per run.
It does not exercise the full application composition or authorization layer.
