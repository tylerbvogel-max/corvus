"""Layer-2 citation grounding: semantic relevance of cited claims (log-only).

Sits between the existence check (citation hopping: "did you see this
source?") and the LLM entailment judge ("does the source support this
claim?"): for each answer sentence that cites [FQ] keys, score the claim
against the cited sources' content via local-embedding cosine, max-pooled
over sentence windows of the source. Deterministic, one batched local
encode (~100-300ms), $0.

What it catches: wrong-source citation — a valid key attached to a claim
the source has nothing to do with. What it CANNOT catch: topically-aligned
misrepresentation (right source, wrong number/negation) — embeddings are
nearly blind to those; that is the entailment layer's job.

Phase 2 (calibrated 2026-07): three-band routing. Scores below the flag
floor get an advisory "flag" band (never stripped — the prose is
untouched); scores above the pass threshold pass; the ambiguous band
between is escalated to the LLM entailment judge in one batched call, so
genuine support-checking runs by default at a fraction of always-on cost.
Bands are pure geometry over the score distribution — nothing here is
domain- or industry-specific.
"""

from __future__ import annotations

import re

from app.config import settings
from app.services.embedding_service import embed_batch, cosine_similarity
from app.services.entailment_check import (
    _JUDGE_SYSTEM_PROMPT,
    _build_judge_message,
    _parse_verdicts,
    extract_cited_claims,
)
from app.services.llm_provider import llm_chat


# Two-sentence windows keep the claim-vs-source comparison length-symmetric;
# window count per source is bounded so a pathological source can't stall
# the exit layer (JPL-2).
_WINDOW_SENTS = 2
_MAX_WINDOWS_PER_SOURCE = 12
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    assert isinstance(text, str), "text must be a string"
    return [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]


def _sentence_windows(text: str) -> list[str]:
    """Overlapping windows of _WINDOW_SENTS sentences (stride 1), bounded."""
    sents = _split_sentences(text)
    if not sents:
        return []
    if len(sents) <= _WINDOW_SENTS:
        return [" ".join(sents)]
    windows = [
        " ".join(sents[i:i + _WINDOW_SENTS])
        for i in range(len(sents) - _WINDOW_SENTS + 1)
    ]
    return windows[:_MAX_WINDOWS_PER_SOURCE]


def _token_candidate_texts(
    token: str, hop_map, neuron_map: dict, regs_by_id: dict,
) -> list[str]:
    """Comparison texts for one cited key: label/summary + content windows.

    Max-pooling over several granularities absorbs the length asymmetry
    between a one-sentence claim and a multi-paragraph source.
    """
    nid = hop_map.neuron_by_token.get(token)
    if nid is not None:
        neuron = neuron_map.get(nid)
        if neuron is None:
            return []
        texts = [t for t in (neuron.label, neuron.summary) if t]
        texts.extend(_sentence_windows(neuron.content or ""))
        return texts
    eid = hop_map.engram_by_token.get(token)
    if eid is not None:
        reg = regs_by_id.get(eid)
        if reg is None:
            return []
        return [reg.cfr_ref] + _sentence_windows(reg.text or "")
    return []


def score_citation_relevance(
    answer: str, hop_map, neuron_map: dict, resolved_regulations: list,
    max_claims: int,
) -> dict | None:
    """Score every cited claim in the answer against its cited sources.

    Returns None when the answer cites nothing. Otherwise a calibration
    payload: per-claim max-pooled cosine plus min/mean aggregates. Pure CPU
    (local embedder) — callers run it off the event loop.
    """
    assert isinstance(answer, str), "answer must be a string"
    assert max_claims > 0, "max_claims must be positive"
    claims = extract_cited_claims(answer, max_claims)
    if not claims or hop_map is None:
        return None

    regs_by_id = {r.engram_id: r for r in (resolved_regulations or [])}
    texts_by_token: dict[str, list[str]] = {}
    for _claim, tokens in claims:
        for token in tokens:
            if token not in texts_by_token:
                texts_by_token[token] = _token_candidate_texts(
                    token, hop_map, neuron_map, regs_by_id)

    # One batched encode: all claims + all candidate windows.
    claim_texts = [c for c, _t in claims]
    flat_candidates = [t for texts in texts_by_token.values() for t in texts]
    if not flat_candidates:
        return None
    vectors = embed_batch(claim_texts + flat_candidates)
    claim_vecs = vectors[:len(claim_texts)]
    vec_by_token: dict[str, list] = {}
    pos = len(claim_texts)
    for token, texts in texts_by_token.items():
        vec_by_token[token] = vectors[pos:pos + len(texts)]
        pos += len(texts)

    results = []
    for (claim_text, tokens), cvec in zip(claims, claim_vecs):
        candidates = [v for t in tokens for v in vec_by_token.get(t, [])]
        score = max(
            (cosine_similarity(cvec, v) for v in candidates), default=None,
        )
        results.append({
            "claim": claim_text[:200],
            "tokens": tokens,
            "score": round(score, 4) if score is not None else None,
        })

    scored = [r["score"] for r in results if r["score"] is not None]
    return {
        "checked": len(results),
        "scored": len(scored),
        "min": min(scored) if scored else None,
        "mean": round(sum(scored) / len(scored), 4) if scored else None,
        "claims": results,
    }


