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

This module also owns :func:`effective_routes` and :func:`mounted_paths`, the
single accessor every route enumeration in the repo goes through — the
composition tests, the job reconciler below, and the surface files the
frontend's check:contracts and check:boundaries read. Route enumeration has one
fastapi-version-sensitive spelling and it lives here, once.

Run with the backend venv interpreter from the backend directory:

    PYTHONPATH=. venv/bin/python scripts/capability_snapshot.py --write
    PYTHONPATH=. venv/bin/python scripts/capability_snapshot.py --check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
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

# fastapi 0.138.0 changed what include_router() leaves behind. It used to flatten
# each included router's routes into the parent as APIRoute objects; it now
# appends one opaque _IncludedRouter per include and resolves through it at
# request time. Requests route identically either way — what moves is
# introspection, and Corvus decides capability ownership by introspection.
# Reading .routes directly against the wrapper sees pathless placeholders and
# concludes every capability owns nothing.
#
# iter_route_contexts is fastapi's own supported way through it: the same public
# helper its OpenAPI generator uses, yielding one RouteContext per route the app
# effectively serves, with include prefixes already applied.
#
# Imported unconditionally, and that is the point. This once carried a fallback
# to a plain .routes read for fastapi < 0.138, which was dead the moment the pin
# moved to 0.141.1 and would have been the wrong branch anyway: if a future
# fastapi renames this helper while keeping the wrapper, falling back silently
# resolves nothing and every capability reads as owning no routes. Failing at
# import is louder and arrives sooner than twelve composition tests failing for
# a reason nobody connects to a dependency bump.
try:
    from fastapi.routing import iter_route_contexts
except ImportError as exc:  # pragma: no cover — a fastapi API break, not a path
    raise ImportError(
        "fastapi.routing.iter_route_contexts is gone. Corvus resolves capability "
        "ownership through it; without it, route enumeration silently reports "
        "that every capability owns nothing. Find what replaced it and update "
        "effective_routes below — do not fall back to reading app.routes, which "
        "is what this replaced. Background: deps-fastapi-included-router."
    ) from exc


def effective_routes(app) -> list:
    """Every route ``app`` actually serves, with included routers resolved.

    The one accessor for "what is mounted here". Takes anything carrying a
    ``routes`` list — a FastAPI app or a bare APIRouter — and returns objects
    that answer ``path``, ``methods``, ``name`` and ``include_in_schema`` for
    the *effective* route, so callers never learn how fastapi arranged them.

    Note the attribute contract and do not assume ``hasattr``: a RouteContext
    always *has* ``path`` and ``methods``, and reports their absence by
    returning ``None``. Test membership with ``getattr(r, "methods", None)``,
    never ``hasattr(r, "methods")``.
    """
    return list(iter_route_contexts(app.routes))


