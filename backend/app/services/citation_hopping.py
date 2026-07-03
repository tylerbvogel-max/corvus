"""Frequency-hopped citation grounding — an anti-hallucination exit layer.

Entry layer: mint a random per-query ephemeral key (a "frequency") for each
neuron placed in the assembled prompt, building a secret ``{key: neuron_id}``
map. The analysis-layer LLM sees only the keys next to each source, never the
real neuron IDs and never the map. Exit layer: extract the keys the answer
actually cited and check them against the secret map. Any cited key that is
NOT in the map is a fabricated (hallucinated) neuron reference — caught
deterministically. Keys rotate every query (the "frequency hopping"), so a key
memorised or guessed from training or any other query will not validate.

Scope: this reduces the risk of hallucinated NEURON references. It does NOT
prove a correctly-keyed claim is entailed by the cited neuron — entailment is
a separate concern (see OVERHAUL-STATUS.md).

The module is pure (no DB, no I/O). Persistence + LLM repair live in the
executor / MCP layers that call these functions.
"""

import re
import secrets
from dataclasses import dataclass

from app.config import settings

# Bounded collision-retry when minting (JPL-2: bounded loops).
_MINT_MAX_ATTEMPTS = 8


@dataclass(frozen=True)
class HopMap:
    """Secret per-query key<->neuron mapping. Never exposed to the analysis layer."""

    token_by_neuron: dict[int, str]
    neuron_by_token: dict[str, int]

    def tokens(self) -> set[str]:
        return set(self.neuron_by_token.keys())


@dataclass(frozen=True)
class HopVerification:
    """Deterministic result of the exit-layer citation handshake."""

    ok: bool
    allowed: list[str]
    used: list[str]
    hallucinated: list[str]      # cited but not in the map = fabricated references
    missing: list[str]           # required but not cited (require_all mode only)
    cited_neuron_ids: list[int]  # neurons the answer actually grounded on

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "allowed": self.allowed,
            "used": self.used,
            "hallucinated": self.hallucinated,
            "missing": self.missing,
            "cited_neuron_ids": self.cited_neuron_ids,
        }


def _mint_token() -> str:
    """Mint one high-entropy uppercase token, e.g. ``FQ-7F3A2C``."""
    width = max(2, settings.citation_hop_hex_width)
    n_bytes = (width + 1) // 2
    return f"{settings.citation_hop_prefix}{secrets.token_hex(n_bytes).upper()[:width]}"


def _unique_token(existing: dict[str, int]) -> str:
    """Mint a token not already used in this query. Bounded retry + suffix fallback."""
    assert isinstance(existing, dict), "existing must be a dict"
    for _ in range(_MINT_MAX_ATTEMPTS):
        token = _mint_token()
        if token not in existing:
            return token
    # Astronomically unlikely fallback: deterministic, bounded disambiguation.
    base = _mint_token()
    for suffix in range(1, len(existing) + 2):
        candidate = f"{base}{suffix}"
        if candidate not in existing:
            return candidate
    return f"{base}{secrets.token_hex(2).upper()}"


def mint_hop_map(neuron_ids: list[int]) -> HopMap:
    """Assign a fresh random ephemeral key to each neuron id (order-independent)."""
    assert isinstance(neuron_ids, list), "neuron_ids must be a list"
    token_by_neuron: dict[int, str] = {}
    neuron_by_token: dict[str, int] = {}
    for nid in neuron_ids:
        assert isinstance(nid, int), "neuron id must be an int"
        if nid in token_by_neuron:
            continue
        token = _unique_token(neuron_by_token)
        token_by_neuron[nid] = token
        neuron_by_token[token] = nid
    return HopMap(token_by_neuron=token_by_neuron, neuron_by_token=neuron_by_token)


def _token_regex() -> re.Pattern:
    """Compile the citation-token matcher from the configured prefix + hex width."""
    prefix = re.escape(settings.citation_hop_prefix)
    width = max(2, settings.citation_hop_hex_width)
    return re.compile(prefix + "[0-9A-Fa-f]{" + str(width) + "}", re.IGNORECASE)


def extract_citation_tokens(text: str) -> list[str]:
    """Deterministically extract citation tokens from an answer.

    Matches bracketed (``[FQ-7F3A2C]``) or bare occurrences, normalises to
    upper case, and dedups preserving first-seen order.
    """
    assert isinstance(text, str), "text must be a string"
    seen: dict[str, None] = {}
    for match in _token_regex().finditer(text):
        seen.setdefault(match.group(0).upper(), None)
    return list(seen.keys())


def verify_citations(
    used: list[str], hop_map: HopMap, require_all: bool = False,
) -> HopVerification:
    """Check cited keys against the secret map. ``used ⊆ allowed`` always; plus
    ``allowed ⊆ used`` when ``require_all``. Any extra key = hallucination."""
    assert isinstance(used, list), "used must be a list"
    assert isinstance(hop_map, HopMap), "hop_map must be a HopMap"
    allowed_set = hop_map.tokens()
    used_set = {t.upper() for t in used}
    hallucinated = sorted(used_set - allowed_set)
    missing = sorted(allowed_set - used_set) if require_all else []
    cited_ids = sorted(
        {hop_map.neuron_by_token[t] for t in used_set if t in hop_map.neuron_by_token}
    )
    ok = not hallucinated and not missing
    return HopVerification(
        ok=ok, allowed=sorted(allowed_set), used=sorted(used_set),
        hallucinated=hallucinated, missing=missing, cited_neuron_ids=cited_ids,
    )


def strip_hallucinated(text: str, hallucinated: list[str]) -> str:
    """Remove fabricated citation tokens (and any surrounding brackets) from text."""
    assert isinstance(text, str), "text must be a string"
    out = text
    for token in hallucinated:
        out = re.sub(r"\[\s*" + re.escape(token) + r"\s*\]", "", out, flags=re.IGNORECASE)
        out = re.sub(re.escape(token), "", out, flags=re.IGNORECASE)
    return out


def repair_instruction(hop_map: HopMap, hallucinated: list[str]) -> str:
    """Build the one-shot repair directive listing the only valid keys."""
    assert isinstance(hop_map, HopMap), "hop_map must be a HopMap"
    valid = ", ".join(f"[{t}]" for t in sorted(hop_map.tokens()))
    bad = ", ".join(f"[{t}]" for t in hallucinated)
    return (
        f"Your previous answer cited citation keys that do not exist: {bad}. "
        f"The ONLY valid citation keys for this answer are: {valid}. "
        "Rewrite the answer citing only valid keys, and drop any claim you "
        "cannot attribute to a valid key."
    )