def apply_relevance_bands(payload: dict) -> dict:
    """Annotate each scored claim with its routing band (pure geometry).

    flag   — below the calibrated floor: almost no true citations score here.
    verify — the ambiguous band: routed to the entailment judge.
    pass   — above the pass threshold: wrong-source pairs rarely reach it.
    Unscored claims (unresolvable keys) get no band — layer 1 owns those.
    """
    floor = settings.citation_relevance_flag_floor
    pass_t = settings.citation_relevance_pass_threshold
    assert 0.0 <= floor < pass_t <= 1.0, "bands must satisfy 0 <= floor < pass <= 1"
    for c in payload.get("claims", []):
        score = c.get("score")
        if score is None:
            c["band"] = None
        elif score < floor:
            c["band"] = "flag"
        elif score >= pass_t:
            c["band"] = "pass"
        else:
            c["band"] = "verify"
    bands = [c.get("band") for c in payload.get("claims", [])]
    payload["flagged"] = bands.count("flag")
    payload["escalated"] = 0
    payload["unsupported"] = 0
    return payload


def _centered_excerpt(claim_vec, text: str, limit: int) -> str:
    """Excerpt of `text` centered on the sentences that best match the claim.

    Head-truncation (text[:limit]) made the judge's evidence depend on WHERE
    in a long source the relevant passage lives — a true claim supported by
    paragraph four was ruled unsupported because the judge saw only paragraph
    one (observed live, 2026-07-08). The relevance layer already knows how to
    find the matching region: embed the source's sentence windows and center
    the excerpt on the argmax. Ellipses mark cut edges so the judge knows it
    is reading a window, not a whole document.
    """
    sents = _split_sentences(text)
    if not sents:
        return text[:limit]
    windows = [
        " ".join(sents[i:i + _WINDOW_SENTS])
        for i in range(max(len(sents) - _WINDOW_SENTS + 1, 1))
    ]
    vecs = embed_batch(windows)
    scores = [cosine_similarity(claim_vec, v) for v in vecs]
    best = scores.index(max(scores))
    lo, hi = best, best + _WINDOW_SENTS
    # Grow symmetrically around the best window until the budget is spent.
    while lo > 0 or hi < len(sents):
        size = sum(len(x) + 1 for x in sents[lo:hi])
        if size >= limit:
            break
        if lo > 0:
            lo -= 1
        if hi < len(sents):
            hi += 1
    prefix = "… " if lo > 0 else ""
    suffix = " …" if hi < len(sents) else ""
    return (prefix + " ".join(sents[lo:hi]))[:limit] + suffix


def _source_excerpts(
    claim: str, tokens: list[str], hop_map, neuron_map: dict, regs_by_id: dict,
) -> list[tuple[str, str]]:
    """(label, claim-centered excerpt) pairs for the judge, from ctx sources."""
    limit = settings.entailment_source_chars
    claim_vec = embed_batch([claim])[0]
    sources: list[tuple[str, str]] = []
    for token in tokens:
        nid = hop_map.neuron_by_token.get(token)
        if nid is not None:
            n = neuron_map.get(nid)
            if n is not None:
                body = n.content or n.summary or n.label or ""
                sources.append((n.label or "source", _centered_excerpt(claim_vec, body, limit)))
            continue
        eid = hop_map.engram_by_token.get(token)
        if eid is not None:
            reg = regs_by_id.get(eid)
            if reg is not None:
                sources.append((reg.cfr_ref, _centered_excerpt(claim_vec, reg.text or "", limit)))
    return sources


async def escalate_borderline(
    payload: dict, hop_map, neuron_map: dict, resolved_regulations: list,
) -> None:
    """Judge the 'verify'-band claims for actual support — ONE batched call.

    Mutates the payload in place: escalated claims gain supported/reason;
    unsupported ones move to the 'unsupported' band (advisory — prose is
    never touched). Any failure degrades to escalation_status; this layer
    never raises into the answer path.
    """
    verify = [c for c in payload.get("claims", []) if c.get("band") == "verify"]
    verify = verify[: settings.entailment_max_claims]
    if not verify:
        return
    import asyncio
    regs_by_id = {r.engram_id: r for r in (resolved_regulations or [])}
    pairs = []
    judged = []
    for c in verify:
        sources = await asyncio.to_thread(
            _source_excerpts, c["claim"], c["tokens"], hop_map, neuron_map, regs_by_id,
        )
        if sources:
            pairs.append((c["claim"], sources))
            judged.append(c)
    if not pairs:
        return
    try:
        result = await llm_chat(
            system_prompt=_JUDGE_SYSTEM_PROMPT,
            user_message=_build_judge_message(pairs),
            max_tokens=1024,
            model=settings.entailment_check_model,
        )
    except (AssertionError, ValueError, OSError) as exc:
        payload["escalation_status"] = f"llm_error: {str(exc)[:120]}"
        return
    verdicts = _parse_verdicts(result.get("text", ""), len(pairs))
    if verdicts is None:
        payload["escalation_status"] = "parse_error"
        return
    by_pair = {v["pair"]: v for v in verdicts}
    unsupported = 0
    for i, claim in enumerate(judged):
        v = by_pair.get(i)
        if v is None:
            continue
        claim["supported"] = bool(v.get("supported"))
        claim["reason"] = str(v.get("reason", ""))[:200]
        if not claim["supported"]:
            claim["band"] = "unsupported"
            unsupported += 1
    payload["escalated"] = len(judged)
    payload["unsupported"] = unsupported
    payload["escalation_status"] = "ok"
