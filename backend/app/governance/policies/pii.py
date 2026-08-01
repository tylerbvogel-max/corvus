"""PII policy — detect common personal-identifier patterns in LLM output.

Three regex families (email, US phone, US SSN) cover the majority of
accidental-leak cases in customer-facing responses. This is a tripwire,
not a substitute for a full DLP solution — tune thresholds or disable
in tenant.yaml if the domain produces legitimate PII-shaped strings.

Configured via tenant.yaml ``output_policies.pii``:
  enabled:  bool
  action:   flag | redact | block  (default redact)
  severity: info | warn | error | critical  (default error)
"""

from __future__ import annotations

import re
from types import MappingProxyType

from app.governance.policies.base import (
    PolicyContext,
    ViolationDraft,
    _cfg_action,
    _cfg_severity,
)


# Compile once at module load. MappingProxyType keeps the patterns
# immutable (JPL-6 friendly).
_PII_PATTERNS = MappingProxyType({
    "email": re.compile(
        r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b",
    ),
    "us_phone": re.compile(
        r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b",
    ),
    "us_ssn": re.compile(
        r"\b\d{3}-\d{2}-\d{4}\b",
    ),
})


# Bound the regex scan so a pathological input can't stall the gate.
_MAX_MATCHES_PER_KIND = 32


def _redact(span: str, kind: str) -> str:
    """Return the masked form of a matched PII span."""
    return f"[REDACTED:{kind}]"


class PIIPolicy:
    """Flags email / US phone / US SSN strings in the LLM response."""

    rule_id = "pii"

    def __init__(self, cfg: dict) -> None:
        self.action = _cfg_action(cfg, default="redact")
        self.severity = _cfg_severity(cfg, default="error")

    def check(self, ctx: PolicyContext) -> list[ViolationDraft]:
        """Return one draft per PII match, capped per pattern family."""
        drafts: list[ViolationDraft] = []
        for kind, pattern in _PII_PATTERNS.items():
            matches = pattern.findall(ctx.response_text)[:_MAX_MATCHES_PER_KIND]
            for match in matches:
                drafts.append(
                    ViolationDraft(
                        rule_id=self.rule_id,
                        severity=self.severity,
                        action=self.action,
                        matched_span=match,
                        redaction=_redact(match, kind),
                        detail={"kind": kind},
                    )
                )
        return drafts
