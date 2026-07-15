"""Agency Lab incentive arithmetic and immutable-policy invariants."""

import pytest

from app.services.agency_lab import (DEFAULT_POLICY, agency_wager, brier_return,
                                     conservative_success_probability, score_event,
                                     settlement_adjustment, validate_policy)
from app.services.agency_work_orders import (canonical_digest, choose_audit,
    compile_contract, lock_completion, validate_plan_graph)
from types import SimpleNamespace


def test_default_policy_is_valid_and_all_sections_toggleable():
    cfg = validate_policy({})
    assert cfg == DEFAULT_POLICY
    assert all("enabled" in section for section in cfg.values())


def test_unknown_policy_section_is_rejected():
    with pytest.raises(ValueError, match="unknown policy section"):
        validate_policy({"secret_backdoor": {"enabled": True}})


def test_probabilities_are_bounded():
    with pytest.raises(ValueError, match="between 0 and 1"):
        validate_policy({"audit": {"base_probability": 1.1}})


def test_clawback_cannot_be_weaker_than_false_completion_gain():
    with pytest.raises(ValueError, match="clawback"):
        validate_policy({"concealment": {"base_clawback": 5}})


def test_self_disclosure_rewards_early_reproducible_report():
    scored = score_event("self_disclosure", DEFAULT_POLICY,
                         pre_submission=True, reproducible=True)
    assert scored["dimensions"]["integrity"] == 11
    assert scored["settled"] == 6.6
    assert scored["escrow"] == 4.4


def test_concealment_is_immediate_negative_not_escrowed():
    scored = score_event("concealment", DEFAULT_POLICY, known_failure=True)
    assert scored["total"] == -60
    assert scored["settled"] == -60
    assert scored["escrow"] == 0


def test_fabricated_evidence_is_maximum_clawback():
    scored = score_event("fabricated_evidence", DEFAULT_POLICY)
    assert scored["dimensions"]["integrity"] == -150


def test_honest_failure_is_positive():
    scored = score_event("honest_failure", DEFAULT_POLICY)
    assert scored["total"] == 2


def test_brier_return_rewards_calibrated_success():
    assert brier_return(0.9, True, 4) > brier_return(0.6, True, 4)
    assert brier_return(0.9, False, 4) < 0


def test_verified_completion_combines_delivery_and_calibration():
    scored = score_event("verified_completion", DEFAULT_POLICY,
                         forecast_probability=0.9, resolved_outcome=True)
    assert scored["dimensions"]["delivery"] == 10
    assert scored["dimensions"]["calibration"] == pytest.approx(3.92)
    assert scored["total"] == pytest.approx(13.92)


def test_conservative_p_increases_with_verified_successes():
    assert conservative_success_probability(20, 4) > conservative_success_probability(2, 2)


def test_fractional_kelly_is_capped():
    cfg = validate_policy({"capital": {"kelly_fraction": 1, "max_wager_fraction": 0.1}})
    wager = agency_wager(100, 2, 500, cfg)
    assert wager["allocated_fraction"] == 0.1
    assert wager["capital_at_risk"] == 50


def test_uncertain_new_worker_gets_no_positive_wager():
    wager = agency_wager(2, 2, 100, DEFAULT_POLICY)
    assert wager["allocated_fraction"] == 0
    assert wager["capital_at_risk"] == 0


def test_disabled_reward_rejects_scoring_event():
    cfg = validate_policy({"disclosure": {"enabled": False}})
    with pytest.raises(ValueError, match="unsupported or disabled"):
        score_event("self_disclosure", cfg)


def test_delayed_success_releases_only_escrow():
    assert settlement_adjustment(10, 4, True) == {
        "event_type": "escrow_release", "capital_amount": 4, "escrow_offset": -4}


def test_delayed_failure_claws_back_full_provisional_win_and_cancels_escrow():
    assert settlement_adjustment(10, 4, False) == {
        "event_type": "delayed_clawback", "capital_amount": -10, "escrow_offset": -4}


def test_north_star_requires_executable_acceptance_contract():
    with pytest.raises(ValueError, match="missing"):
        validate_plan_graph({"nodes": [{"id": "x", "title": "X"}]})


def test_north_star_rejects_dangling_dependencies():
    graph = {"nodes": [{"id": "x", "title": "X", "outcome": "done",
        "acceptance": ["proof"], "risk_tier": 1, "task_class": "intake"}],
        "edges": [{"from": "x", "to": "missing"}]}
    with pytest.raises(ValueError, match="known nodes"):
        validate_plan_graph(graph)


def test_completion_claims_lock_with_stable_digest():
    payload = {"disposition": "complete", "claims": [{"claim": "read file"}],
        "confidence": .7, "limitations": [], "disclosures": [],
        "evidence": [{"kind": "command"}], "next_action": "audit"}
    locked = lock_completion(payload)
    assert locked["digest"] == canonical_digest(payload)
    assert "locked_at" in locked


def test_completion_refuses_missing_disclosure_channel():
    with pytest.raises(ValueError, match="disclosures"):
        lock_completion({"disposition": "complete", "claims": [], "confidence": .5,
            "limitations": [], "evidence": [], "next_action": "audit"})


def test_new_worker_is_always_critic_audited_even_at_low_risk():
    worker = SimpleNamespace(beta_alpha=2, beta_beta=2)
    result = choose_audit(work_order_id="wo-fixed", risk_tier=1,
                          config=DEFAULT_POLICY, worker=worker)
    assert result["deterministic_inspection"] is True
    assert result["critic_selected"] is True
    assert result["critic_probability"] == 1


def test_compiled_contract_carries_machine_readable_exit_schema():
    policy = SimpleNamespace(id=1, version=1, config=DEFAULT_POLICY)
    worker = SimpleNamespace(beta_alpha=2, beta_beta=2, agency_capital=100)
    contract = compile_contract(work_order_id="wo-test", node={"acceptance": ["proof"]},
        policy=policy, worker=worker, plan_digest="abc", lease_scope={"filesystem": "none"})
    schema = contract["completion_schema"]
    assert schema["properties"]["claims"]["type"] == "array"
    assert schema["properties"]["disposition"]["enum"] == ["complete", "partial", "failed", "blocked"]
