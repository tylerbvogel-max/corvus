import pytest
from datetime import datetime, timezone

from app.services.roadmap_ledger import (
    advance_state, empty_state, extract_commit_candidates, next_review_at,
    node_is_ready, reconcile_node, review_status, slugify, state_summary,
    validate_state,
)


def _state():
    return {
        "version": 7,
        "updatedAt": "2026-07-25T00:00:00+00:00",
        "sections": [{"id": "engine", "label": "Engine", "color": "#3987e5"}],
        "nodes": [
            {"id": "base", "section": "engine", "label": "Base", "status": "done"},
            {"id": "next", "section": "engine", "label": "Next", "status": "active",
             "prereqs": ["base"], "implementationContext": {"path": "app/main.py"}},
            {"id": "drop", "section": "engine", "label": "Dropped", "status": "cancelled"},
        ],
        "edges": [{"from": "base", "to": "next", "type": "enables"}],
        "milestones": [{"id": "m1", "label": "First gate", "prereqs": ["next"]}],
    }


def test_validate_preserves_extension_fields_without_aliasing_input():
    raw = _state()
    validated = validate_state(raw)
    assert validated["nodes"][1]["implementationContext"]["path"] == "app/main.py"
    validated["nodes"][1]["label"] = "mutated"
    assert raw["nodes"][1]["label"] == "Next"


@pytest.mark.parametrize("mutation, message", [
    (lambda s: s["nodes"].append(dict(s["nodes"][0])), "duplicate node id"),
    (lambda s: s["nodes"][1].update(section="missing"), "unknown section"),
    (lambda s: s["nodes"][1].update(prereqs=["missing"]), "unknown prerequisite"),
    (lambda s: s["edges"].append({"from": "base", "to": "missing"}), "known records"),
    (lambda s: s["nodes"][1].update(status="mystery"), "unsupported status"),
])
def test_validate_rejects_structural_corruption(mutation, message):
    state = _state()
    mutation(state)
    with pytest.raises(ValueError, match=message):
        validate_state(state)


def test_summary_excludes_cancelled_from_delivery_denominator():
    assert state_summary(_state()) == {
        "records": 3,
        "in_scope": 2,
        "out_of_scope": 1,
        "done": 1,
        "moving": 1,
        "completion": 50,
        "assumptions": 0,
        "challenged_assumptions": 0,
        "reviews_due": 0,
        "reviews_upcoming": 0,
        "horizons": {
            "active": 0,
            "horizon-1": 0,
            "horizon-2": 0,
            "horizon-3": 0,
            "thesis": 0,
        },
        "unclassified_horizon": 2,
    }


def test_advance_state_stamps_time_and_monotonically_advances_source_version():
    advanced = advance_state(_state(), previous_version=10)
    assert advanced["version"] == 11
    assert advanced["updatedAt"] != "2026-07-25T00:00:00+00:00"


def test_empty_state_is_immediately_valid_and_slugify_is_stable():
    assert validate_state(empty_state())["sections"][0]["id"] == "roadmap"
    assert slugify("Bounty Hunter / Derivatives") == "bounty-hunter-derivatives"
    with pytest.raises(ValueError, match="letters or numbers"):
        slugify("!!!")


def test_ready_record_accepts_independently_verified_reconciliation():
    state = _state()
    state["nodes"][1]["verification"] = ["test passes", "live round trip observed"]
    node = state["nodes"][1]
    assert node_is_ready(state, node) is True
    reconciled = reconcile_node(
        node,
        ledger_revision=4,
        disposition="complete",
        result_recap="Delivered and observed against the live API.",
        verification_passed=True,
        confidence=.93,
        claims=["The implementation is live"],
        limitations=["One browser verified"],
        disclosures=[],
        evidence=["pytest: 12 passed", "live API: HTTP 200", "commit 4f2a91c"],
        verifier="qa-agent",
        accepted_by="tyler",
        accepted_at=datetime(2026, 7, 26, tzinfo=timezone.utc),
    )
    assert reconciled["status"] == "done"
    assert reconciled["verificationResults"]["ledgerRevision"] == 4
    assert reconciled["verificationResults"]["verifier"] == "qa-agent"
    assert reconciled["reconciliationHistory"][-1]["evidence"][0] == "pytest: 12 passed"
    assert reconciled["verificationResults"]["evidenceCommits"] == ["4f2a91c"]


