# Runbook: schema mismatch / migration failure

## Symptom

The service refuses to start. Nothing serves; there is no partial mode.

## Detection

- Startup exits **3** with `SchemaAuthorityError`
- The message names both revisions: observed vs expected
- If it starts and drifts later, `GET /ready` fails its `schema` check

## First response

1. Read the error — it already contains the fix. Do not guess.
2. `psql -d <db> -At -c "SELECT version_num FROM alembic_version"` to confirm.
3. `alembic upgrade head` **against the intended tenant database**. Running it
   against the wrong tenant is the common cause of this in the first place.
4. Never edit `alembic_version` by hand to make startup succeed. Fail-closed is
   the feature: a build serving a schema it was not written for corrupts
   quietly, and this is the last gate before that.

## Drill — EXECUTED 2026-08-02

A restored copy of the live database was deliberately downgraded one revision
(`027_synaptic_homeostasis` → `026_delivery_pathways`) and a backend booted
against it.

Observed — startup exit **3**, fail-closed, with:

```
SchemaAuthorityError: Database schema is not at this build's Alembic head:
observed 026_delivery_pathways; expected 027_synaptic_homeostasis.
Run `alembic upgrade head` with the intended tenant/database before starting Corvus.
```

No partial startup, no degraded serving, no request reached the graph.
