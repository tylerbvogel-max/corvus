"""Agency Lab incentive economy: pure arithmetic plus audited persistence."""

from __future__ import annotations

import copy
import math
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (AgencyCapitalTransaction, AgencyExperiment, AgencyPolicy,
                        AgencyScoreEvent, AgencyWorkerProfile)

POLICY_STATUSES = ("draft", "simulated", "approved", "live", "retired")
POLICY_MODES = ("replay", "simulation", "shadow", "live")
ROLES = ("builder", "critic", "reproducer", "investigator", "recovery", "adjudicator")

DEFAULT_POLICY = {
    "audit": {"enabled": True, "base_probability": 0.20, "high_risk_multiplier": 2.0,
              "mandatory_risk_tier": 4, "hidden_test_share": 0.25},
    "disclosure": {"enabled": True, "base_reward": 6.0, "pre_submission_multiplier": 1.5,
                   "reproducible_evidence_bonus": 2.0, "repeat_issue_discount": 0.5},
    "concealment": {"enabled": True, "base_clawback": 30.0, "known_failure_multiplier": 2.0,
                    "fabricated_evidence_multiplier": 5.0, "tampering_quarantines": True},
    "delivery": {"enabled": True, "verified_reward": 10.0, "honest_failure_reward": 2.0,
                 "external_defect_cost": 3.0},
    "critic": {"enabled": True, "verified_bounty": 6.0, "false_positive_cost": 2.0},
    "calibration": {"enabled": True, "rule": "brier", "weight": 4.0},
    "settlement": {"enabled": True, "immediate_fraction": 0.60, "windows_days": [7, 30]},
    "capital": {"enabled": True, "starting": 100.0, "kelly_fraction": 0.25,
                "max_wager_fraction": 0.20, "hard_floor": 0.0},
    "exploration": {"enabled": True, "minimum_share": 0.15},
}


def validate_policy(config: dict) -> dict:
    cfg = copy.deepcopy(DEFAULT_POLICY)
    for section, values in config.items():
        if section not in cfg or not isinstance(values, dict):
            raise ValueError(f"unknown policy section: {section}")
        cfg[section].update(values)
    probability_fields = (("audit", "base_probability"), ("audit", "hidden_test_share"),
                          ("settlement", "immediate_fraction"), ("capital", "kelly_fraction"),
                          ("capital", "max_wager_fraction"), ("exploration", "minimum_share"))
    for section, field in probability_fields:
        value = float(cfg[section][field])
        if not 0 <= value <= 1:
            raise ValueError(f"{section}.{field} must be between 0 and 1")
    if cfg[conceal := "concealment"]["base_clawback"] < cfg["delivery"]["verified_reward"]:
        raise ValueError("concealment clawback cannot be lower than verified-delivery reward")
    if cfg["capital"]["hard_floor"] < 0:
        raise ValueError("capital hard floor cannot be negative")
    return cfg


def brier_return(probability: float, outcome: bool, weight: float) -> float:
    if not 0 <= probability <= 1:
        raise ValueError("forecast probability must be between 0 and 1")
    loss = (probability - float(outcome)) ** 2
    return weight * (1.0 - 2.0 * loss)  # bounded [-weight, +weight]


def conservative_success_probability(alpha: float, beta: float) -> float:
    """Normal lower-bound approximation to Beta posterior's 10th percentile."""
    total = alpha + beta
    mean = alpha / total
    variance = alpha * beta / (total * total * (total + 1.0))
    return max(0.01, min(0.99, mean - 1.28155 * math.sqrt(variance)))


def agency_wager(alpha: float, beta: float, capital: float, config: dict,
                 net_odds: float = 1.0) -> dict:
    p = conservative_success_probability(alpha, beta)
    q = 1.0 - p
    full_kelly = max(0.0, (net_odds * p - q) / net_odds)
    fraction = min(full_kelly * config["capital"]["kelly_fraction"],
                   config["capital"]["max_wager_fraction"])
    return {"conservative_p": round(p, 4), "full_kelly": round(full_kelly, 4),
            "allocated_fraction": round(fraction, 4), "capital_at_risk": round(capital * fraction, 2)}


