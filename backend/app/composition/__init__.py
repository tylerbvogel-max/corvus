"""Explicit runtime composition — which surfaces a tenant actually runs.

Before this package, ``app.main`` mounted every router unconditionally and the
only per-tenant gating was a scattered 404 dependency. Composition now has one
authority (:mod:`app.composition.capabilities`), one resolver
(:mod:`app.composition.profiles`), and one builder
(:mod:`app.composition.factory`).
"""

from app.composition.capabilities import Capability
from app.composition.factory import create_app
from app.composition.profiles import resolve_profile

__all__ = ["Capability", "create_app", "resolve_profile"]
