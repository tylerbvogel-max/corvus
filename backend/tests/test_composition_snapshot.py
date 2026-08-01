"""Composition tripwire for durability-tenant-composition.

These tests do not assert that composition is *correct* — today it is not, and
that is the point. They assert that the recorded baseline still describes the
running application, so that when the capability profile lands, the diff is
evidence rather than an unreviewed rewrite of the expectations.

Scope split, forced by the hermetic lane's no-subprocess rule:

  here                        the tenant under test (TENANT_ID), in-process,
                              route surface and full OpenAPI contract.
  capability_snapshot --check every tenant plus the import footprint, in one
                              clean process per tenant. Runs in CI, because
                              proving a module was never imported cannot be
                              done inside a pytest process that imported it.

The baseline was captured on 2026-07-31 against the unconditional-mount app.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.main import app
from app.tenant import tenant
from scripts.capability_snapshot import describe_surface

_CONTRACTS = Path(__file__).resolve().parent / "contracts"
_TENANTS_DIR = Path(__file__).resolve().parent.parent / "tenants"

# Captured 2026-07-31, pre-factory: every tenant received this same surface.
# corvus-mind still measures exactly this after the capability factory landed —
# byte-identical openapi.corvus-mind.json — which is how acceptance criterion 3
# ("no enabled production route changes contract accidentally") is evidenced
# rather than asserted.
_PREFACTORY_ROUTE_COUNT = 242

# Post-factory, measured. corvus-locomo is a benchmark tenant whose only
# consumer imports app.services.* in-process and never speaks HTTP, so its
# operator, compliance, governance, ingestion, evaluation, and external
# surfaces are composed away.
_EXPECTED_ROUTE_COUNTS = {
    "corvus-mind": _PREFACTORY_ROUTE_COUNT,
    "corvus-locomo": 78,
}

_ALL_TENANTS = sorted(
    d.name for d in _TENANTS_DIR.iterdir()
    if d.is_dir() and (d / "tenant.yaml").exists()
)


def _stored(kind: str, tenant_id: str) -> dict:
    return json.loads((_CONTRACTS / f"{kind}.{tenant_id}.json").read_text())


@pytest.mark.hermetic
def test_every_tenant_has_a_captured_baseline():
    """A tenant with no snapshot is a tenant whose surface nobody has reviewed."""
    assert _ALL_TENANTS, "no tenants discovered"
    missing = [
        f"{kind}.{name}.json"
        for name in _ALL_TENANTS
        for kind in ("openapi", "surface", "imports")
        if not (_CONTRACTS / f"{kind}.{name}.json").exists()
    ]
    assert not missing, f"uncaptured composition snapshots: {missing}"


@pytest.mark.hermetic
def test_live_surface_matches_recorded_baseline():
    """This tenant's real route surface still equals the reviewed baseline.

    Failure is not automatically a bug: it means composition moved. The fix is
    to re-run ``capability_snapshot.py --write`` and review the diff as a
    contract change, which is exactly the gate this record installs.
    """
    live = describe_surface(app, tenant.tenant_id)
    stored = _stored("surface", tenant.tenant_id)
    assert live["surface"] == stored, (
        "route surface drifted from the recorded baseline for "
        f"{tenant.tenant_id}; refresh with capability_snapshot.py --write "
        "and review the diff"
    )


@pytest.mark.hermetic
def test_live_openapi_contract_matches_recorded_baseline():
    """Enabled routes keep their request/response contracts.

    Surface equality alone would miss a route that still exists but changed
    shape, which acceptance criterion 3 forbids.
    """
    live = describe_surface(app, tenant.tenant_id)
    assert live["openapi"] == _stored("openapi", tenant.tenant_id), (
        f"OpenAPI contract drifted for {tenant.tenant_id}"
    )


@pytest.mark.hermetic
def test_tenant_surfaces_differ_by_profile():
    """Composition is selective, and selectively in the measured direction.

    This replaces the pre-factory assertion that every tenant received an
    identical surface. That test was written to be broken deliberately by the
    capability profile; this is what replaced it.
    """
    route_sets = {
        name: {(r["path"], r["method"]) for r in _stored("surface", name)["routes"]}
        for name in _ALL_TENANTS
    }
    assert len({frozenset(routes) for routes in route_sets.values()}) == len(_ALL_TENANTS), (
        "two tenants compose to the same surface; either a profile stopped "
        "being selective or a capability lost its routers"
    )

    for name, expected in _EXPECTED_ROUTE_COUNTS.items():
        actual = _stored("surface", name)["method_level_route_count"]
        assert actual == expected, (
            f"{name} exposes {actual} documented routes, expected {expected}"
        )

    # The reduced tenant's surface must be a strict subset of the full one —
    # a profile may only remove, never invent, routes.
    assert route_sets["corvus-locomo"] < route_sets["corvus-mind"]


@pytest.mark.hermetic
def test_production_contract_survived_the_factory():
    """corvus-mind's contract is unchanged from the pre-factory capture."""
    surface = _stored("surface", "corvus-mind")
    assert surface["method_level_route_count"] == _PREFACTORY_ROUTE_COUNT, (
        "the operator tenant's route count moved; the factory was supposed to "
        "change composition, not contracts"
    )


@pytest.mark.hermetic
def test_reduced_profile_actually_drops_modules():
    """Absence of routes is cheap; absence of imports is the real property."""
    mind = _stored("imports", "corvus-mind")["app_modules"]
    locomo = _stored("imports", "corvus-locomo")["app_modules"]
    dropped = set(mind) - set(locomo)

    assert len(dropped) > 40, (
        f"only {len(dropped)} modules dropped for the reduced profile; "
        f"disabled capabilities are still being imported"
    )
    # The import-time-heavy ones specifically: FastMCP builds a server and
    # registers every tool at import, and the compliance registry is a
    # module-level singleton.
    for module in ("app.mcp_server", "app.mcp_http", "app.compliance.registry"):
        assert module in mind
        assert module not in locomo, f"{module} still imported by the reduced profile"
