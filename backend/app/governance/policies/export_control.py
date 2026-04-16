"""Export-control policy — block responses containing tenant-flagged terms.

Tripwire for controlled vocabulary (ITAR / EAR / classification markings
in the aerospace tenant). A term hit defaults to a hard block because a
false negative here is a compliance incident. Tenants with no export
surface disable the rule entirely.

Configured via tenant.yaml ``output_policies.export_control``:
  enabled:   bool
  action:    flag | redact | block  (default block)
  severity:  info | warn | error | critical  (default critical)
  terms:     list[str]   (case-insensitive substring match)
"""

from __future__ import annotations

from app.governance.policies import (
    PolicyContext,
    ViolationDraft,
    _cfg_action,
    _cfg_severity,
)


# Cap so a misconfigured tenant can't ship 10k terms and stall the gate.
_MAX_TERMS = 256


class ExportControlPolicy:
    """Substring match (case-insensitive) against a tenant-supplied term list."""

    rule_id = "export_control"

    def __init__(self, cfg: dict) -> None:
        raw_terms = cfg.get("terms", []) or []
        assert isinstance(raw_terms, list), "export_control.terms must be a list"
        assert len(raw_terms) <= _MAX_TERMS, (
            f"export_control.terms exceeds {_MAX_TERMS} (got {len(raw_terms)})"
        )
        self.terms = tuple(
            t.lower().strip() for t in raw_terms
            if isinstance(t, str) and t.strip()
        )
        self.action = _cfg_action(cfg, default="block")
        self.severity = _cfg_severity(cfg, default="critical")

    def check(self, ctx: PolicyContext) -> list[ViolationDraft]:
        """Return one draft per distinct term hit in the response."""
        if not self.terms:
            return []
        haystack = ctx.response_text.lower()
        drafts: list[ViolationDraft] = []
        seen: set[str] = set()
        for term in self.terms:
            if term in seen:
                continue
            if term in haystack:
                seen.add(term)
                drafts.append(
                    ViolationDraft(
                        rule_id=self.rule_id,
                        severity=self.severity,
                        action=self.action,
                        matched_span=term,
                        redaction=None,
                        detail={"term": term},
                    )
                )
        return drafts
