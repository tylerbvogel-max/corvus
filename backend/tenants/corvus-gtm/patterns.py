"""Reference detection patterns for the go-to-market knowledge domain.

The "regulatory" analog here is source citation: course numbers, essay
slugs, book references, and case ids are what ground a GTM claim the way
a code section grounds a compliance claim.
"""

import re

REGULATORY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("COURSE",  re.compile(r'\b1[45]\.\d{3}[A-Za-z]?\b')),                      # MIT Sloan course numbers (15.390 etc.)
    ("ESSAY",   re.compile(r'\bpaulgraham\.com/\w+\.html|\bpg:\s*[a-z-]+\b', re.IGNORECASE)),
    ("REVIEW",  re.compile(r'\bfirst\s*round\s*review\b|\breview\.firstround\.com\b', re.IGNORECASE)),
    ("YC",      re.compile(r'\b(?:YC|y\s*combinator)\s+(?:library|startup\s+school|essay)\b', re.IGNORECASE)),
    ("BOOK",    re.compile(r'\b(?:Obviously Awesome|Crossing the Chasm|Getting to Yes|Never Split the Difference|Play Bigger)\b')),
    ("CASE",    re.compile(r'\bHBS\s+case\b|\bcase\s+\d{3}-\d{3}\b', re.IGNORECASE)),
]

TECHNICAL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("unit econ",   re.compile(r'\b(?:CAC|LTV|ACV|ARR|MRR|NRR|churn|payback)\b')),
    ("market size", re.compile(r'\b(?:TAM|SAM|SOM)\b')),
    ("motion",      re.compile(r'\b(?:PLG|product-led|founder-led\s+sales|self-serve|land-and-expand)\b', re.IGNORECASE)),
    ("segment",     re.compile(r'\bICP\b|\bideal\s+customer\s+profile\b', re.IGNORECASE)),
    ("negotiation", re.compile(r'\bBATNA\b|\banchor(?:ing)?\b', re.IGNORECASE)),
]
