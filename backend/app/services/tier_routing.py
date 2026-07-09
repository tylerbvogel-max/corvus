"""Tier-elastic escalation routing (roadmap node arch-tier-routing).

Route each query to the cheapest model tier that meets its stakes: the
primary slot defaults to haiku, and escalates to a stronger tier BEFORE
execution when per-query uncertainty signals fire. Eval evidence
(2026-07-08, 3-mode + follow-up runs): haiku+neurons 4.4 overall at
$0.019/answer, sonnet+neurons 4.7 at $0.052 — so escalating only the
uncertain minority collapses expected cost while worst-case quality holds.

All signals are available at prep time (no extra LLM calls, no latency),
calibrated 2026-07-09 over 63 live corvus-aero queries + the smoke suite:

1. Coverage — mean stimulus relevance of the packed slice's top 5. The
   per-neuron max saturates at 1.0 (RRF rank normalization), but the
   top-5 mean separates cleanly: direct hits sit 0.95+, cross-domain /
   off-graph questions 0.76-0.83, garbage 0.23. Weak anchors are where
   cheap models hallucinate; floor 0.82 ≈ p25 of live traffic.
2. Spread density — the fraction of the packed slice that arrived via
   spreading activation rather than direct semantic hits (live p90 = 0.30).
   A hop-heavy pack means the answer must be synthesized across
   associations, which is where the stronger tier's judgment earns its price.
3. Regulatory stakes — an EXPLICIT regulatory citation in the query text
   (tenant reference patterns: FAR/MIL-STD/AS9100/CMMC/...). "Any resolved
   regulation in context" was measured useless here (62/63 live queries
   resolve regs in this tenant); a user citing a specific authority is the
   sharp compliance-stakes signal (6/63 live).

Union of the three ≈ 29% of live traffic → expected cost ~$0.029/answer
vs $0.052 all-sonnet, with the risky tail still answered by sonnet.

opus@low is deliberately NOT a routing tier: measured at medium effort it
is just a more expensive sonnet (19/20 judgments tied, $0.080 vs $0.052),
and its distinctive profile (accuracy 4.7 / faithfulness 5.0 / terse) only
appears at low effort. It is exposed as an explicit audit-grade ACTION
(QuerySlotRequest.audit / QueryRequest.audit_grade), never chosen silently.

Failure behavior: any missing signal input (no context, empty pack) reads
as ZERO coverage and therefore escalates — uncertainty about uncertainty
routes up, never down, so the failure mode is cost, not quality.
"""

from dataclasses import dataclass
from types import MappingProxyType

from app.config import settings

# Escalation only ever moves UP this ladder — a slot that explicitly asked
# for sonnet or opus is never touched, and non-Claude models (groq, gemini)
# are outside the measured evidence, so they never route.
TIER_RANK = MappingProxyType({"haiku": 0, "sonnet": 1, "opus": 2})

_COVERAGE_TOP_N = 5  # top-N relevance mean; N=5 is what was calibrated


@dataclass(frozen=True)
class TierDecision:
    """Pre-execution routing decision for the primary slot, plus the measured
    signal values (recorded on every query — including non-escalations — so
    thresholds stay recalibratable from production data)."""
    escalate: bool
    model: str                # tier to escalate to (meaningful when escalate)
    reasons: tuple[str, ...]  # which signals fired, human-readable keys
    signals: dict             # measured values behind the decision

    def to_payload(self) -> dict:
        """JSON-safe dict persisted into the slot result (queries.results_json)."""
        return {
            "escalated": self.escalate,
            "model": self.model,
            "reasons": list(self.reasons),
            "signals": self.signals,
        }


def _coverage_signals(ctx) -> tuple[float, float]:
    """(coverage, spread_share) of the packed slice.

    Coverage is the mean stimulus relevance (hybrid semantic+keyword, 0..1)
    of the top-5 scored neurons — the per-neuron max is rank-normalized to
    1.0 and carries no information. spread_share counts neurons that entered
    via spreading activation (spread_boost > 0) rather than direct stimulus.
    """
    packed = getattr(ctx, "all_scored", None) or []
    assert isinstance(packed, list), "ctx.all_scored must be a list"
    if not packed:
        return 0.0, 0.0
    top_rels = sorted((s.relevance for s in packed), reverse=True)[:_COVERAGE_TOP_N]
    coverage = sum(top_rels) / len(top_rels)
    spread_share = sum(1 for s in packed if s.spread_boost > 0) / len(packed)
    assert 0.0 <= spread_share <= 1.0, "spread_share must be a fraction"
    return coverage, spread_share


def _cites_regulation(user_message: str) -> bool:
    """True when the query text explicitly cites a regulatory authority
    (tenant patterns — FAR clauses, MIL-STDs, AS9100, CMMC, ...). For hero
    follow-ups the packed conversation history is scanned too, which is
    intended: a session anchored on a cited standard keeps its stakes."""
    from app.services.reference_detector import detect_references
    return any(d.domain == "regulatory" for d in detect_references(user_message))


def decide_tier_escalation(
    ctx, user_message: str, session_overlap: float | None = None,
) -> TierDecision:
    """Decide, pre-execution, whether the primary slot escalates one tier.

    Signals fire independently; ANY firing escalates (uncertainty routes up).
    session_overlap (drift-gate packed-set overlap, None outside persisted
    sessions) is recorded for calibration but is not a trigger: a low overlap
    just means the topic moved and a fresh pack was made — the fresh pack's
    own coverage/spread signals already grade it.
    """
    assert ctx is not None, "decide_tier_escalation requires a prepared context"
    assert isinstance(user_message, str), "user_message must be a string"

    coverage, spread_share = _coverage_signals(ctx)
    regulatory = settings.tier_routing_regulatory_escalates and _cites_regulation(user_message)

    reasons: list[str] = []
    if coverage < settings.tier_routing_coverage_floor:
        reasons.append("low_coverage")
    if spread_share >= settings.tier_routing_spread_share:
        reasons.append("spread_dense")
    if regulatory:
        reasons.append("regulatory")

    return TierDecision(
        escalate=bool(reasons),
        model=settings.tier_routing_escalation_model,
        reasons=tuple(reasons),
        signals={
            "coverage": round(coverage, 4),
            "spread_share": round(spread_share, 4),
            "regulatory": regulatory,
            "session_overlap": session_overlap,
        },
    )


def escalated_model(model_name: str, decision: TierDecision | None) -> str:
    """The effective primary model after routing. Upgrades only: the decision
    applies solely when the slot's model sits BELOW the escalation tier on the
    Claude ladder — explicit sonnet/opus picks and non-Claude models pass
    through untouched."""
    assert isinstance(model_name, str) and model_name, "model_name must be non-empty"
    if decision is None or not decision.escalate:
        return model_name
    target = decision.model
    if target not in TIER_RANK or model_name not in TIER_RANK:
        return model_name
    if TIER_RANK[model_name] < TIER_RANK[target]:
        return target
    return model_name
