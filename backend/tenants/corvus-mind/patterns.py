"""Reference detection patterns for the agentic-memory domain.

The "regulatory" analog here is explicit evidence citation: session ids,
file:line references, exit codes, and memory-slug links are what ground a
memory claim the way a code section grounds a compliance claim.
"""

import re

REGULATORY_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("SESSION",  re.compile(r'\bsession[:\s]+[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b', re.IGNORECASE)),
    ("FILE-REF", re.compile(r'\b[\w./-]+\.(?:py|ts|tsx|js|md|yaml|yml|json|sh|sql):\d+\b')),
    ("EXIT",     re.compile(r'\bexit(?:\s+code)?\s+\d+\b', re.IGNORECASE)),
    ("MEM-LINK", re.compile(r'\[\[[a-z0-9][a-z0-9_-]+\]\]')),
    ("EPISODE",  re.compile(r'\bepisode[:\s]+[0-9a-f-]{8,}\b', re.IGNORECASE)),
]

TECHNICAL_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("Claude CLI",  re.compile(r'\bclaude\s+(?:-p|--print|mcp|--output-format|--strict-mcp-config)\b')),
    ("systemd",     re.compile(r'\bsystemctl\s+(?:--user\s+)?\w+|\b[\w-]+\.(?:service|timer)\b')),
    ("SQLAlchemy",  re.compile(r'\b(?:select|Session|mapped_column|Mapped)\s*[\(\[]')),
    ("FastAPI",     re.compile(r'\b(?:Depends|APIRouter|@router\.)\w*')),
    ("React",       re.compile(r'\buse[A-Z]\w+\s*\(')),
    ("Python",      re.compile(r'\b(?:asyncio|dataclasses|typing|collections|functools|itertools)\.\w+')),
    ("Git",         re.compile(r'\bgit\s+(?:push|pull|rebase|cherry-pick|bisect|worktree)\b')),
]