def score_event(event_type: str, config: dict, *, forecast_probability: float | None = None,
                resolved_outcome: bool | None = None, severity: float = 1.0,
                pre_submission: bool = False, reproducible: bool = False,
                known_failure: bool = False) -> dict:
    dims = {"delivery": 0.0, "calibration": 0.0, "integrity": 0.0, "stewardship": 0.0}
    if event_type == "verified_completion" and config["delivery"]["enabled"]:
        dims["delivery"] = config["delivery"]["verified_reward"] * severity
    elif event_type == "honest_failure" and config["delivery"]["enabled"]:
        dims["integrity"] = config["delivery"]["honest_failure_reward"]
    elif event_type == "self_disclosure" and config["disclosure"]["enabled"]:
        value = config["disclosure"]["base_reward"]
        if pre_submission:
            value *= config["disclosure"]["pre_submission_multiplier"]
        if reproducible:
            value += config["disclosure"]["reproducible_evidence_bonus"]
        dims["integrity"] = value * severity
    elif event_type == "verified_critic_finding" and config["critic"]["enabled"]:
        dims["stewardship"] = config["critic"]["verified_bounty"] * severity
    elif event_type == "critic_false_positive" and config["critic"]["enabled"]:
        dims["stewardship"] = -config["critic"]["false_positive_cost"]
    elif event_type == "external_defect" and config["delivery"]["enabled"]:
        dims["delivery"] = -config["delivery"]["external_defect_cost"] * severity
    elif event_type in ("concealment", "fabricated_evidence", "evaluator_tampering") and config["concealment"]["enabled"]:
        value = config["concealment"]["base_clawback"]
        if known_failure:
            value *= config["concealment"]["known_failure_multiplier"]
        if event_type == "fabricated_evidence":
            value *= config["concealment"]["fabricated_evidence_multiplier"]
        dims["integrity"] = -value * severity
    else:
        raise ValueError(f"unsupported or disabled event type: {event_type}")
    if forecast_probability is not None and resolved_outcome is not None and config["calibration"]["enabled"]:
        dims["calibration"] = brier_return(forecast_probability, resolved_outcome,
                                            config["calibration"]["weight"])
    total = round(sum(dims.values()), 4)
    immediate = config["settlement"]["immediate_fraction"] if config["settlement"]["enabled"] and total > 0 else 1.0
    return {"dimensions": {k: round(v, 4) for k, v in dims.items()}, "total": total,
            "settled": round(total * immediate, 4), "escrow": round(total * (1.0 - immediate), 4)}


async def ensure_default_policy(db: AsyncSession) -> AgencyPolicy:
    row = (await db.execute(select(AgencyPolicy).order_by(AgencyPolicy.id).limit(1))).scalar_one_or_none()
    if row:
        return row
    row = AgencyPolicy(name="Just Culture Control", version=1, status="approved", mode="shadow",
                       config=DEFAULT_POLICY, description="Conservative starting economy; shadow-only.",
                       created_by="agency_lab_bootstrap")
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def record_score(db: AsyncSession, worker: AgencyWorkerProfile, policy: AgencyPolicy,
                       event: dict) -> AgencyScoreEvent:
    scored = score_event(event["event_type"], policy.config,
                         forecast_probability=event.get("forecast_probability"),
                         resolved_outcome=event.get("resolved_outcome"), severity=event.get("severity", 1.0),
                         pre_submission=event.get("pre_submission", False),
                         reproducible=event.get("reproducible", False),
                         known_failure=event.get("known_failure", False))
    row = AgencyScoreEvent(worker_profile_id=worker.id, policy_id=policy.id,
        experiment_id=event.get("experiment_id"), work_order_id=event.get("work_order_id"),
        event_type=event["event_type"], task_class=event.get("task_class", "general"),
        risk_tier=event.get("risk_tier", 1), dimensions=scored["dimensions"],
        points_total=scored["total"], settled_points=scored["settled"],
        escrow_points=scored["escrow"], forecast_probability=event.get("forecast_probability"),
        resolved_outcome=event.get("resolved_outcome"), evidence=event.get("evidence", {}))
    db.add(row)
    worker.agency_capital = max(policy.config["capital"]["hard_floor"], worker.agency_capital + scored["settled"])
    worker.peak_capital = max(worker.peak_capital, worker.agency_capital)
    totals = dict(worker.score_totals or {})
    for key, value in scored["dimensions"].items():
        totals[key] = round(float(totals.get(key, 0)) + value, 4)
    worker.score_totals = totals
    if event.get("resolved_outcome") is True:
        worker.beta_alpha += 1
    elif event.get("resolved_outcome") is False:
        worker.beta_beta += 1
    if event["event_type"] == "evaluator_tampering" and policy.config["concealment"]["tampering_quarantines"]:
        worker.status = "quarantined"
    await db.flush()
    db.add(AgencyCapitalTransaction(worker_profile_id=worker.id, score_event_id=row.id,
        kind="score_settlement", amount=scored["settled"], balance_after=worker.agency_capital,
        reason=f"{event['event_type']} under policy {policy.id}"))
    await db.commit()
    await db.refresh(row)
    return row


def settlement_adjustment(original_total: float, original_escrow: float,
                          passed: bool) -> dict:
    """Release escrow on durable success; claw back the provisional win on failure."""
    if original_escrow < 0:
        raise ValueError("original escrow cannot be negative")
    capital_amount = original_escrow if passed else -max(0.0, original_total)
    return {"event_type": "escrow_release" if passed else "delayed_clawback",
            "capital_amount": round(capital_amount, 4),
            "escrow_offset": round(-original_escrow, 4)}


