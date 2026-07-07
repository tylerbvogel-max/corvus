"""Regulatory citation coverage detection.

When an answer cites a CFR reference that was NOT resolved from an engram this
query, that reference is un-grounded — the model reached for a regulation we
have no coverage for. This module extracts cited CFR refs, diffs them against
the resolved set, and queues the gaps into EmergentQueue (domain='regulatory',
priority 0.9 in the gap detector) so a controller (or autopilot) can create an
engram for them. Purely a detection/queue signal — nothing is auto-applied; the
write gate governs any resolution.
"""

import datetime
import json
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import EmergentQueue

# "48 CFR 31.205-6", "14 CFR 25.1309"
_CFR_RE = re.compile(r"\b(\d{1,2})\s+CFR\s+(\d+(?:\.\d+(?:-\d+)?)?)", re.IGNORECASE)
# FAR = 48 CFR ch.1, DFARS = 48 CFR ch.2 — both normalise to Title 48.
_ALIAS_RE = re.compile(r"\b(?:FAR|DFARS)\s+(\d+(?:\.\d+(?:-\d+)?)?)", re.IGNORECASE)


def extract_cfr_refs(text: str) -> set[str]:
    """Extract normalised CFR references ('N CFR X') cited in text."""
    assert isinstance(text, str), "text must be a string"
    refs: set[str] = set()
    for m in _CFR_RE.finditer(text):
        refs.add(f"{int(m.group(1))} CFR {m.group(2)}")
    for m in _ALIAS_RE.finditer(text):
        refs.add(f"48 CFR {m.group(1)}")  # FAR/DFARS live in Title 48
    return refs


# Non-CFR standard/spec families. Case-SENSITIVE (standards are uppercase in
# practice) so the English word "as"/"do" + a number can't false-match. Each
# normaliser strips revision letters so 'MIL-STD-1521B' == 'MIL-STD-1521'.
_STD_PATTERNS = (
    (re.compile(r"\b(MIL-(?:STD|HDBK|PRF|DTL|SPEC))-(\d+)"), lambda m: f"{m.group(1)}-{m.group(2)}"),
    (re.compile(r"\bAS\s?(\d{4})[A-Z]?\b"), lambda m: f"AS{m.group(1)}"),
    (re.compile(r"\bDO-(\d{3})[A-Z]?\b"), lambda m: f"DO-{m.group(1)}"),
    (re.compile(r"\bNAS\s?(\d{2,4})\b"), lambda m: f"NAS{m.group(1)}"),
    (re.compile(r"\bISO\s?(\d{3,5})\b"), lambda m: f"ISO{m.group(1)}"),
    (re.compile(r"\bASME\s+(Y?\d+(?:\.\d+)?)"), lambda m: f"ASME {m.group(1)}"),
    (re.compile(r"\bNIST\s+SP\s+(\d+-\d+)"), lambda m: f"NIST SP {m.group(1)}"),
    (re.compile(r"\bNADCAP\b"), lambda m: "NADCAP"),
    (re.compile(r"\bCMMC\b"), lambda m: "CMMC"),
)


def extract_standard_refs(text: str) -> set[str]:
    """Extract normalised regulation/standard/spec references — CFR/FAR/DFARS plus
    MIL-STD, AS####, DO-###, NAS, ISO, ASME, NIST SP, NADCAP, CMMC."""
    assert isinstance(text, str), "text must be a string"
    refs = set(extract_cfr_refs(text))
    for pattern, norm in _STD_PATTERNS:
        for m in pattern.finditer(text):
            refs.add(norm(m))
    return refs


def count_ungrounded_refs(answer: str, context_text: str) -> int:
    """Count regulation/standard references named in the answer that do NOT appear
    in the retrieved context — authority the model invoked without grounding (the
    'per MIL-STD-1521' case the frequency-hop layer can't see since it's not an
    [FQ-] key). Deterministic; conservative to avoid spurious flags."""
    if not answer or not context_text:
        return 0
    return len(extract_standard_refs(answer) - extract_standard_refs(context_text))


def _cfr_family(ref: str) -> str | None:
    """Title+part family label, e.g. '48 CFR 31' from '48 CFR 31.205-6'."""
    m = re.match(r"(\d+)\s+CFR\s+(\d+)", ref)
    return f"{m.group(1)} CFR {m.group(2)}" if m else None


async def _upsert_coverage_gap(db: AsyncSession, ref: str, query_id: int | None) -> None:
    """Create or bump an EmergentQueue entry for an un-grounded CFR reference."""
    existing = (await db.execute(
        select(EmergentQueue).where(EmergentQueue.citation_pattern == ref)
    )).scalar_one_or_none()
    if existing is not None:
        if existing.status != "resolved":
            existing.detection_count += 1
            existing.last_detected_at = datetime.datetime.utcnow()
            if query_id is not None:
                ids = json.loads(existing.detected_in_query_ids or "[]")
                if query_id not in ids:
                    ids.append(query_id)
                    existing.detected_in_query_ids = json.dumps(ids)
        return
    db.add(EmergentQueue(
        citation_pattern=ref,
        domain="regulatory",
        family=_cfr_family(ref),
        detection_count=1,
        detected_in_query_ids=json.dumps([query_id] if query_id is not None else []),
    ))


async def record_regulatory_coverage_gaps(
    db: AsyncSession, answer: str, resolved_cfr_refs: set[str], query_id: int | None,
) -> list[str]:
    """Queue CFR refs cited in the answer but not resolved this query.

    Returns the uncovered refs (also the audit signal). No-op when disabled or
    when the answer cites nothing un-grounded (the common case, so no DB work).
    """
    if not settings.regulatory_coverage_detection_enabled or not answer:
        return []
    resolved = {r.strip() for r in resolved_cfr_refs}
    uncovered = sorted(extract_cfr_refs(answer) - resolved)
    for ref in uncovered:
        await _upsert_coverage_gap(db, ref, query_id)
    return uncovered
