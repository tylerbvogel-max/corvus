"""Opt-in claim-entailment grounding check (grounding backlog §6.4).

A valid [FQ] citation key proves the cited source was IN CONTEXT — not that
the claim is ENTAILED by it (citation_hopping.py explicitly leaves entailment
out of scope). This pass judges each cited claim of the PRIMARY answer against
the content of the source(s) it cites, catching the failure mode "cited a real
source, but the source doesn't say that."

Cost: ONE extra LLM call per query — all claims are judged in a single batched
prompt — so the pass is opt-in (settings.entailment_check_enabled) and runs on
the primary answer only, never per compare slot. Failure behavior: any error
(no hop session, LLM failure, unparseable verdict) degrades to a status dict —
the query result is never blocked or mutated by this check; it is advisory.
"""

import json
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import release_connection_before_external_io
from app.models import CitationHopSession, Engram, Neuron, Query
from app.services.citation_hopping import (
    HopMap, deserialize_hop_map, extract_citation_tokens, strip_hallucinated,
)
from app.services.llm_provider import llm_chat

logger = logging.getLogger(__name__)

# Sentence boundary: end punctuation + whitespace, or hard newlines (list items).
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
# A claim shorter than this after token-stripping is a fragment, not a claim.
_MIN_CLAIM_CHARS = 20

_JUDGE_SYSTEM_PROMPT = (
    "You are a strict entailment judge for a citation-grounded QA system. "
    "You will receive numbered pairs, each with a CLAIM (one sentence from an "
    "answer) and the SOURCE excerpt(s) that sentence cited. For each pair "
    "decide whether the sources genuinely support the claim as stated. "
    "Paraphrase is fine; the claim does not need to quote the source. But if "
    "the claim asserts specifics (numbers, requirements, named authorities, "
    "causal relationships) that the sources do not state, it is unsupported.\n\n"
    "Respond with ONLY a JSON object, no prose, in exactly this shape:\n"
    '{"verdicts": [{"pair": 0, "supported": true, "reason": "<one line>"}, ...]}\n'
    "Include every pair number you were given exactly once."
)


def extract_cited_claims(answer: str, max_claims: int) -> list[tuple[str, list[str]]]:
    """Split the answer into sentences and keep those citing ≥1 [FQ] token.

    Returns up to max_claims (claim_text_without_tokens, [tokens]) pairs.
    Pure — no I/O. Bounded by the sentence count of the answer (JPL-2).
    """
    assert isinstance(answer, str), "answer must be a string"
    assert max_claims > 0, "max_claims must be positive"

    claims: list[tuple[str, list[str]]] = []
    for sentence in _SENTENCE_SPLIT.split(answer):
        tokens = extract_citation_tokens(sentence)
        if not tokens:
            continue
        clean = strip_hallucinated(sentence, tokens).strip()
        if len(clean) < _MIN_CLAIM_CHARS:
            continue
        # A colon-terminated lead-in ("you must demonstrate:") is an intro to a
        # list, not a self-contained claim — judging it alone yields noise.
        if clean.endswith(":"):
            continue
        # Markdown headings are section titles, not claims — their citations
        # decorate structure. Calibration (2026-07, 131 claims): headings
        # scored 0.01-0.15 against sources they truthfully introduced.
        if clean.lstrip().startswith("#"):
            continue
        claims.append((clean, tokens))
        if len(claims) >= max_claims:
            break
    return claims


async def _load_source_texts(db: AsyncSession, hop_map: HopMap) -> dict[str, tuple[str, str]]:
    """Resolve each hop token to (label, content excerpt) for the judge prompt.

    Engrams prefer cached_text (the actual regulation text); neurons prefer
    content over summary over label. Excerpts are truncated to keep the single
    judge call bounded in size.
    """
    limit = settings.entailment_source_chars
    assert limit > 0, "entailment_source_chars must be positive"
    out: dict[str, tuple[str, str]] = {}

    neuron_ids = list(hop_map.token_by_neuron.keys())
    if neuron_ids:
        rows = (await db.execute(select(Neuron).where(Neuron.id.in_(neuron_ids)))).scalars()
        for n in rows:
            text = (n.content or n.summary or n.label or "")[:limit]
            out[hop_map.token_by_neuron[n.id]] = (n.label, text)

    engram_ids = list(hop_map.token_by_engram.keys())
    if engram_ids:
        rows = (await db.execute(select(Engram).where(Engram.id.in_(engram_ids)))).scalars()
        for e in rows:
            text = (e.cached_text or e.content or e.summary or e.label or "")[:limit]
            out[hop_map.token_by_engram[e.id]] = (e.label, text)
    return out


