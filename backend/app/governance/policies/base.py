"""Shared base types and config helpers for output policies.

Extracted from ``app/governance/policies/__init__.py`` by roadmap record
durability-modular-monolith (04, seam 3) to break the package-init cycle.

The cycle was the ordinary Python one: the package ``__init__`` defined the base
types AND imported the three concrete policies at the bottom to build
``POLICY_CLASSES``, while each concrete policy imported those base types back
from the package. The ``# noqa: E402`` on those bottom imports was the
confession — they sat after executable code specifically so the names existed
before the leaves asked for them.

This module imports nothing from the package, so the direction is now one-way:

    __init__  ->  base
    __init__  ->  citation | pii | export_control  ->  base

``_cfg_action`` and ``_cfg_severity`` keep their leading underscore. Unlike
``_FRESHNESS_SQL`` in seam 2, they are genuinely package-internal — only sibling
policies in this directory call them — so the underscore states a true boundary
rather than hiding a crossed one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PolicyContext:
    """Read-only view passed to each policy's ``check`` method."""

    response_text: str
    firings: list
    tenant_policy: dict  # the tenant config block for this rule


@dataclass
class ViolationDraft:
    """A potential violation produced by a policy.

    Turned into an :class:`app.models.OutputViolation` row by output_guard.
    ``action`` controls response-time behavior; ``severity`` is audit-only.
    """

    rule_id: str
    severity: str  # "info" | "warn" | "error" | "critical"
    action: str    # "flag" | "redact" | "block"
    matched_span: str | None = None
    redaction: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


VALID_SEVERITIES = frozenset({"info", "warn", "error", "critical"})
VALID_ACTIONS = frozenset({"flag", "redact", "block"})


def _cfg_action(cfg: dict, default: str) -> str:
    """Extract ``action`` from a policy config block with validation."""
    value = str(cfg.get("action", default)).lower()
    assert value in VALID_ACTIONS, f"invalid policy action: {value!r}"
    return value


def _cfg_severity(cfg: dict, default: str) -> str:
    """Extract ``severity`` from a policy config block with validation."""
    value = str(cfg.get("severity", default)).lower()
    assert value in VALID_SEVERITIES, f"invalid policy severity: {value!r}"
    return value
