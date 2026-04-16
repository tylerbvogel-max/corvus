"""Output policy implementations + shared base types.

Each policy is a small class with:
  - ``rule_id``:   canonical string used for tenant-config lookup + audit
  - ``check(ctx)``: returns a list of :class:`ViolationDraft` objects

A policy MUST NOT mutate the context. Redactions are expressed as
``matched_span`` + ``redaction`` on the draft; output_guard applies them.
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


from app.governance.policies.citation import CitationPolicy  # noqa: E402
from app.governance.policies.export_control import ExportControlPolicy  # noqa: E402
from app.governance.policies.pii import PIIPolicy  # noqa: E402


# Registry order = evaluation order (deterministic for tests).
POLICY_CLASSES = (CitationPolicy, PIIPolicy, ExportControlPolicy)


__all__ = [
    "CitationPolicy",
    "ExportControlPolicy",
    "PIIPolicy",
    "POLICY_CLASSES",
    "PolicyContext",
    "ViolationDraft",
    "_cfg_action",
    "_cfg_severity",
]
