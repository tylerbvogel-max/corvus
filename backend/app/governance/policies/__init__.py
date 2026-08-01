"""Output policy implementations + shared base types.

Each policy is a small class with:
  - ``rule_id``:   canonical string used for tenant-config lookup + audit
  - ``check(ctx)``: returns a list of :class:`ViolationDraft` objects

A policy MUST NOT mutate the context. Redactions are expressed as
``matched_span`` + ``redaction`` on the draft; output_guard applies them.

The base types live in ``.base`` rather than here. They used to be defined in
this file, which forced every concrete policy to import the package back and
created the package-init cycle record 04 seam 3 removed. This module is now
purely a facade: it re-exports the base types and assembles the registry, so
every existing importer keeps working unchanged.
"""

from __future__ import annotations

from app.governance.policies.base import (
    PolicyContext,
    ViolationDraft,
    VALID_ACTIONS,
    VALID_SEVERITIES,
    _cfg_action,
    _cfg_severity,
)
from app.governance.policies.citation import CitationPolicy
from app.governance.policies.export_control import ExportControlPolicy
from app.governance.policies.pii import PIIPolicy


# Registry order = evaluation order (deterministic for tests).
POLICY_CLASSES = (CitationPolicy, PIIPolicy, ExportControlPolicy)


__all__ = [
    "CitationPolicy",
    "ExportControlPolicy",
    "PIIPolicy",
    "POLICY_CLASSES",
    "PolicyContext",
    "ViolationDraft",
    "VALID_ACTIONS",
    "VALID_SEVERITIES",
    "_cfg_action",
    "_cfg_severity",
]
