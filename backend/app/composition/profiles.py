"""Resolve a tenant into the capability set it is authorized to run.

Fail-closed is the whole point of this module, so it fails on four separate
things rather than defaulting to "run everything":

  * a tenant.yaml with no ``capabilities:`` block at all — silence is not
    consent, and the pre-factory behavior (mount everything) is exactly what
    this record exists to remove;
  * a capabilities block that is not a list;
  * an unrecognized capability name, including one that was valid before a
    rename;
  * a duplicate entry, which usually means a merge went wrong.

Every failure raises :class:`ProfileError` at import/startup with the tenant,
the offending value, and the known-good set — an actionable startup error, not
a 500 on the first request that happens to touch the missing surface.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.composition.capabilities import (
    CAPABILITIES,
    Capability,
    RouterSpec,
    STARTUP_STEPS,
    StartupStep,
    capability_of,
)


class ProfileError(RuntimeError):
    """Composition configuration is missing, malformed, or unrecognized."""


@dataclass(frozen=True)
class CapabilityProfile:
    """The resolved answer to 'what does this tenant run?'"""

    tenant_id: str
    granted: frozenset[Capability]

    def __contains__(self, capability: Capability) -> bool:
        return capability in self.granted

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(c.value for c in self.granted))

    @property
    def disabled(self) -> tuple[str, ...]:
        return tuple(sorted(c.value for c in set(Capability) - self.granted))

    def routers(self) -> tuple[RouterSpec, ...]:
        """Router specs to mount, deduplicated, in declaration order.

        Two capabilities may legitimately name the same router; mounting it
        twice would duplicate every one of its operations in the OpenAPI
        document and quietly corrupt the contract snapshot.
        """
        seen: set[tuple[str, str]] = set()
        ordered: list[RouterSpec] = []
        for capability in Capability:  # declaration order, not set order
            if capability not in self.granted:
                continue
            for spec in CAPABILITIES[capability].routers:
                key = (spec.module, spec.attr)
                if key not in seen:
                    seen.add(key)
                    ordered.append(spec)
        return tuple(ordered)

    def startup_steps(self) -> tuple[StartupStep, ...]:
        return tuple(s for s in STARTUP_STEPS if s.is_required(self.granted))

    def jobs(self) -> tuple[str, ...]:
        """Scheduled units this profile is responsible for driving.

        A unit absent from this tuple but installed on the host is an orphan:
        it will POST into a route this profile does not mount.
        """
        return tuple(sorted({
            unit
            for capability in self.granted
            for unit in CAPABILITIES[capability].jobs
        }))


def resolve_profile(tenant_config) -> CapabilityProfile:
    """Read and validate ``capabilities:`` from a loaded tenant config."""
    tenant_id = tenant_config.tenant_id
    declared = tenant_config.declared_capabilities

    if declared is None:
        raise ProfileError(
            f"tenant {tenant_id!r} declares no 'capabilities:' block in "
            f"tenant.yaml. Composition fails closed: list the capabilities "
            f"this tenant runs. Known capabilities: "
            f"{', '.join(sorted(c.value for c in Capability))}"
        )
    if not isinstance(declared, list):
        raise ProfileError(
            f"tenant {tenant_id!r} declares 'capabilities:' as "
            f"{type(declared).__name__}; it must be a list of names"
        )

    granted: set[Capability] = set()
    for entry in declared:
        if not isinstance(entry, str):
            raise ProfileError(
                f"tenant {tenant_id!r} lists a non-string capability: {entry!r}"
            )
        try:
            capability = capability_of(entry)
        except ValueError as exc:
            raise ProfileError(f"tenant {tenant_id!r}: {exc}") from None
        if capability in granted:
            raise ProfileError(
                f"tenant {tenant_id!r} lists capability {entry!r} more than once"
            )
        granted.add(capability)

    return CapabilityProfile(tenant_id=tenant_id, granted=frozenset(granted))