def _build_judge_message(pairs: list[tuple[str, list[tuple[str, str]]]]) -> str:
    """Format (claim, [(source_label, source_text), ...]) pairs for the judge."""
    assert len(pairs) > 0, "pairs must be non-empty"
    blocks: list[str] = []
    for i, (claim, sources) in enumerate(pairs):
        src_lines = "\n".join(
            f"  SOURCE ({label}): {text}" for label, text in sources
        )
        blocks.append(f"=== Pair {i} ===\nCLAIM: {claim}\n{src_lines}")
    return "\n\n".join(blocks)


def _parse_verdicts(raw: str, n_pairs: int) -> list[dict] | None:
    """Extract the judge's JSON verdicts. Returns None if unparseable."""
    assert n_pairs > 0, "n_pairs must be positive"
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None
    verdicts = payload.get("verdicts")
    if not isinstance(verdicts, list):
        return None
    by_pair: dict[int, dict] = {}
    for v in verdicts:
        if isinstance(v, dict) and isinstance(v.get("pair"), int):
            by_pair[v["pair"]] = v
    return [by_pair.get(i, {}) for i in range(n_pairs)]


async def _load_hop_map_for_query(db: AsyncSession, query_id: int) -> HopMap | None:
    """Load the persisted secret hop map for a query, if a session exists."""
    assert query_id > 0, "query_id must be positive"
    query = (await db.execute(select(Query).where(Query.id == query_id))).scalar_one_or_none()
    if query is None or not query.citation_hop_session_id:
        return None
    session = (await db.execute(
        select(CitationHopSession).where(CitationHopSession.id == query.citation_hop_session_id)
    )).scalar_one_or_none()
    if session is None:
        return None
    return deserialize_hop_map(session.token_map_json)


def _summarize(results: list[dict], cost_usd: float) -> dict:
    """Assemble the advisory result dict from per-claim judgments."""
    unsupported = [r for r in results if r.get("supported") is False]
    return {
        "checked": len(results),
        "unsupported_count": len(unsupported),
        "results": results,
        "cost_usd": cost_usd,
        "status": "ok",
    }


async def run_entailment_check(db: AsyncSession, response_text: str, query_id: int | None) -> dict:
    """Judge each cited claim of the primary answer against its cited sources.

    Advisory only — always returns a status dict, never raises past itself and
    never mutates the answer. One batched LLM call for all claims.
    """
    assert isinstance(response_text, str), "response_text must be a string"
    if not response_text or not query_id:
        return {"checked": 0, "status": "no_answer"}

    hop_map = await _load_hop_map_for_query(db, query_id)
    if hop_map is None or not hop_map.tokens():
        return {"checked": 0, "status": "no_hop_session"}

    claims = extract_cited_claims(response_text, settings.entailment_max_claims)
    if not claims:
        return {"checked": 0, "status": "no_cited_claims"}

    sources = await _load_source_texts(db, hop_map)
    pairs: list[tuple[str, list[tuple[str, str]]]] = []
    for claim, tokens in claims:
        resolved = [sources[t] for t in tokens if t in sources]
        if resolved:
            pairs.append((claim, resolved))
    if not pairs:
        return {"checked": 0, "status": "no_resolvable_sources"}

    # Source hydration is complete. Return its pooled connection before the
    # optional judge subprocess waits; this function performs no DB writes.
    await release_connection_before_external_io(db)

    try:
        llm_result = await llm_chat(
            system_prompt=_JUDGE_SYSTEM_PROMPT,
            user_message=_build_judge_message(pairs),
            max_tokens=2048,
            model=settings.entailment_check_model,
        )
    except (RuntimeError, AssertionError, OSError, ValueError) as e:
        logger.warning("entailment check LLM call failed: %s", e)
        return {"checked": 0, "status": "llm_error", "error": str(e)[:200]}

    verdicts = _parse_verdicts(llm_result.get("text", ""), len(pairs))
    if verdicts is None:
        return {"checked": 0, "status": "parse_error", "cost_usd": llm_result.get("cost_usd", 0.0)}

    results = [{
        "claim": claim[:300],
        "sources": [label for label, _ in srcs],
        "supported": v.get("supported") if isinstance(v.get("supported"), bool) else None,
        "reason": str(v.get("reason", ""))[:300],
    } for (claim, srcs), v in zip(pairs, verdicts)]
    return _summarize(results, llm_result.get("cost_usd", 0.0))
