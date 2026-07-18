"""Secret redaction for evidence packets (reconsolidation auditor).

Evidence packets replay raw episode events and transcript turns into an
LLM critic prompt. Those sources are unfiltered shell/tool output —
credentials pasted into a terminal or read from an env file would
otherwise ride straight into the critic call and its persisted ledger.
Nothing else in the distill→write path scrubs secrets (verified
2026-07-18), so this module is the single chokepoint: redact BEFORE the
text enters a packet, a prompt, or a durable record.

Patterns favor recall over precision — a false [REDACTED] costs a little
evidence fidelity; a leaked credential in the auditor ledger is an
incident. Every replacement names its kind so a reviewer can still see
WHAT was there.
"""

from __future__ import annotations

import re

# (kind, pattern) — first match wins per span; applied in order.
_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("private-key", re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{10,}\b")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("google-key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("jwt", re.compile(
        r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}\b")),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*")),
    ("basic-auth-url", re.compile(r"(?i)\b(\w+://)[^/\s:@]+:[^/\s:@]+@")),
    ("assignment", re.compile(
        r"(?i)\b((?:api[_-]?key|secret|token|passwd|password|credential)s?"
        r"[\w-]*)\s*[=:]\s*[\"']?[A-Za-z0-9._~+/-]{8,}[\"']?")),
)

_MAX_TEXT = 200_000  # bound the scan; packets are bounded far below this


def redact(text: str) -> str:
    """Replace secret-shaped spans with [REDACTED:<kind>] markers."""
    if not text:
        return text
    out = text[:_MAX_TEXT]
    for kind, pattern in _PATTERNS:
        if kind == "basic-auth-url":
            out = pattern.sub(rf"\1[REDACTED:{kind}]@", out)
        elif kind == "assignment":
            out = pattern.sub(rf"\1=[REDACTED:{kind}]", out)
        else:
            out = pattern.sub(f"[REDACTED:{kind}]", out)
    return out


def redact_obj(value):
    """Recursively redact every string in a JSON-shaped structure."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_obj(v) for v in value]
    if isinstance(value, dict):
        return {k: redact_obj(v) for k, v in value.items()}
    return value