def test_verified_completion_requires_checklist_evidence_and_independent_acceptance():
    state = _state()
    common = dict(
        ledger_revision=1,
        disposition="complete",
        result_recap="Done.",
        verification_passed=True,
        confidence=.8,
        claims=["done"],
        limitations=[],
        disclosures=[],
        evidence=["receipt"],
        verifier="qa-agent",
        accepted_by="tyler",
    )
    with pytest.raises(ValueError, match="verification checklist"):
        reconcile_node(state["nodes"][1], **common)
    state["nodes"][1]["verification"] = ["live behavior observed"]
    with pytest.raises(ValueError, match="independent"):
        reconcile_node(
            state["nodes"][1],
            **{**common, "accepted_by": "qa-agent"},
        )


def test_strategic_fields_validate_and_summarize_due_reviews():
    state = _state()
    state["nodes"][1].update({
        "horizon": "horizon-2",
        "reviewCadence": "quarterly",
        "nextReviewAt": "2026-01-01T00:00:00+00:00",
        "assumptions": [{
            "id": "adoption",
            "statement": "Operators will use the workflow weekly.",
            "status": "challenged",
            "confidence": 45,
            "evidenceFor": ["Three pilot interviews"],
            "evidenceAgainst": ["No retained cohort yet"],
            "invalidationTrigger": "Four weeks below 20% weekly use",
            "consequence": "Retire the workflow",
        }],
    })
    validated = validate_state(state)
    assert validated["nodes"][1]["assumptions"][0]["confidence"] == 45
    summary = state_summary(validated)
    assert summary["assumptions"] == 1
    assert summary["challenged_assumptions"] == 1
    assert summary["reviews_due"] == 1
    assert summary["horizons"]["horizon-2"] == 1


@pytest.mark.parametrize("field, value, message", [
    ("horizon", "someday", "unsupported horizon"),
    ("reviewCadence", "whenever", "unsupported review cadence"),
    ("nextReviewAt", "not-a-date", "ISO date"),
])
def test_strategic_fields_reject_invalid_values(field, value, message):
    state = _state()
    state["nodes"][1][field] = value
    with pytest.raises(ValueError, match=message):
        validate_state(state)


def test_assumption_contract_requires_bounded_confidence_and_unique_ids():
    state = _state()
    state["nodes"][1]["assumptions"] = [
        {"id": "market", "statement": "Demand persists.", "confidence": 101},
    ]
    with pytest.raises(ValueError, match="integer from 0 to 100"):
        validate_state(state)
    state["nodes"][1]["assumptions"] = [
        {"id": "market", "statement": "Demand persists.", "confidence": 60},
        {"id": "market", "statement": "Budget persists.", "confidence": 40},
    ]
    with pytest.raises(ValueError, match="duplicate assumption id"):
        validate_state(state)


def test_review_schedule_uses_calendar_months_and_status_windows():
    leap_day = datetime(2024, 2, 29, 15, tzinfo=timezone.utc)
    assert next_review_at(
        {"reviewCadence": "annual"}, from_time=leap_day,
    ) == "2025-02-28T15:00:00+00:00"
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)
    assert review_status(
        {"status": "planned", "nextReviewAt": "2026-07-24T00:00:00+00:00"},
        now=now,
    ) == "due"
    assert review_status(
        {"status": "planned", "nextReviewAt": "2026-08-10T00:00:00+00:00"},
        now=now,
    ) == "upcoming"


def _completion_kwargs(**overrides):
    base = dict(
        ledger_revision=1,
        disposition="complete",
        result_recap="Shipped.",
        verification_passed=True,
        confidence=.9,
        claims=["landed"],
        limitations=[],
        disclosures=[],
        evidence=["commit 308d053", "1,175 tests pass"],
        verifier="qa-agent",
        accepted_by="tyler",
    )
    base.update(overrides)
    return base


