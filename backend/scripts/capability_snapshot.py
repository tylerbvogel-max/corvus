#!/usr/bin/env python3
"""Capture (or verify) the runtime composition surface of one tenant profile.

This is the tripwire for durability-tenant-composition. Before the application
factory exists, every tenant gets the same unconditionally-mounted surface, and
that sameness is exactly what the baseline has to record: once composition
becomes selective, the diff against these files is the evidence that a
capability was actually removed rather than merely refused later.

Three artifacts per tenant, each answering a different verification question:

  openapi.<tenant>.json   the full contract. Diffing this answers "did an
                          ENABLED route's request/response shape change?" —
                          the compatibility half of verification #2.
  surface.<tenant>.json   one line per method-level route plus a fingerprint of
                          that operation's schema. Diffing this answers "is a
                          DISABLED route absent?" — the presence/absence half of
                          verification #2, in a form a human can read.
  imports.<tenant>.json   every ``app.*`` module resident in sys.modules after
                          importing the application. This is the cheap proxy for
                          verification #3: a capability whose service modules
                          were never imported cannot have constructed a provider
                          client or registered a startup dependency. Absence
                          here is stronger than a 404.

The import footprint is captured in a dedicated subprocess per tenant, because
sys.modules is process-global and a second tenant imported into the same
interpreter would inherit the first one's modules and silently read as "always
present".

Run with the backend venv interpreter from the backend directory:

    PYTHONPATH=. venv/bin/python scripts/capability_snapshot.py --write
    PYTHONPATH=. venv/bin/python scripts/capability_snapshot.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_OUT = _BACKEND_DIR / "tests" / "contracts"
_TENANTS_DIR = _BACKEND_DIR / "tenants"

# Snapshots are compared byte-for-byte, so every writer must agree on encoding.
_JSON_KWARGS = {"sort_keys": True, "indent": 2, "ensure_ascii": False}

# Static-frontend serving is conditional on a built frontend/dist, which is a
# property of the machine rather than of the tenant profile. Excluded from the
# comparable surface and reported as a flag instead.
_SPA_CATCH_ALL = "/{full_path}"
_STATIC_FRONTEND_ROUTES = frozenset({"MOUNT /assets", f"GET {_SPA_CATCH_ALL}"})


def _discover_tenants() -> list[str]:
    """Every tenant directory carrying a tenant.yaml, in stable order."""
    return sorted(
        d.name for d in _TENANTS_DIR.iterdir()
        if d.is_dir() and (d / "tenant.yaml").exists()
    )


def _fingerprint(payload: object) -> str:
    """Short stable digest of one operation's schema.

    Full-document diffs prove compatibility but are unreadable; this lets the
    compact surface file show *that* an operation's contract moved without
    reproducing it.
    """
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def describe_surface(app, tenant_id: str) -> dict:
    """Describe the route surface an already-built ``app`` exposes.

    Split out from :func:`_collect` so the hermetic test can call it in-process
    on the tenant under test. The import footprint deliberately stays out of
    this function: under pytest, sys.modules is polluted by the test modules
    themselves, so that measurement is only meaningful in a dedicated process.
    """
    spec = app.openapi()

    # The SPA catch-all and /assets mount exist only when frontend/dist has been
    # built, so they are machine state, not composition. Left in, the snapshot
    # would flip between a developer box and a clean CI checkout and the tripwire
    # would be useless. Record the condition, exclude the routes.
    bundle_present = any(getattr(r, "name", None) == "assets" for r in app.routes)
    spec.get("paths", {}).pop(_SPA_CATCH_ALL, None)

    routes = []
    for path, operations in spec.get("paths", {}).items():
        for method, operation in operations.items():
            if not isinstance(operation, dict):
                continue  # shared parameters/servers keys, not an operation
            routes.append({
                "path": path,
                "method": method.upper(),
                "operation_id": operation.get("operationId"),
                "tags": operation.get("tags", []),
                "schema_fingerprint": _fingerprint(operation),
            })
    routes.sort(key=lambda r: (r["path"], r["method"]))

    # Routes FastAPI serves that never reach the OpenAPI document: mounted ASGI
    # apps (/mcp), static files, and anything with include_in_schema=False. A
    # composition record that ignored these would call /mcp "absent" while it
    # was still mounted and serving.
    unschematized = sorted(
        {
            f"{sorted(getattr(r, 'methods', None) or ['MOUNT'])[0]} {getattr(r, 'path', '')}"
            for r in app.routes
            if getattr(r, "include_in_schema", True) is False
            or not hasattr(r, "methods")
        }
        - _STATIC_FRONTEND_ROUTES
    )

    return {
        "tenant_id": tenant_id,
        # Reported, never stored: this varies with the machine, not the profile.
        "frontend_bundle_present": bundle_present,
        "openapi": spec,
        "surface": {
            "tenant_id": tenant_id,
            "method_level_route_count": len(routes),
            "documented_path_count": len(spec.get("paths", {})),
            "routes": routes,
            "routes_absent_from_openapi": unschematized,
            "static_frontend_excluded": {
                "routes": sorted(_STATIC_FRONTEND_ROUTES),
                "note": "conditional on frontend/dist; excluded so the snapshot "
                        "is identical on a dev box and a clean CI checkout",
            },
        },
    }


def _collect(tenant_id: str) -> dict:
    """Import the app for ``tenant_id`` and describe what that produced.

    Runs inside the per-tenant subprocess. Importing app.main is deliberately
    the whole experiment: it triggers module-level router mounting and any
    import-time client construction, but NOT the lifespan, so nothing here
    needs a reachable database.
    """
    from app.main import app  # noqa: PLC0415 — import must follow TENANT_ID

    snapshot = describe_surface(app, tenant_id)
    app_modules = sorted(m for m in sys.modules if m == "app" or m.startswith("app."))
    snapshot["imports"] = {
        "tenant_id": tenant_id,
        "app_module_count": len(app_modules),
        "app_modules": app_modules,
    }
    return snapshot


def _capture(tenant_id: str) -> dict:
    """Collect ``tenant_id``'s snapshot in a clean interpreter.

    sys.modules and the tenant singleton are both process-global; reusing this
    process across tenants would make every tenant look like the first one.
    """
    env = dict(os.environ, TENANT_ID=tenant_id, PYTHONPATH=str(_BACKEND_DIR))
    # The snapshot must describe composition, not local operator preference.
    env.pop("CORVUS_ACCESS_KEY", None)
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--emit", tenant_id],
        capture_output=True, text=True, env=env, cwd=str(_BACKEND_DIR),
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"snapshot capture failed for {tenant_id} (exit {proc.returncode})")
    return json.loads(proc.stdout)


def _paths(out_dir: Path, tenant_id: str) -> dict[str, Path]:
    return {
        "openapi": out_dir / f"openapi.{tenant_id}.json",
        "surface": out_dir / f"surface.{tenant_id}.json",
        "imports": out_dir / f"imports.{tenant_id}.json",
    }


def _render(snapshot: dict) -> dict[str, str]:
    return {
        "openapi": json.dumps(snapshot["openapi"], **_JSON_KWARGS) + "\n",
        "surface": json.dumps(snapshot["surface"], **_JSON_KWARGS) + "\n",
        "imports": json.dumps(snapshot["imports"], **_JSON_KWARGS) + "\n",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="write/refresh snapshots")
    mode.add_argument("--check", action="store_true", help="fail if snapshots drifted")
    mode.add_argument("--emit", metavar="TENANT", help="internal: emit one tenant as JSON")
    parser.add_argument("--tenant", action="append", help="limit to this tenant (repeatable)")
    parser.add_argument("--out-dir", type=Path, default=_DEFAULT_OUT)
    args = parser.parse_args()

    if args.emit:
        json.dump(_collect(args.emit), sys.stdout)
        return 0

    tenants = args.tenant or _discover_tenants()
    if not tenants:
        sys.stderr.write(f"no tenants with a tenant.yaml under {_TENANTS_DIR}\n")
        return 2

    args.out_dir.mkdir(parents=True, exist_ok=True)
    drifted: list[str] = []

    for tenant_id in tenants:
        snapshot = _capture(tenant_id)
        rendered = _render(snapshot)
        targets = _paths(args.out_dir, tenant_id)
        for kind, path in targets.items():
            if args.write:
                path.write_text(rendered[kind], encoding="utf-8")
                continue
            if not path.exists():
                drifted.append(f"{path.name}: missing (never captured)")
            elif path.read_text(encoding="utf-8") != rendered[kind]:
                drifted.append(f"{path.name}: differs from live composition")
        if args.write:
            counts = json.loads(rendered["surface"])
            imports = json.loads(rendered["imports"])
            print(
                f"{tenant_id}: {counts['method_level_route_count']} method-level routes, "
                f"{len(counts['routes_absent_from_openapi'])} undocumented, "
                f"{imports['app_module_count']} app modules imported "
                f"(frontend bundle {'present' if snapshot['frontend_bundle_present'] else 'absent'}, excluded)"
            )

    if args.check:
        if drifted:
            sys.stderr.write("composition snapshot drift:\n")
            for line in drifted:
                sys.stderr.write(f"  {line}\n")
            sys.stderr.write(
                "\nIf the change was intended, refresh with:\n"
                "  PYTHONPATH=. venv/bin/python scripts/capability_snapshot.py --write\n"
                "and review the diff as a contract change.\n"
            )
            return 1
        print(f"composition snapshots match live app for: {', '.join(tenants)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
