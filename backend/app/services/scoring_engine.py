"""6-signal biomimetic scoring engine for neuron activation."""

import math
from dataclasses import dataclass
from types import MappingProxyType

import numpy as np

from app.config import settings


@dataclass
class NeuronScoreBreakdown:
    neuron_id: int
    burst: float
    impact: float
    precision: float
    novelty: float
    recency: float
    relevance: float
    combined: float
    spread_boost: float = 0.0
    entity_type: str = "neuron"  # "neuron" or "engram"


@dataclass
class ColdstartInputs:
    """Inputs for the cold-start prior (authority + freshness + centrality).

    The prior stands in for usage signals while a neuron has little firing
    history, then hands off to the usage posterior as invocations accrue
    (Bayesian shrinkage). Callers that cannot supply these fields pass None
    and the prior term is skipped entirely.
    """
    authority_level: str | None
    freshness_days: float | None
    centrality: float
    invocations: int


# Authority level -> prior evidence strength. Unknown/missing = mild default.
AUTHORITY_PRIOR_MAP = MappingProxyType({
    "binding_standard": 1.0,
    "regulatory": 0.9,
    "industry_practice": 0.7,
    "organizational": 0.5,
    "guidance": 0.5,
    "informational": 0.3,
})
_AUTHORITY_PRIOR_DEFAULT = 0.4


def coldstart_shrinkage(invocations: int | np.ndarray):
    """Weight of the prior vs the usage posterior: strength / (strength + n)."""
    strength = settings.coldstart_prior_strength
    return strength / (strength + np.maximum(invocations, 0))


def calc_coldstart_prior(
    authority_level: str | None,
    freshness_days: float | None,
    centrality: float,
) -> float:
    """Cold-start prior in [0, 1] from authority + source freshness + centrality.

    Story: an org declares what is authoritative (authority_level), documents
    edited recently outrank documents from 2019 (freshness), and structurally
    central nodes outrank periphery (degree centrality) — all before any
    firing history exists.
    """
    authority = AUTHORITY_PRIOR_MAP.get(authority_level or "", _AUTHORITY_PRIOR_DEFAULT)
    if freshness_days is None:
        freshness = 0.5  # unknown provenance date: neutral
    else:
        freshness = math.exp(-max(freshness_days, 0.0) / settings.coldstart_freshness_halflife_days)
    central = max(0.0, min(1.0, centrality))

    prior = (
        settings.coldstart_authority_weight * authority
        + settings.coldstart_freshness_weight * freshness
        + settings.coldstart_centrality_weight * central
    )
    assert 0.0 <= prior <= 1.0, f"coldstart prior out of range: {prior}"
    return prior


def calc_coldstart_term(coldstart: "ColdstartInputs | None") -> float:
    """Signed modulatory contribution of the cold-start prior.

    Centered at 0.5 so high-authority fresh central knowledge gets a boost
    and stale unanchored knowledge a mild penalty; scaled by shrinkage so
    the term decays out as real firings accrue.
    """
    if coldstart is None or settings.weight_coldstart_prior <= 0:
        return 0.0
    prior = calc_coldstart_prior(
        coldstart.authority_level, coldstart.freshness_days, coldstart.centrality,
    )
    shrink = float(coldstart_shrinkage(coldstart.invocations))
    return settings.weight_coldstart_prior * (prior - 0.5) * shrink


def calc_burst(fires_in_window: int) -> float:
    """Burst: min(1, fires_in_window / 15)"""
    assert fires_in_window >= 0, f"fires_in_window must be non-negative, got {fires_in_window}"
    result = min(1.0, fires_in_window / settings.burst_threshold)
    assert 0.0 <= result <= 1.0, f"calc_burst result out of range: {result}"
    return result


def calc_impact(avg_utility: float) -> float:
    """Impact: avg_utility (EMA, alpha=0.3)"""
    result = max(0.0, min(1.0, avg_utility))
    assert 0.0 <= result <= 1.0, f"calc_impact result out of range: {result}"
    return result


def calc_precision(dept_fires: int, dept_total_queries: int) -> float:
    """Precision: dept_fires / dept_total_queries. Floor 0.3 if dept_total_queries < 5."""
    assert dept_fires >= 0, f"dept_fires must be non-negative, got {dept_fires}"
    if dept_total_queries < 5:
        return 0.3
    result = min(1.0, dept_fires / max(1, dept_total_queries))
    assert 0.0 <= result <= 1.0, f"calc_precision result out of range: {result}"
    return result