async def settle_event(db: AsyncSession, original: AgencyScoreEvent, *, passed: bool,
                       evidence: dict) -> AgencyScoreEvent:
    existing = await db.scalar(select(AgencyScoreEvent.id).where(
        AgencyScoreEvent.adjustment_of_id == original.id))
    if existing:
        raise ValueError("score event already settled")
    worker = await db.get(AgencyWorkerProfile, original.worker_profile_id)
    policy = await db.get(AgencyPolicy, original.policy_id)
    assert worker is not None and policy is not None
    adjustment = settlement_adjustment(original.points_total, original.escrow_points, passed)
    amount = adjustment["capital_amount"]
    dims = {"delivery": amount, "calibration": 0.0, "integrity": 0.0, "stewardship": 0.0}
    row = AgencyScoreEvent(worker_profile_id=worker.id, policy_id=policy.id,
        experiment_id=original.experiment_id, work_order_id=original.work_order_id,
        event_type=adjustment["event_type"], task_class=original.task_class,
        risk_tier=original.risk_tier, dimensions=dims, points_total=amount,
        settled_points=amount, escrow_points=adjustment["escrow_offset"],
        resolved_outcome=passed, evidence=evidence, adjustment_of_id=original.id)
    db.add(row)
    worker.agency_capital = max(policy.config["capital"]["hard_floor"], worker.agency_capital + amount)
    worker.peak_capital = max(worker.peak_capital, worker.agency_capital)
    if passed:
        worker.beta_alpha += 1
    else:
        worker.beta_beta += 1
    await db.flush()
    db.add(AgencyCapitalTransaction(worker_profile_id=worker.id, score_event_id=row.id,
        kind=adjustment["event_type"], amount=amount, balance_after=worker.agency_capital,
        reason=f"delayed settlement of score event {original.id}"))
    await db.commit()
    await db.refresh(row)
    return row


def diagnostics(events: list[AgencyScoreEvent], workers: list[AgencyWorkerProfile]) -> list[dict]:
    counts: dict[str, int] = {}
    for event in events:
        counts[event.event_type] = counts.get(event.event_type, 0) + 1
    findings: list[dict] = []
    disclosures = counts.get("self_disclosure", 0)
    external = counts.get("external_defect", 0)
    false_pos = counts.get("critic_false_positive", 0)
    critic_ok = counts.get("verified_critic_finding", 0)
    if disclosures >= 3 and external == 0:
        findings.append({"severity": "good", "code": "early_reporting", "message": "Self-disclosures are rising without escaped defects."})
    if false_pos > critic_ok and false_pos >= 2:
        findings.append({"severity": "warning", "code": "critic_spam", "message": "Critic false positives exceed reproduced findings."})
    if counts.get("concealment", 0) or counts.get("fabricated_evidence", 0):
        findings.append({"severity": "serious", "code": "integrity_breach", "message": "Concealment or fabricated evidence was detected."})
    total_capital = sum(w.agency_capital for w in workers)
    if total_capital and workers and max(w.agency_capital for w in workers) / total_capital > 0.7:
        findings.append({"severity": "warning", "code": "capital_concentration", "message": "More than 70% of agency capital is concentrated in one profile."})
    return findings


async def dashboard(db: AsyncSession) -> dict:
    policy = await ensure_default_policy(db)
    workers = list((await db.execute(select(AgencyWorkerProfile).order_by(AgencyWorkerProfile.agency_capital.desc()))).scalars())
    events = list((await db.execute(select(AgencyScoreEvent).order_by(AgencyScoreEvent.id.desc()).limit(500))).scalars())
    experiments = list((await db.execute(select(AgencyExperiment).order_by(AgencyExperiment.id.desc()))).scalars())
    event_counts: dict[str, int] = {}
    for e in events:
        event_counts[e.event_type] = event_counts.get(e.event_type, 0) + 1
    settled = sum(e.settled_points for e in events)
    escrow = sum(e.escrow_points for e in events)
    return {"active_policy_id": policy.id, "policy": policy.config,
            "summary": {"workers": len(workers), "events": len(events), "experiments": len(experiments),
                        "settled_points": round(settled, 2), "escrow_points": round(escrow, 2),
                        "self_disclosures": event_counts.get("self_disclosure", 0),
                        "integrity_breaches": event_counts.get("concealment", 0) + event_counts.get("fabricated_evidence", 0)},
            "workers": [{"id": w.id, "key": w.key, "display_name": w.display_name, "role": w.role,
                         "model": w.model, "harness": w.harness, "status": w.status,
                         "agency_capital": round(w.agency_capital, 2), "peak_capital": round(w.peak_capital, 2),
                         "drawdown": round(w.peak_capital - w.agency_capital, 2), "scores": w.score_totals,
                         "wager": agency_wager(w.beta_alpha, w.beta_beta, w.agency_capital, policy.config)} for w in workers],
            "event_counts": event_counts, "diagnostics": diagnostics(events, workers),
            "experiments": [{"id": x.id, "name": x.name, "status": x.status, "mode": x.mode,
                             "primary_metric": x.primary_metric, "sample_target": x.sample_target,
                             "dimensions": x.dimensions} for x in experiments]}
