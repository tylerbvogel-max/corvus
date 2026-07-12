"""Risk category patterns and grounding reference pattern for the GTM domain."""

import re

RISK_CATEGORIES: dict[str, list[tuple[re.Pattern, str]]] = {
    "work_ip_leakage": [
        (re.compile(r"\b(?:Aurora|Boeing|AIP\b|work[- ]owned|employer)\b", re.I),
         "May reference work-track material; the personal/work IP wall runs both ways — work-owned strategy must never enter this personal graph"),
    ],
    "unverified_market_claim": [
        (re.compile(r"\b(?:guaranteed|always\s+works|every\s+(?:company|market)|proven\s+formula)\b", re.I),
         "Overclaims universality; GTM doctrine is stage- and context-dependent"),
        (re.compile(r"\b(?:I\s+think|probably|it\s+seems|might\s+be|untested)\b", re.I),
         "Hedged or speculative; keep at informational authority until applied"),
    ],
    "financial_specifics": [
        (re.compile(r"\$\s?\d{2,}[,\d]*(?:\s?(?:k|K|per\s+year|/yr))?", re.I),
         "Contains specific compensation or price figures; verify recency and source before relying on them"),
    ],
    "pii": [
        (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),
         "Contains an email address; redact before storage"),
    ],
}

GROUNDING_REF_PATTERN = re.compile(
    r'\b1[45]\.\d{3}[A-Za-z]?\b'                       # MIT course numbers
    r'|paulgraham\.com/\w+\.html'                       # PG essay slugs
    r'|review\.firstround\.com/[\w-]+'                  # First Round essays
    r'|\b(?:Obviously Awesome|Getting to Yes|Crossing the Chasm)\b',
    re.IGNORECASE,
)