def calc_novelty(age_queries: int) -> float:
    """Novelty: max(0, 1 - age_queries / 200). Newer neurons score higher."""
    result = max(0.0, 1.0 - age_queries / settings.novelty_halflife_queries)
    assert 0.0 <= result <= 1.0, f"calc_novelty result out of range: {result}"
    return result


def calc_recency(queries_since_last: int) -> float:
    """Recency: e^(-queries_since / 500). Recently-fired neurons score higher."""
    if queries_since_last < 0:
        return 1.0
    result = math.exp(-queries_since_last / settings.recency_decay_queries)
    assert 0.0 <= result <= 1.0, f"calc_recency result out of range: {result}"
    return result


# --- Vectorized batch signal functions (numpy) ---
# These mirror the scalar functions above but operate on entire arrays at once.
# Used by _score_candidates_vectorized() in neuron_service.py for 10-50x speedup.


def calc_burst_batch(fires_array: np.ndarray) -> np.ndarray:
    """Vectorized burst: min(1, fires / threshold) for all candidates."""
    return np.minimum(1.0, fires_array / settings.burst_threshold)


def calc_impact_batch(utilities: np.ndarray) -> np.ndarray:
    """Vectorized impact: clip avg_utility to [0, 1]."""
    return np.clip(utilities, 0.0, 1.0)


def calc_precision_batch(
    dept_fires: np.ndarray, dept_totals: np.ndarray,
) -> np.ndarray:
    """Vectorized precision: dept_fires / dept_total, floor 0.3 if < 5 queries."""
    result = np.where(
        dept_totals >= 5,
        np.minimum(1.0, dept_fires / np.maximum(dept_totals, 1)),
        0.3,
    )
    return result


def calc_novelty_batch(age_queries: np.ndarray) -> np.ndarray:
    """Vectorized novelty: max(0, 1 - age / halflife)."""
    return np.maximum(0.0, 1.0 - age_queries / settings.novelty_halflife_queries)


def calc_recency_batch(queries_since: np.ndarray) -> np.ndarray:
    """Vectorized recency: e^(-queries_since / decay)."""
    safe_qs = np.maximum(queries_since, 0.0)
    return np.exp(-safe_qs / settings.recency_decay_queries)


def calc_coldstart_term_batch(
    authority_levels: list,
    freshness_days: np.ndarray,
    centrality: np.ndarray,
    invocations: np.ndarray,
) -> np.ndarray:
    """Vectorized cold-start prior term (see calc_coldstart_term).

    freshness_days uses NaN for unknown provenance dates (neutral 0.5).
    """
    if settings.weight_coldstart_prior <= 0:
        return np.zeros(len(authority_levels), dtype=np.float64)
    authority = np.array(
        [AUTHORITY_PRIOR_MAP.get(a or "", _AUTHORITY_PRIOR_DEFAULT) for a in authority_levels],
        dtype=np.float64,
    )
    freshness = np.where(
        np.isnan(freshness_days),
        0.5,
        np.exp(-np.maximum(freshness_days, 0.0) / settings.coldstart_freshness_halflife_days),
    )
    central = np.clip(centrality, 0.0, 1.0)
    prior = (
        settings.coldstart_authority_weight * authority
        + settings.coldstart_freshness_weight * freshness
        + settings.coldstart_centrality_weight * central
    )
    shrink = coldstart_shrinkage(invocations)
    return settings.weight_coldstart_prior * (prior - 0.5) * shrink


_STOP_WORDS = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "into", "also",
    "are", "was", "were", "been", "has", "have", "had", "not", "but",
    "all", "can", "will", "may", "use", "per", "via", "its", "our",
    "any", "each", "more", "most", "such", "than", "when", "how",
    "new", "based", "using", "used", "general", "process", "system",
    "management", "plan", "data", "review", "standard", "control",
    "report", "analysis", "list", "level", "type", "set", "model",
    "support", "service", "design", "test", "requirements",
    "documentation", "procedure", "configuration",
})


