// Ungrounded-authority inline marking (grounding backlog §6.3).
//
// The backend decides WHICH normalised standard/regulation refs are ungrounded
// (SlotResult.ungrounded_ref_list, computed against the real assembled prompt).
// This module only LOCATES occurrences of those refs in the rendered answer so
// they can be visually marked. The patterns are a direct port of
// backend/app/services/regulatory_coverage.py (_CFR_RE, _ALIAS_RE,
// _STD_PATTERNS) — keep the two in sync, including case-sensitivity: CFR/FAR
// aliases are case-insensitive, the standard families are case-SENSITIVE so the
// English words "as"/"do" + a number can't false-match.

type Normalizer = (m: RegExpExecArray) => string;

// "48 CFR 31.205-6", "14 CFR 25.1309" → "48 CFR 31.205-6"
const CFR_RE = /\b(\d{1,2})\s+CFR\s+(\d+(?:\.\d+(?:-\d+)?)?)/gi;
// FAR/DFARS both normalise to Title 48
const ALIAS_RE = /\b(?:FAR|DFARS)\s+(\d+(?:\.\d+(?:-\d+)?)?)/gi;

const PATTERNS: Array<[RegExp, Normalizer]> = [
  [CFR_RE, m => `${parseInt(m[1], 10)} CFR ${m[2]}`],
  [ALIAS_RE, m => `48 CFR ${m[1]}`],
  // Visible match consumes a trailing revision letter (MIL-STD-1521B) so the
  // mark wraps the whole designator; the normaliser drops it, like the backend.
  [/\b(MIL-(?:STD|HDBK|PRF|DTL|SPEC))-(\d+)[A-Z]?\b/g, m => `${m[1]}-${m[2]}`],
  [/\bAS\s?(\d{4})[A-Z]?\b/g, m => `AS${m[1]}`],
  [/\bDO-(\d{3})[A-Z]?\b/g, m => `DO-${m[1]}`],
  [/\bNAS\s?(\d{2,4})\b/g, m => `NAS${m[1]}`],
  [/\bISO\s?(\d{3,5})\b/g, m => `ISO${m[1]}`],
  [/\bASME\s+(Y?\d+(?:\.\d+)?)/g, m => `ASME ${m[1]}`],
  [/\bNIST\s+SP\s+(\d+-\d+)/g, m => `NIST SP ${m[1]}`],
  [/\bNADCAP\b/g, () => 'NADCAP'],
  [/\bCMMC\b/g, () => 'CMMC'],
];

const MARK_TITLE =
  'Named as authority but NOT in the retrieved context — grounding unverified';

interface Hit {
  start: number;
  end: number;
}

function collectHits(text: string, ungrounded: Set<string>): Hit[] {
  const hits: Hit[] = [];
  for (const [re, norm] of PATTERNS) {
    re.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text)) !== null) {
      if (ungrounded.has(norm(m))) {
        hits.push({ start: m.index, end: m.index + m[0].length });
      }
      // Zero-width safety: exec on a global regex always advances lastIndex
      // here because every pattern consumes ≥1 char, but guard anyway.
      if (m.index === re.lastIndex) re.lastIndex++;
    }
  }
  // Sort and drop overlaps (e.g. an alias match nested in a CFR match)
  hits.sort((a, b) => a.start - b.start || b.end - a.end);
  const merged: Hit[] = [];
  for (const h of hits) {
    const last = merged[merged.length - 1];
    if (last && h.start < last.end) continue;
    merged.push(h);
  }
  return merged;
}

function markTextSegment(text: string, ungrounded: Set<string>): string {
  const hits = collectHits(text, ungrounded);
  if (hits.length === 0) return text;
  let out = '';
  let pos = 0;
  for (const h of hits) {
    out += text.slice(pos, h.start);
    out += `<mark class="ungrounded-ref" title="${MARK_TITLE}">${text.slice(h.start, h.end)}</mark>`;
    pos = h.end;
  }
  return out + text.slice(pos);
}

/**
 * Wrap occurrences of backend-flagged ungrounded refs in rendered answer HTML
 * with <mark class="ungrounded-ref">. Operates only on text between tags, so
 * tag names/attributes are never touched. No-op when the list is empty.
 */
export function markUngroundedRefs(html: string, ungroundedRefs: string[] | undefined): string {
  if (!ungroundedRefs || ungroundedRefs.length === 0) return html;
  const set = new Set(ungroundedRefs);
  return html
    .split(/(<[^>]*>)/g)
    .map(seg => (seg.startsWith('<') ? seg : markTextSegment(seg, set)))
    .join('');
}
