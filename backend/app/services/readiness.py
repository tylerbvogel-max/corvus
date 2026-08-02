"""Readiness evaluation: can this process actually serve, right now.

Liveness and readiness answer different questions and must fail independently.
Liveness asks "can the process answer at all" and belongs to the supervisor
deciding whether to restart. Readiness asks "are this build's dependencies
satisfied" and belongs to the thing deciding whether to send traffic. Corvus
previously had only ``/health``, which queried the database and then reported
``ok`` — so it went down whenever Postgres blinked (wrong for liveness) while
never once checking the schema head or the capability surface (wrong for
readiness).

The capability check exists because of a specific observed failure. When the
compliance capability was composed away, ``/admin/system-banner`` went with it
and the AC-8 system-use notification silently stopped rendering — the frontend
caught the error and did nothing. No log, alert, or health check noticed. A
profile that CLAIMS a capability must therefore prove its router surface
actually mounted, and a capability that is intentionally absent must produce
silence rather than an error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from app.services.schema_authority import (
    SchemaAuthorityError, validate_schema_authority,
)


PASS = "pass"
FAIL = "fail"


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    status: str
    detail: str
    remediation: str = ""

    def as_dict(self) -> dict[str, str]:
        payload = {"name": self.name, "status": self.status, "detail": self.detail}
        if self.remediation:
            payload["remediation"] = self.remediation
        return payload


@dataclass(frozen=True)
class ReadinessReport:
    tenant: str
    checks: list[ReadinessCheck] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return all(check.status == PASS for check in self.checks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "ready" if self.ready else "not-ready",
            "tenant": self.tenant,
            "checks": [check.as_dict() for check in self.checks],
        }


async def check_schema(engine: AsyncEngine) -> ReadinessCheck:
    """Connectivity and Alembic head in one check — both are hard requirements.

    ``validate_schema_authority`` already distinguishes the two failures in its
    message, and it is the same call startup fails closed on, so readiness and
    startup cannot disagree about what a servable database looks like.
    """
    try:
        status = await validate_schema_authority(engine)
    except SchemaAuthorityError as exc:
        return ReadinessCheck(
            name="schema",
            status=FAIL,
            detail=str(exc),
            remediation=(
                "Verify DATABASE_URL and PostgreSQL availability, then run "
                "`alembic upgrade head` for this tenant."
            ),
        )
    except Exception as exc:  # noqa: BLE001 - a readiness probe reports, never raises
        # Found by drill, not by review: dropping the database under a running
        # process raises asyncpg.InvalidCatalogNameError, which is NOT a
        # SQLAlchemyError, so it escaped validate_schema_authority's catch and
        # /ready answered 500 "Internal server error" — the least actionable
        # possible reply to the exact outage this endpoint exists to report.
        # The driver's exception type is kept in the detail because causality
        # is the payload here; collapsing it to a generic string is the failure.
        return ReadinessCheck(
            name="schema",
            status=FAIL,
            detail=f"database dependency failed: {type(exc).__name__}: {exc}",
            remediation=(
                "Verify the database exists and is reachable at this tenant's "
                "DATABASE_URL, then run `alembic upgrade head`."
            ),
        )
    return ReadinessCheck(
        name="schema", status=PASS,
        detail=f"database reachable at Alembic head {', '.join(status.current_heads)}",
    )


def check_capability_surface(
    claimed: set[str], mounted: set[str], granted: tuple[str, ...],
) -> ReadinessCheck:
    """Every router the profile claims must actually be mounted.

    Silence is the correct answer for a capability this tenant does not have,
    so ``claimed`` is derived from the granted set — never from the full
    capability catalog. A readiness probe that demanded the compliance registry
    would fail permanently on a tenant that intentionally omits it.
    """
    missing = sorted(claimed - mounted)
    if missing:
        return ReadinessCheck(
            name="capabilities",
            status=FAIL,
            detail=(
                f"profile grants {', '.join(granted) or 'nothing'} but "
                f"{len(missing)} claimed router surface(s) did not mount: "
                f"{', '.join(missing)}"
            ),
            remediation=(
                "A granted capability is not serving. Check composition wiring "
                "and `capability_snapshot.py --check` for contract drift."
            ),
        )
    return ReadinessCheck(
        name="capabilities", status=PASS,
        detail=(f"{len(granted)} granted ({', '.join(granted) or 'none'}); "
                f"all {len(claimed)} claimed router surface(s) mounted"),
    )


async def evaluate_readiness(
    *, engine: AsyncEngine, tenant: str, granted: tuple[str, ...],
    claimed_specs: set[str], mounted_specs: set[str],
) -> ReadinessReport:
    """Run every readiness check, always all of them.

    Checks are not short-circuited: an operator debugging a failed deployment
    needs the whole picture, and a schema failure must not hide a capability
    failure that will bite immediately after the migration is fixed.
    """
    return ReadinessReport(
        tenant=tenant,
        checks=[
            await check_schema(engine),
            check_capability_surface(claimed_specs, mounted_specs, granted),
        ],
    )