def calc_relevance(keywords: list[str], neuron_text: str) -> float:
    """Relevance: two-tier keyword matching with stop-word filtering.

    Tier 1 — exact phrase match (full keyword string found in neuron text).
             Strong stimulus-response: the neuron directly encodes this concept.
    Tier 2 — token-level match, excluding domain stop words.
             Partial stimulus: individual distinctive terms overlap.

    Domain stop words (management, data, process, etc.) are filtered from
    token matching because they appear in nearly every neuron and provide
    no discriminative signal — analogous to tonic background firing that
    carries no information about the stimulus.
    """
    if not keywords:
        return 0.0
    text_lower = neuron_text.lower()

    # Tier 1: exact phrase matches (strong stimulus response)
    phrase_hits = sum(1 for kw in keywords if kw.lower() in text_lower)
    phrase_score = phrase_hits / len(keywords)

    # Tier 2: token-level matches, excluding stop words
    tokens = set()
    for kw in keywords:
        for token in kw.lower().split():
            if len(token) >= 3 and token not in _STOP_WORDS:
                tokens.add(token)
    if tokens:
        token_hits = sum(1 for t in tokens if t in text_lower)
        token_score = token_hits / len(tokens)
    else:
        token_score = 0.0

    result = min(1.0, max(phrase_score, token_score))
    assert 0.0 <= result <= 1.0, f"calc_relevance result out of range: {result}"
    return result


def calc_hybrid_relevance(
    keyword_scores: dict[int, float],
    semantic_scores: dict[int, float],
    k: int = 60,
) -> dict[int, float]:
    """Fuse keyword and semantic relevance via Reciprocal Rank Fusion (RRF).

    RRF score = 1/(k + keyword_rank) + 1/(k + semantic_rank), normalized to [0, 1].
    Neurons appearing in only one list receive half-weight from that list alone.
    """
    assert k > 0, f"RRF k must be positive, got {k}"
    all_ids = set(keyword_scores) | set(semantic_scores)
    if not all_ids:
        return {}

    # Rank each list (1-based, sorted descending by score)
    kw_ranked = {nid: rank for rank, (nid, _) in enumerate(
        sorted(keyword_scores.items(), key=lambda x: x[1], reverse=True), start=1
    )}
    sem_ranked = {nid: rank for rank, (nid, _) in enumerate(
        sorted(semantic_scores.items(), key=lambda x: x[1], reverse=True), start=1
    )}

    # Default rank for missing entries: len + 1 (worst rank)
    default_kw_rank = len(kw_ranked) + 1
    default_sem_rank = len(sem_ranked) + 1

    raw: dict[int, float] = {}
    for nid in all_ids:
        kw_rank = kw_ranked.get(nid, default_kw_rank)
        sem_rank = sem_ranked.get(nid, default_sem_rank)
        raw[nid] = 1.0 / (k + kw_rank) + 1.0 / (k + sem_rank)

    # Normalize to [0, 1]
    max_score = max(raw.values()) if raw else 1.0
    assert max_score > 0, "max RRF score must be positive"
    return {nid: score / max_score for nid, score in raw.items()}


def _resolve_relevance(
    semantic_similarity: float | None,
    keywords: list[str],
    neuron_text: str,
    hybrid_score: float | None = None,
) -> float:
    """Resolve relevance from hybrid RRF score, semantic similarity, or keyword match."""
    if hybrid_score is not None:
        return max(0.0, min(1.0, hybrid_score))
    if semantic_similarity is not None:
        return max(0.0, min(1.0, semantic_similarity))
    return calc_relevance(keywords, neuron_text)


def _compute_gated_combined(
    burst: float,
    impact: float,
    precision: float,
    novelty: float,
    recency: float,
    relevance: float,
    coldstart_term: float = 0.0,
) -> float:
    stimulus = settings.weight_relevance * relevance

    modulatory = (
        settings.weight_burst * burst
        + settings.weight_impact * impact
        + settings.weight_precision * precision
        + settings.weight_novelty * novelty
        + settings.weight_recency * recency
        + coldstart_term
    )

    threshold = settings.relevance_gate_threshold
    floor = settings.relevance_gate_floor
    if relevance >= threshold:
        gate = 1.0
    elif relevance > 0:
        gate = floor + (1.0 - floor) * (relevance / threshold)
    else:
        gate = floor

    # Clamp at zero: the coldstart term is signed and may push an otherwise
    # inactive neuron slightly negative, which just means "no activation".
    combined = max(0.0, stimulus + modulatory * gate)
    assert combined >= 0, f"combined score must be non-negative, got {combined}"
    assert isinstance(combined, float), f"combined must be float, got {type(combined)}"
    return combined


def _apply_classification_boost(
    combined: float, region_match: bool, role_match: bool
) -> float:
    """Boost for classifier-predicted region/role membership (role wins)."""
    if role_match:
        combined *= 1.5
    elif region_match:
        combined *= 1.25
    assert combined >= 0, f"boosted score must be non-negative, got {combined}"
    return combined


