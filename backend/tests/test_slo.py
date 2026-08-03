"""SLOs must judge honestly, and say where their thresholds came from."""

from __future__ import annotations

import asyncio

import pytest

from app.observability.slo import (
    BREACHED, HYPOTHESIS, MEASURED, OBJECTIVES, OBJECTIVES_BY_ID, OK,
    STATED_BUDGET, UNKNOWN, collect_signals, evaluate, slo_report,
)


# ---- contract ---------------------------------------------------------------

def test_every_objective_names_an_owner_a_response_and_a_provenance():
    for o in OBJECTIVES:
        assert o.owner, f"{o.id} has no owner"
        assert o.first_response, f"{o.id} has no first response"
        assert o.threshold_basis in (STATED_BUDGET, MEASURED, HYPOTHESIS), o.id
        assert o.basis_detail, f"{o.id} does not say where its threshold came from"
        assert o.comparison in ("<=", ">="), o.id
        assert o.metric.startswith("corvus."), f"{o.id} metric is not namespaced"


def test_guessed_thresholds_are_labelled_as_guesses():
    """A constant whose origin is unrecorded gets defended as if it were evidence."""
    guessed = [o for o in OBJECTIVES if o.threshold_basis == HYPOTHESIS]
    assert guessed, "no objective is labelled a hypothesis — suspiciously confident"
    for o in guessed:
        assert "GUESSED" in o.basis_detail or "guess" in o.basis_detail.lower()


def test_the_objective_set_stays_small():
    """'Define a SMALL SLO set' — a dashboard nobody reads is not observability."""
    assert len(OBJECTIVES) <= 6


# ---- judging ----------------------------------------------------------------

def test_a_value_inside_its_bound_is_ok():
    entry = OBJECTIVES_BY_ID["recall-latency"].judge(3448.0)
    assert entry["status"] == OK


def test_a_value_outside_its_bound_is_breached():
    entry = OBJECTIVES_BY_ID["recall-latency"].judge(12_000.0)
    assert entry["status"] == BREACHED
    assert entry["first_response"]


def test_a_lower_bound_objective_breaches_downward():
    """recall-availability breaches when traffic STOPS, not when it grows."""
    assert OBJECTIVES_BY_ID["recall-availability"].judge(0)["status"] == BREACHED
    assert OBJECTIVES_BY_ID["recall-availability"].judge(2000)["status"] == OK


def test_a_missing_signal_is_unknown_not_healthy():
    """A dead collector must not be able to report perfect health."""
    entry = OBJECTIVES_BY_ID["distill-backlog"].judge(None)
    assert entry["status"] == UNKNOWN
    assert entry["value"] is None


@pytest.mark.parametrize("unhealthy, expected", [(0, OK), (1, BREACHED), (4, BREACHED)])
def test_any_unhealthy_job_breaches(unhealthy, expected):
    assert OBJECTIVES_BY_ID["scheduled-jobs"].judge(unhealthy)["status"] == expected


# ---- signal extraction ------------------------------------------------------

def test_signals_are_read_from_the_reports_corvus_already_produces():
    signals = collect_signals(
        mind_metrics={"recall": {"latency_ms": {"p95": 3448.0},
                                 "performance_window": 2000}},
        distill_status={"ready": 3},
        jobs={"counts": {"ok": 3, "late": 1, "failing": 0, "never-run": 0}},
    )
    assert signals["recall-latency"] == 3448.0
    assert signals["recall-availability"] == 2000
    assert signals["distill-backlog"] == 3
    assert signals["scheduled-jobs"] == 1


def test_never_run_jobs_do_not_count_as_unhealthy():
    """A job awaiting its first tick is waiting, not broken."""
    signals = collect_signals(None, None, {"counts": {"never-run": 4, "ok": 0}})
    assert signals["scheduled-jobs"] == 0


def test_absent_inputs_produce_none_not_zero():
    signals = collect_signals(None, None, None)
    assert all(v is None for v in signals.values()), signals


def test_malformed_inputs_do_not_masquerade_as_zero():
    signals = collect_signals({"recall": "not-a-dict"}, {"ready": "lots"}, None)
    assert signals["recall-latency"] is None
    assert signals["distill-backlog"] is None


# ---- report -----------------------------------------------------------------

def test_breaches_sort_first():
    report = evaluate({
        "recall-latency": 99_999.0, "recall-availability": 2000,
        "distill-backlog": 0, "scheduled-jobs": 0,
    })
    assert report["objectives"][0]["status"] == BREACHED
    assert report["meeting_all"] is False


def test_report_publishes_threshold_provenance():
    report = evaluate({o.id: 0 for o in OBJECTIVES})
    provenance = report["threshold_provenance"]
    assert "distill-backlog" in provenance[HYPOTHESIS]
    assert "recall-latency" in provenance[STATED_BUDGET]


def test_a_failing_signal_source_degrades_one_objective_not_the_report():
    """An SLO endpoint that 500s during an incident is worse than none."""
    def _explode():
        raise RuntimeError("metrics collector died")

    report = asyncio.run(slo_report(
        metrics_loader=_explode,
        distill_loader=lambda: {"ready": 0},
    ))
    by_id = {o["id"]: o for o in report["objectives"]}
    assert by_id["recall-latency"]["status"] == UNKNOWN
    assert by_id["distill-backlog"]["status"] == OK


def test_async_loaders_are_awaited():
    async def _metrics():
        return {"recall": {"latency_ms": {"p95": 100.0}, "performance_window": 5}}

    report = asyncio.run(slo_report(
        metrics_loader=_metrics, distill_loader=lambda: {"ready": 0},
    ))
    by_id = {o["id"]: o for o in report["objectives"]}
    assert by_id["recall-latency"]["value"] == 100.0
