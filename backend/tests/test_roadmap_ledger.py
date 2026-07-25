import pytest
from datetime import datetime, timezone

from app.services.roadmap_ledger import (
    advance_state, compile_review_node, compile_work_order_node, empty_state,
    next_review_at, node_is_ready, review_status, slugify, state_summary,
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


def test_ready_record_compiles_to_revision_pinned_agency_contract_node():
    state = _state()
    state["nodes"][1]["verification"] = ["test passes", "live round trip observed"]
    node = state["nodes"][1]
    assert node_is_ready(state, node) is True
    compiled = compile_work_order_node(
        ledger_slug="corvus-long-horizon", ledger_revision=4, source_version=57,
        node=node, task_class="coding", risk_tier=2,
    )
    assert compiled["acceptance"] == ["test passes", "live round trip observed"]
    assert compiled["roadmap_ledger_revision"] == 4
    assert compiled["kickoff_prompt"] is None


def test_blocked_or_unverified_record_cannot_be_commissioned():
    state = _state()
    state["nodes"][0]["status"] = "active"
    assert node_is_ready(state, state["nodes"][1]) is False
    with pytest.raises(ValueError, match="verification checklist"):
        compile_work_order_node(
            ledger_slug="test", ledger_revision=1, source_version=1,
            node=state["nodes"][1], task_class="coding", risk_tier=2,
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


def test_review_compiles_assumptions_into_revision_pinned_agency_contract():
    state = _state()
    node = state["nodes"][1]
    node.update({
        "horizon": "thesis",
        "reviewCadence": "annual",
        "nextReviewAt": "2026-07-01",
        "assumptions": [{
            "id": "market",
            "statement": "The target problem remains expensive.",
            "status": "standing",
            "confidence": 70,
            "invalidationTrigger": "The workflow becomes fully commoditized",
        }],
    })
    compiled = compile_review_node(
        ledger_slug="long-horizon",
        ledger_revision=8,
        source_version=21,
        node=node,
        risk_tier=1,
    )
    assert compiled["task_class"] == "strategic-review"
    assert compiled["roadmap_work_kind"] == "strategic-review"
    assert compiled["roadmap_ledger_revision"] == 8
    assert "fully commoditized" in compiled["kickoff_prompt"]
    assert len(compiled["acceptance"]) == 4