def compute_score(
    fires_in_window: int,
    avg_utility: float,
    dept_fires: int,
    dept_total_queries: int,
    age_queries: int,
    queries_since_last: int,
    keywords: list[str],
    neuron_text: str,
    neuron_id: int = 0,
    dept_match: bool = False,
    role_match: bool = False,
    semantic_similarity: float | None = None,
    hybrid_score: float | None = None,
    coldstart: ColdstartInputs | None = None,
) -> NeuronScoreBreakdown:
    """Compute combined activation score using gated modulatory scoring.

    Biological analogue:
    - Relevance = stimulus (glutamate depolarization). Without stimulus,
      the neuron cannot reach activation threshold.
    - Burst/Impact/Precision/Novelty/Recency = neuromodulatory signals
      (dopamine, norepinephrine, serotonin). They adjust sensitivity and
      gain but cannot cause firing on their own.
    - Cold-start prior (optional) = developmental wiring bias: authority +
      freshness + centrality stand in for usage until firings accrue.

    The modulatory component is gated by relevance: full modulation at
    relevance >= threshold (default 0.2), with a small floor for
    spontaneous background activity.
    """
    burst = calc_burst(fires_in_window)
    impact = calc_impact(avg_utility)
    precision = calc_precision(dept_fires, dept_total_queries)
    novelty = calc_novelty(age_queries)
    recency = calc_recency(queries_since_last)

    assert 0.0 <= burst <= 1.0, f"burst out of range: {burst}"
    assert 0.0 <= impact <= 1.0, f"impact out of range: {impact}"
    assert 0.0 <= precision <= 1.0, f"precision out of range: {precision}"
    assert 0.0 <= novelty <= 1.0, f"novelty out of range: {novelty}"
    assert 0.0 <= recency <= 1.0, f"recency out of range: {recency}"

    relevance = _resolve_relevance(semantic_similarity, keywords, neuron_text, hybrid_score)
    combined = _compute_gated_combined(
        burst, impact, precision, novelty, recency, relevance,
        coldstart_term=calc_coldstart_term(coldstart),
    )
    combined = _apply_classification_boost(combined, dept_match, role_match)

    return NeuronScoreBreakdown(
        neuron_id=neuron_id,
        burst=round(burst, 4),
        impact=round(impact, 4),
        precision=round(precision, 4),
        novelty=round(novelty, 4),
        recency=round(recency, 4),
        relevance=round(relevance, 4),
        combined=round(combined, 4),
    )


def _apply_single_override(
    value: float, override: dict,
) -> float:
    """Apply multiplier, floor, ceiling to a signal value."""
    if override.get("multiplier") is not None:
        value *= override["multiplier"]
    if override.get("floor") is not None:
        value = max(value, override["floor"])
    if override.get("ceiling") is not None:
        value = min(value, override["ceiling"])
    return value


def apply_score_overrides(
    scores: list[NeuronScoreBreakdown],
    overrides_by_neuron: dict[int, list[dict]],
) -> list[NeuronScoreBreakdown]:
    """Apply per-neuron signal overrides to scored candidates.

    overrides_by_neuron: {neuron_id: [{signal, floor, ceiling, multiplier}, ...]}
    Mutates scores in place and recalculates combined when individual signals change.
    """
    if not overrides_by_neuron:
        return scores
    signal_fields = ("burst", "impact", "precision", "novelty", "recency", "relevance")
    for score in scores:
        neuron_overrides = overrides_by_neuron.get(score.neuron_id)
        if not neuron_overrides:
            continue
        changed = False
        for ov in neuron_overrides:
            sig = ov["signal"]
            if sig in signal_fields:
                old = getattr(score, sig)
                new = _apply_single_override(old, ov)
                if new != old:
                    setattr(score, sig, round(new, 4))
                    changed = True
            elif sig == "combined":
                score.combined = round(
                    _apply_single_override(score.combined, ov), 4,
                )
        if changed:
            score.combined = round(
                _compute_gated_combined(
                    score.burst, score.impact, score.precision,
                    score.novelty, score.recency, score.relevance,
                ) + score.spread_boost,
                4,
            )
    return scores


def update_impact_ema(current_avg: float, new_utility: float) -> float:
    """Update Impact signal using EMA: new_avg = alpha * new + (1-alpha) * old"""
    alpha = settings.impact_ema_alpha
    result = alpha * new_utility + (1 - alpha) * current_avg
    assert 0.0 <= result <= 1.0, f"update_impact_ema result out of range: {result}"
    return result
