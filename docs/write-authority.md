# Governed write authority

The shared automatic-write gate accepts `informational`, `guidance`,
`organizational`, `industry_practice`, `regulatory` and `binding_standard`.
`authority_rank`, `evaluate_write` and proposal aggregation reject every other
value with `ValueError`, including empty strings, whitespace, case variants,
numbers, booleans and containers. Invalid input is rejected before automatic
approval; it is not silently relabeled or persisted as an ordinary queued save.

`POST /remember` exposes this vocabulary as an OpenAPI enum and returns 422 for
unsupported authority. Omission defaults to `informational`; explicit JSON null
is invalid. The internal gate retains the historical `None` default for
unattributed writes. `save_lesson` normalizes that internal default to
`informational` and validates authority before staging or provider work.

## Review requirements

Manual mode queues all valid writes. Tiered mode may automatically apply
informational or guidance writes within its configured ceiling, subject to
guardrail and confidence checks. Organizational and all higher authorities
always require human review, even if a legacy policy raises its ceiling.
Assistant-scope creates, edits and moves also require countersign; low-authority
Assistant saves are elevated to organizational authority. This enforces the
documented identity rule across HTTP saves and proposal routing, rather than
depending only on the distiller's scope assignment.

These checks do not replace human approval or the Action Bus. Valid automatic
proposals still use the existing apply tree, actor and audit/reversibility path.
The automatic gate is not a new validation layer for every direct privileged
Action Bus operation, and this change does not establish multi-user security.

## Proposal aggregation

Every item is checked before choosing the highest authority. One valid item
cannot hide an invalid sibling. Creates require a JSON object spec; omitted or
null spec authority retains the internal default. Updates and merges require
resolvable targets and consider the target's current authority and department.
Authority-field changes also validate and consider the new value; moves into
Assistant scope require review. An existing high authority cannot be lowered
to obtain automatic approval.

Malformed/non-object specs, unknown authorities, missing targets and empty
proposals raise before approval or apply. Automatic classification currently
supports create/update/merge items only. Link, rescale and reconsolidation
proposals have different multi-target contracts: they must use their existing
explicit review paths rather than being misclassified as unattributed writes.
Document placement catches gate validation failures and leaves its proposal
queued after rollback. Other internal callers must handle the error and roll
back their own transaction; the gate does not own their commits.

## Policy validation

Policy construction, dictionary overlays and evaluation use explicit validation
that survives `python -O`. Modes must be `manual` or `tiered`, ceilings must name
a supported authority, and guardrail requirements must be actual booleans.
Confidence thresholds/signals must be finite numbers in [0,1], not booleans or
strings. `None` remains valid only for an inapplicable runtime signal. Unknown
policy keys and malformed regional overrides are rejected, including falsey
values that previously disappeared into a default. Absent config/override
fields retain their documented defaults.

Errors do not interpolate arbitrary authority or policy values. Validation is
performed when the policy is loaded/used; this is not proof that every unsafe
deployment configuration fails at startup. Broader deployment-profile hardening
remains separate work. Historical invalid rows are not rewritten.

## Evidence and reproduction

From `backend/`, using the project Python environment:

```bash
TENANT_ID=corvus-mind PYTHONPATH=. python -m pytest tests/test_write_gate.py tests/test_write_authority_validation.py tests/test_region_policy.py -q
TENANT_ID=corvus-mind PYTHONPATH=. python -O -m pytest tests/test_write_gate.py tests/test_write_authority_validation.py tests/test_region_policy.py -q
```

Tests exercise internal boundaries, mixed proposals, region overlays and actual
ASGI `/remember` validation before the replaced save function can run. Normal
allowed writes and the existing Action Bus routing contract remain covered.
The focused fixtures use synthetic data and never connect to a personal graph.

`scripts/verify_write_authority.py` exercises actual loopback HTTP, lesson
staging and Action Bus persistence on a fresh migrated PostgreSQL database.
It requires an explicit loopback `DATABASE_URL` naming `corvus_test_*` or
`corvus_migration_*`. Provision the database, then run `python -m alembic
upgrade head`, `python -m alembic check`, and `python -O
scripts/verify_write_authority.py` with `TENANT_ID=corvus-mind PYTHONPATH=.`.
The probe replaces near-duplicate and embedding work to avoid inference;
it does not prove provider behavior or full deployment authentication.
Synthetic rows remain for inspection until the disposable database is removed.