def mounted_paths(app) -> set[str]:
    """The set of paths ``app`` serves, included routers resolved.

    This is the question capability ownership actually asks — "is this surface
    mounted?" — and the answer a disabled capability must make empty.
    """
    return {
        path
        for path in (getattr(r, "path", None) for r in effective_routes(app))
        if path is not None
    }


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
    bundle_present = any(getattr(r, "name", None) == "assets" for r in effective_routes(app))
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
    # "Has no methods" is spelled as a falsy value rather than a missing
    # attribute on purpose: a RouteContext always carries the attribute and
    # says "none" with None (or, for a mount reached through an include, an
    # empty set). hasattr here would silently call every mount a method route.
    unschematized = sorted(
        {
            f"{sorted(getattr(r, 'methods', None) or ['MOUNT'])[0]} "
            f"{getattr(r, 'path', None) or ''}"
            for r in effective_routes(app)
            if getattr(r, "include_in_schema", True) is False
            or not getattr(r, "methods", None)
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


_SYSTEMD_DIR = _BACKEND_DIR.parent / "harness" / "systemd"
_EXEC_URL = re.compile(r"ExecStart=.*?-X\s+POST\s+\"?(?P<url>https?://[^\s\"?]+)")


def _declared_unit_targets() -> dict[str, str]:
    """Map each in-repo timer unit to the path its service POSTs to.

    The maintenance jobs are not in-process schedulers — they are systemd
    timers that curl the application's own routes. Job ownership is therefore
    route ownership, and it can be checked without systemd being present.
    """
    targets: dict[str, str] = {}
    if not _SYSTEMD_DIR.is_dir():
        return targets
    for timer in sorted(_SYSTEMD_DIR.glob("*.timer")):
        service = timer.with_suffix(".service")
        if not service.exists():
            continue
        match = _EXEC_URL.search(service.read_text())
        if match:
            targets[timer.name] = "/" + match.group("url").split("/", 3)[-1]
    return targets


def _reconcile_jobs(tenant_id: str) -> tuple[list[str], list[str]]:
    """Compare a tenant's declared job ownership against the in-repo units.

    Returns (findings, informational lines). A finding is a real inconsistency:
    a profile claiming a unit that does not exist, or claiming one whose target
    route the profile does not mount — which would make the timer fire into a
    404 on every tick.
    """
    env = dict(os.environ, TENANT_ID=tenant_id, PYTHONPATH=str(_BACKEND_DIR))
    probe = (
        "import json;"
        "from app.composition.profiles import resolve_profile;"
        "from app.composition.factory import create_app;"
        "from app.tenant import tenant;"
        "from scripts.capability_snapshot import mounted_paths;"
        "p=resolve_profile(tenant);"
        "a=create_app(profile=p, tenant=tenant);"
        "print(json.dumps({'jobs': list(p.jobs()),"
        " 'paths': sorted(mounted_paths(a))}))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, env=env, cwd=str(_BACKEND_DIR),
    )
    if proc.returncode != 0:
        return ([f"{tenant_id}: could not resolve profile — {proc.stderr.strip()[-200:]}"], [])

    data = json.loads(proc.stdout)
    declared, mounted = data["jobs"], set(data["paths"])
    units = _declared_unit_targets()

    findings, info = [], []
    for unit in declared:
        target = units.get(unit)
        if target is None:
            findings.append(f"{tenant_id}: claims {unit}, which has no unit under harness/systemd")
        elif target not in mounted:
            findings.append(
                f"{tenant_id}: claims {unit} -> POST {target}, but this profile "
                f"does not mount that route; the timer would fire into a 404"
            )
        else:
            info.append(f"{tenant_id}: {unit} -> POST {target} (mounted)")

    for unit, target in units.items():
        if unit not in declared:
            info.append(
                f"{tenant_id}: {unit} -> POST {target} is UNOWNED by this "
                f"profile; installing it here would create an orphaned timer"
            )
    return findings, info


def _declared_jobs(tenant_id: str) -> set[str]:
    """The set of units ``tenant_id``'s profile claims ownership of."""
    env = dict(os.environ, TENANT_ID=tenant_id, PYTHONPATH=str(_BACKEND_DIR))
    probe = (
        "import json;"
        "from app.composition.profiles import resolve_profile;"
        "from app.tenant import tenant;"
        "print(json.dumps(list(resolve_profile(tenant).jobs())))"
    )
    proc = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                          text=True, env=env, cwd=str(_BACKEND_DIR))
    return set(json.loads(proc.stdout)) if proc.returncode == 0 else set()


def _orphaned_installed_units(claimed: set[str]) -> list[str]:
    """Installed corvus timers that no tenant profile claims.

    This is the half that motivated the check. A capability composed away stops
    mounting its routes, but the systemd units live outside the repo in
    ~/.config/systemd/user and keep firing on their schedule — into a 404,
    silently, forever. Nothing else in the system would notice.

    Skipped (not failed) where systemd is unavailable, e.g. in CI containers.
    """
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "list-unit-files", "--no-legend", "corvus-*.timer"],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        print("  (systemd unavailable — skipped installed-unit reconciliation)")
        return []
    if proc.returncode != 0:
        print("  (systemd query failed — skipped installed-unit reconciliation)")
        return []

    installed = {
        line.split()[0] for line in proc.stdout.splitlines()
        if line.strip() and line.split()[0].endswith(".timer")
    }
    if not installed:
        print("  (no corvus timers installed on this host)")
        return []

    findings = []
    for unit in sorted(installed):
        if unit in claimed:
            print(f"  installed: {unit} (claimed)")
            continue
        # Only timers that drive the application are a composition concern.
        # corvus-backup.timer runs pg_dump via a shell script; it owns no route
        # and composing a capability away cannot orphan it. Flagging it would
        # be a false alarm, and an alarm that cries wolf gets switched off.
        target = _installed_unit_target(unit)
        if target is None:
            print(f"  installed: {unit} (not capability-driven; no HTTP target)")
        else:
            findings.append(
                f"ORPHAN: {unit} is installed and scheduled and POSTs "
                f"{target}, but no tenant profile claims it — that route is "
                f"not mounted, so every tick fires into a 404"
            )
    return findings


def _installed_unit_target(timer_unit: str) -> str | None:
    """The route an installed timer's service POSTs to, if it drives the app."""
    service = timer_unit.removesuffix(".timer") + ".service"
    try:
        proc = subprocess.run(["systemctl", "--user", "cat", service],
                              capture_output=True, text=True, timeout=15)
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    match = _EXEC_URL.search(proc.stdout)
    return "/" + match.group("url").split("/", 3)[-1] if match else None


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
    mode.add_argument("--jobs", action="store_true",
                      help="reconcile declared job ownership against harness/systemd units")
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

    if args.jobs:
        all_findings: list[str] = []
        claimed: set[str] = set()
        for tenant_id in tenants:
            findings, info = _reconcile_jobs(tenant_id)
            for line in info:
                print(f"  {line}")
            all_findings.extend(findings)
            claimed.update(_declared_jobs(tenant_id))
        all_findings.extend(_orphaned_installed_units(claimed))
        if all_findings:
            sys.stderr.write("\njob ownership findings:\n")
            for line in all_findings:
                sys.stderr.write(f"  {line}\n")
            return 1
        print("\njob ownership reconciles for: " + ", ".join(tenants))
        return 0

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
