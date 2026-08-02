"""Liveness and readiness must be able to fail independently."""

from __future__ import annotations

import asyncio

import pytest

from app.services import readiness
from app.services.readiness import (
    FAIL, PASS, ReadinessCheck, ReadinessReport, check_capability_surface,
    check_schema, evaluate_readiness,
)
from app.services.schema_authority import (
    SchemaAuthorityError, SchemaAuthorityStatus,
)


def _run(coro):
    return asyncio.run(coro)


# ---- capability surface ----------------------------------------------------

def test_granted_capability_whose_router_never_mounted_fails_readiness():
    """The AC-8 banner class: the profile claims it, nothing serves it."""
    check = check_capability_surface(
        claimed={"app.routers.admin_compliance:router", "app.routers.memory:router"},
        mounted={"app.routers.memory:router"},
        granted=("compliance", "memory"),
    )
    assert check.status == FAIL
    assert "admin_compliance" in check.detail
    assert check.remediation


def test_capability_intentionally_absent_produces_silence_not_an_error():
    """Readiness is computed FROM the granted set, never the full catalog."""
    check = check_capability_surface(
        claimed={"app.routers.memory:router"},
        mounted={"app.routers.memory:router"},
        granted=("memory",),
    )
    assert check.status == PASS
    assert "1 granted" in check.detail


def test_extra_mounted_routers_do_not_fail_readiness():
    """Only claimed-but-missing is a fault; the reverse is composition's job."""
    check = check_capability_surface(
        claimed={"a:router"}, mounted={"a:router", "b:router"}, granted=("memory",),
    )
    assert check.status == PASS


# ---- schema ----------------------------------------------------------------

def test_schema_check_reports_head_when_database_is_servable(monkeypatch):
    async def _ok(_engine):
        return SchemaAuthorityStatus(
            current_heads=("027_synaptic_homeostasis",),
            expected_heads=("027_synaptic_homeostasis",),
        )
    monkeypatch.setattr(readiness, "validate_schema_authority", _ok)
    check = _run(check_schema(engine=None))
    assert check.status == PASS
    assert "027_synaptic_homeostasis" in check.detail


def test_schema_mismatch_fails_readiness_with_actionable_detail(monkeypatch):
    async def _mismatch(_engine):
        raise SchemaAuthorityError(
            "Database schema is not at this build's Alembic head: observed 026; expected 027."
        )
    monkeypatch.setattr(readiness, "validate_schema_authority", _mismatch)
    check = _run(check_schema(engine=None))
    assert check.status == FAIL
    assert "expected 027" in check.detail
    assert "alembic upgrade head" in check.remediation


def test_database_outage_fails_readiness(monkeypatch):
    async def _down(_engine):
        raise SchemaAuthorityError("Database connectivity failed before Corvus startup.")
    monkeypatch.setattr(readiness, "validate_schema_authority", _down)
    assert _run(check_schema(engine=None)).status == FAIL


class _InvalidCatalogName(Exception):
    """Stands in for asyncpg.InvalidCatalogNameError, which is not a SQLAlchemyError."""


def test_driver_exception_becomes_a_readiness_failure_not_a_500(monkeypatch):
    """Regression: dropping the database under a live process answered HTTP 500.

    Observed in a drill on 2026-08-02 — asyncpg raises InvalidCatalogNameError,
    which validate_schema_authority does not catch, so it escaped and /ready
    replied "Internal server error" during the precise outage it reports on.
    """
    async def _driver_blows_up(_engine):
        raise _InvalidCatalogName('database "corvus_test_readiness" does not exist')
    monkeypatch.setattr(readiness, "validate_schema_authority", _driver_blows_up)

    check = _run(check_schema(engine=None))
    assert check.status == FAIL
    # Causality is the payload: the driver's exception type must survive.
    assert "_InvalidCatalogName" in check.detail
    assert "does not exist" in check.detail
    assert check.remediation


@pytest.mark.parametrize("failure", [
    _InvalidCatalogName("database does not exist"),
    TimeoutError("connection timed out"),
    OSError("connection refused"),
    RuntimeError("event loop is closed"),
])
def test_readiness_never_raises_whatever_the_dependency_does(monkeypatch, failure):
    """Any dependency failure is an ANSWER (not ready), never an exception."""
    async def _fails(_engine):
        raise failure
    monkeypatch.setattr(readiness, "validate_schema_authority", _fails)

    report = _run(evaluate_readiness(
        engine=None, tenant="t", granted=(), claimed_specs=set(), mounted_specs=set(),
    ))
    assert report.ready is False
    assert type(failure).__name__ in report.as_dict()["checks"][0]["detail"]


# ---- report ----------------------------------------------------------------

def test_every_check_runs_even_after_one_fails(monkeypatch):
    """A schema failure must not hide a capability failure waiting behind it."""
    async def _down(_engine):
        raise SchemaAuthorityError("Database connectivity failed.")
    monkeypatch.setattr(readiness, "validate_schema_authority", _down)

    report = _run(evaluate_readiness(
        engine=None, tenant="corvus-mind", granted=("memory",),
        claimed_specs={"a:router"}, mounted_specs=set(),
    ))
    assert report.ready is False
    names = [c.name for c in report.checks]
    assert names == ["schema", "capabilities"]
    assert all(c.status == FAIL for c in report.checks)


def test_ready_report_serializes_status_and_checks():
    report = ReadinessReport(tenant="corvus-mind", checks=[
        ReadinessCheck(name="schema", status=PASS, detail="fine"),
    ])
    payload = report.as_dict()
    assert payload["status"] == "ready"
    assert payload["tenant"] == "corvus-mind"
    assert payload["checks"] == [
        {"name": "schema", "status": PASS, "detail": "fine"},
    ]


def test_not_ready_when_any_check_fails():
    report = ReadinessReport(tenant="t", checks=[
        ReadinessCheck(name="schema", status=PASS, detail=""),
        ReadinessCheck(name="capabilities", status=FAIL, detail="missing"),
    ])
    assert report.ready is False
    assert report.as_dict()["status"] == "not-ready"


@pytest.mark.parametrize("checks, expected", [
    ([], True),   # nothing to fail; a profile with no dependencies is servable
    ([ReadinessCheck("a", PASS, "")], True),
    ([ReadinessCheck("a", FAIL, "")], False),
])
def test_readiness_is_conjunctive(checks, expected):
    assert ReadinessReport(tenant="t", checks=checks).ready is expected
