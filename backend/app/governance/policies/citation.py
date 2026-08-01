"""Citation policy — every answer must cite at least N included neuron firings.

Rationale: Corvus's defensibility claim is that every answer traces back to
graph provenance. An answer produced with zero included firings is either a
pipeline bug or a hallucination leaking through an empty-context path; either
way, it should be flagged.

Configured via tenant.yaml ``output_policies.citation``:
  enabled:        bool
  min_citations:  int     (default 1)
  action:         flag | redact | block  (default flag)
  severity:       info | warn | error | critical  (default warn)
"""

from __future__ import annotations

from app.governance.policies.base import (
    PolicyContext,
    ViolationDraft,
    _cfg_action,
    _cfg_severity,
)


class CitationPolicy:
    """At least ``min_citations`` included firings must back the response."""

    rule_id = "citation"

    def __init__(self, cfg: dict) -> None:
        self.min_citations = int(cfg.get("min_citations", 1))
        assert self.min_citations >= 0, "min_citations must be non-negative"
        self.action = _cfg_action(cfg, default="flag")
        self.severity = _cfg_severity(cfg, default="warn")

    def check(self, ctx: PolicyContext) -> list[ViolationDraft]:
        """Return a violation if too few firings were included in the answer."""
        included = [f for f in ctx.firings if getattr(f, "was_included", False)]
        if len(included) >= self.min_citations:
            return []
        return [
            ViolationDraft(
                rule_id=self.rule_id,
                severity=self.severity,
                action=self.action,
                matched_span=None,
                redaction=None,
                detail={
                    "required": self.min_citations,
                    "found": len(included),
                    "total_scored": len(ctx.firings),
                },
            )
        ]