def _completable_node():
    node = _state()["nodes"][1]
    node["verification"] = ["live behavior observed"]
    return node


def test_commit_candidates_ignore_short_hex_and_dedupe_case_insensitively():
    found = extract_commit_candidates([
        "commit 8CC5DB3 on wt/pretooluse-reach",
        "commit 8cc5db3 again, plus sha256 09481a623e8e",
        "1,175 passed in 33.62s over 469 units",   # no 7+ hex run
    ])
    # The sha256 digest is nominated too — over-nomination is safe because the
    # repository decides, and a receipt only needs one claim to resolve.
    assert found == ["8cc5db3", "09481a623e8e"]


def test_commit_candidates_are_capped_so_prose_cannot_fan_out_git_calls():
    noisy = [" ".join(f"{n:07x}" for n in range(200))]
    assert len(extract_commit_candidates(noisy)) == 20


def test_verified_completion_refuses_evidence_that_names_no_commit():
    with pytest.raises(ValueError, match="must name a commit"):
        reconcile_node(
            _completable_node(),
            **_completion_kwargs(evidence=["pytest: 12 passed", "looks good"]),
        )


def test_verified_completion_records_commits_and_verification_state():
    reconciled = reconcile_node(
        _completable_node(),
        **_completion_kwargs(
            evidence_commits=["308d053"], commit_verification="verified"),
    )
    receipt = reconciled["verificationResults"]
    assert reconciled["status"] == "done"
    assert receipt["evidenceCommits"] == ["308d053"]
    assert receipt["commitVerification"] == "verified"
    assert receipt["schema"] == "corvus.roadmap-reconciliation/v2"


def test_receipt_never_implies_a_check_that_did_not_run():
    """A missing repository is recorded as unverified, not silently passed."""
    reconciled = reconcile_node(
        _completable_node(),
        **_completion_kwargs(commit_verification="unverifiable-no-repo"),
    )
    assert reconciled["status"] == "done"
    assert reconciled["verificationResults"]["commitVerification"] == "unverifiable-no-repo"
    assert reconciled["verificationResults"]["evidenceCommits"] == ["308d053"]


def test_unsupported_commit_verification_state_is_rejected():
    with pytest.raises(ValueError, match="unsupported commit verification"):
        reconcile_node(
            _completable_node(),
            **_completion_kwargs(commit_verification="probably-fine"),
        )


@pytest.mark.parametrize("disposition, passed", [
    ("partial", True), ("failed", False), ("blocked", False),
])
def test_honest_failure_reports_need_no_commit(disposition, passed):
    """Filing an unlanded outcome must stay cheap, or the ledger learns to lie."""
    reconciled = reconcile_node(
        _completable_node(),
        **_completion_kwargs(
            disposition=disposition, verification_passed=passed,
            evidence=["OOM killed the fixture backend; nothing shipped"],
            next_action="Retry on a quiet machine.",
        ),
    )
    assert reconciled["status"] != "done"
    assert reconciled["verificationResults"]["evidenceCommits"] == []


def test_partial_reconciliation_records_evidence_without_closing_record():
    node = _state()["nodes"][1]
    reconciled = reconcile_node(
        node,
        ledger_revision=8,
        disposition="partial",
        result_recap="Behavior works, but outside-user evidence remains open.",
        verification_passed=True,
        confidence=.65,
        claims=["Local behavior verified"],
        limitations=["No outside-user cohort"],
        disclosures=["Retention remains unknown"],
        evidence=["consumer eval run 42"],
        verifier="consumer-eval",
        accepted_by="tyler",
        next_action="Run the design-partner cohort.",
    )
    assert reconciled["status"] == "active"
    assert reconciled["verificationResults"]["disposition"] == "partial"
    assert reconciled["verificationResults"]["nextAction"] == "Run the design-partner cohort."
