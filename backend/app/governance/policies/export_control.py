"""Export-control policy — tiered term matching against the response text.

Tripwire for controlled vocabulary (ITAR / EAR / classification markings
in the aerospace tenant). Two tiers, because a marking and a topic are
not the same risk:

  block_terms — export-control MARKINGS ("USML", "restricted data").
                A hit halts the response; a false negative here is a
                compliance incident.
  flag_terms  — topical vocabulary ("propulsion", "guidance system")
                that legitimate process answers use constantly. A hit is
                recorded as an audit violation but never blocks.

All matching is word-boundary (regex), not substring — the original
substring matcher blocked benign answers because "ITAR" is inside
"military" and "classified" is inside "declassified".

Configured via tenant.yaml ``output_policies.export_control``:
  enabled:       bool
  action:        flag | redact | block   (block_terms + legacy terms; default block)
  severity:      info | warn | error | critical  (block_terms tier; default critical)
  flag_severity: severity for flag_terms hits (default warn)
  block_terms:   list[str]  — markings; get ``action``/``severity``
  flag_terms:    list[str]  — topical; always action=flag
  terms:         list[str]  — legacy list, treated like block_terms
"""

from __future__ import annotations

import re

from app.governance.policies import (
    PolicyContext,
    ViolationDraft,
    VALID_SEVERITIES,
    _cfg_action,
    _cfg_severity,
)


# Cap so a misconfigured tenant can't ship 10k terms and stall the gate.
_MAX_TERMS = 256


def _clean_terms(raw: object, key: str) -> tuple[str, ...]:
    """Validate one term list from config; empty/missing is fine."""
    if raw is None:
        return ()
    assert isinstance(raw, list), f"export_control.{key} must be a list"
    return tuple(t.strip() for t in raw if isinstance(t, str) and t.strip())


def _compile_term(term: str) -> re.Pattern:
    """Word-boundary, case-insensitive pattern for one term.

    Lookarounds instead of ``\\b`` so terms that start or end with a
    non-word character still anchor correctly. Internal whitespace
    matches any run of whitespace ("guidance  system" still hits).
    """
    assert term, "term must be non-empty"
    body = r"\s+".join(re.escape(w) for w in term.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


class ExportControlPolicy:
    """Tiered word-boundary match against tenant-supplied term lists."""

    rule_id = "export_control"

    def __init__(self, cfg: dict) -> None:
        self.action = _cfg_action(cfg, default="block")
        self.severity = _cfg_severity(cfg, default="critical")
        flag_severity = str(cfg.get("flag_severity", "warn")).lower()
        assert flag_severity in VALID_SEVERITIES, (
            f"invalid export_control.flag_severity: {flag_severity!r}"
        )
        block = _clean_terms(cfg.get("block_terms"), "block_terms")
        flag = _clean_terms(cfg.get("flag_terms"), "flag_terms")
        legacy = _clean_terms(cfg.get("terms"), "terms")
        total = len(block) + len(flag) + len(legacy)
        assert total <= _MAX_TERMS, (
            f"export_control term lists exceed {_MAX_TERMS} (got {total})"
        )
        # (pattern, term, action, severity) per term; legacy `terms` keep
        # the configured action so existing tenant configs behave as before
        # (minus the substring false positives).
        self._matchers = tuple(
            (_compile_term(t), t, action, severity)
            for terms, action, severity in (
                (block, self.action, self.severity),
                (legacy, self.action, self.severity),
                (flag, "flag", flag_severity),
            )
            for t in terms
        )

    def check(self, ctx: PolicyContext) -> list[ViolationDraft]:
        """Return one draft per distinct term hit in the response."""
        assert isinstance(ctx.response_text, str), "response_text must be a string"
        if not self._matchers:
            return []
        drafts: list[ViolationDraft] = []
        seen: set[str] = set()
        for pattern, term, action, severity in self._matchers:
            key = term.lower()
            if key in seen:
                continue
            match = pattern.search(ctx.response_text)
            if match:
                seen.add(key)
                drafts.append(
                    ViolationDraft(
                        rule_id=self.rule_id,
                        severity=severity,
                        action=action,
                        matched_span=match.group(0),
                        redaction=None,
                        detail={"term": term, "tier": action},
                    )
                )
        return drafts
