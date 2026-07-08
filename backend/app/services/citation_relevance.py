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

ADVISORY + LOG-ONLY by design: scores ride on the response and are
persisted for calibration; nothing is flagged or stripped until a
threshold is chosen from real distribution data (precedent: the drift-gate
and export-control thresholds both moved after measurement).
"""

from __future__ import annotations

import re

from app.services.embedding_service import embed_batch, cosine_similarity
from app.services.entailment_check import extract_cited_claims


# Two-sentence windows keep the claim-vs-source comparison length-symmetric;
# window count per source is bounded so a pathological source can't stall
# the exit layer (JPL-2).
_WINDOW_SENTS = 2
_MAX_WINDOWS_PER_SOURCE = 12
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _sentence_windows(text: str) -> list[str]:
    """Overlapping windows of _WINDOW_SENTS sentences (stride 1), bounded."""
    assert isinstance(text, str), "text must be a string"
    sents = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
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
