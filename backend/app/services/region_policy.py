"""Region policy resolution — per-silo knobs over the ONE shared substrate.

Provides:
- RequesterContext: who is recalling, which regions they belong to, and
  whether they hold the privileged cross-region read role (reconciler,
  DETECTION ONLY — privileged results never leak raw content to end users).
- A small thread-safe cache of active RegionPolicy rows (per-query scoring
  reads them on the hot path).
- resolve_scoring_weights: global settings defaults merged with a region's
  overrides — "your QA knowledge and your shop-floor knowledge don't decay
  the same way, so we don't score them the same way."
- restricted_regions / requester_can_see: the ACL primitives used by
  candidate load, spread promotion, and assembly.
"""

import threading
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import RegionPolicy

# Signal-weight keys a region may override.
WEIGHT_KEYS = (
    "weight_relevance", "weight_burst", "weight_impact",
    "weight_precision", "weight_novelty", "weight_recency",
    "weight_coldstart_prior",
)


@dataclass(frozen=True)
class RequesterContext:
    """Identity scope for recall. regions=None means unrestricted — the
    single-user/local default, preserving pre-ACL behavior. An enterprise
    deployment resolves this from its auth layer per request."""
    principal: str | None = None
    regions: tuple[str, ...] | None = None
    privileged: bool = False


class _RegionPolicyCache:
    """Thread-safe cache of active region policies keyed by region."""

    def __init__(self):
        self._lock = threading.Lock()
        self._policies: dict[str, dict] = {}
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def load(self, policies: dict[str, dict]) -> None:
        with self._lock:
            self._policies = policies
            self._loaded = True

    def invalidate(self) -> None:
        with self._lock:
            self._policies = {}
            self._loaded = False

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return dict(self._policies)


_cache = _RegionPolicyCache()


def _policy_to_dict(p: RegionPolicy) -> dict:
    return {
        "region": p.region,
        "display_name": p.display_name,
        "scoring_weights": p.scoring_weights or {},
        "loop_config": p.loop_config or {},
        "acl": p.acl or {},
        "write_gate": p.write_gate or {},
        "projection": p.projection or {},
    }


async def get_region_policies(db: AsyncSession) -> dict[str, dict]:
    """Return {region: policy dict} for active policies (cached)."""
    if not _cache.is_loaded:
        rows = (await db.execute(
            select(RegionPolicy).where(RegionPolicy.is_active.is_(True))
        )).scalars().all()
        _cache.load({p.region: _policy_to_dict(p) for p in rows})
    return _cache.snapshot()


def invalidate_region_policy_cache() -> None:
    """Call after any RegionPolicy mutation."""
    _cache.invalidate()


def default_weights() -> dict[str, float]:
    """Global signal weights from settings."""
    return {key: float(getattr(settings, key)) for key in WEIGHT_KEYS}


def resolve_scoring_weights(
    region: str | None, policies: dict[str, dict],
) -> dict[str, float]:
    """Region-effective weights: global defaults overlaid with overrides."""
    weights = default_weights()
    if not region:
        return weights
    overrides = (policies.get(region) or {}).get("scoring_weights") or {}
    for key, value in overrides.items():
        if key in weights:
            weights[key] = float(value)
    return weights


def restricted_regions(policies: dict[str, dict]) -> list[str]:
    """Regions whose ACL default is 'restricted' (per-neuron NULL inherits it)."""
    return [
        region for region, policy in policies.items()
        if (policy.get("acl") or {}).get("visibility") == "restricted"
    ]


def requester_can_see(
    requester: RequesterContext | None,
    neuron_region: str | None,
    neuron_visibility: str | None,
    restricted: set[str],
) -> bool:
    """The single ACL predicate, mirrored by the SQL candidate filters.

    Effective visibility = per-neuron override, else region default.
    Unrestricted requesters (None) and privileged readers see everything;
    otherwise restricted knowledge is visible only inside its own region.
    """
    effective_restricted = (
        neuron_visibility == "restricted"
        or (neuron_visibility is None and neuron_region in restricted)
    )
    if not effective_restricted:
        return True
    if requester is None or requester.privileged:
        return True  # None = unrestricted local default; privileged = reconciler
    return bool(requester.regions and neuron_region in requester.regions)


def acl_sql_clause(
    requester: RequesterContext | None,
    restricted: list[str],
    params: dict,
) -> str:
    """SQL fragment enforcing requester_can_see on the neurons table.

    Returns "TRUE" when no filtering applies (unrestricted requester,
    privileged reader, or no restricted regions configured).
    """
    if requester is None or requester.privileged or not restricted:
        return "TRUE"
    params["acl_restricted_regions"] = list(restricted)
    params["acl_requester_regions"] = list(requester.regions or ())
    return (
        "(NOT (visibility = 'restricted' OR (visibility IS NULL AND "
        "department = ANY(:acl_restricted_regions))) "
        "OR department = ANY(:acl_requester_regions))"
    )
