"""Runbooks must exist, cover the declared failure modes, and stay honest.

A runbook that drifts is worse than none: it is read under pressure and
believed. These are cheap structural checks, not prose review — they catch the
failure mode that actually happens, which is a runbook quietly ceasing to
describe the system.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.observability.jobs import JOB_INVENTORY


RUNBOOKS = Path(__file__).resolve().parents[2] / "docs" / "runbooks"

# The scenarios record durability-operational-envelope names explicitly.
REQUIRED = {
    "database-unavailable",
    "schema-mismatch",
    "provider-failure",
    "backup-restore",
    "distill-backlog",
    "janitor-failure",
    "skill-compile-failure",
    "release-rollback",
}

# The four the criterion required to be executed under controlled conditions.
MUST_BE_DRILLED = {
    "database-unavailable", "schema-mismatch", "provider-failure", "backup-restore",
    "release-rollback",
}


def _runbook(name: str) -> str:
    return (RUNBOOKS / f"{name}.md").read_text(encoding="utf-8")


def test_every_required_scenario_has_a_runbook():
    present = {p.stem for p in RUNBOOKS.glob("*.md")} - {"README"}
    missing = REQUIRED - present
    assert not missing, f"no runbook for: {sorted(missing)}"


@pytest.mark.parametrize("name", sorted(REQUIRED))
def test_each_runbook_says_how_to_detect_and_what_to_do(name):
    body = _runbook(name).lower()
    assert "## detection" in body or "## symptom" in body, f"{name}: no detection guidance"
    assert "first response" in body or "## procedure" in body, f"{name}: no first response"


@pytest.mark.parametrize("name", sorted(MUST_BE_DRILLED))
def test_scenarios_the_record_required_were_actually_drilled(name):
    """'Runbooks are EXECUTED in disposable or controlled conditions.'"""
    body = _runbook(name)
    assert "EXECUTED" in body, (
        f"{name} was never drilled — an unrun runbook is a hypothesis about "
        "how the system fails"
    )
    assert "Observed" in body or "observed" in body, (
        f"{name} claims a drill but records no observation from it"
    )


def test_undrilled_runbooks_say_so_rather_than_implying_otherwise():
    body = _runbook("distill-backlog")
    assert "NOT DRILLED" in body
    assert "hypothesis" in body.lower()


def test_partially_drilled_runbook_names_what_is_still_missing():
    body = _runbook("janitor-failure")
    assert "PARTIALLY EXECUTED" in body
    assert "NOT drilled" in body or "not drilled" in body


def test_index_lists_every_runbook():
    index = _runbook("README")
    for name in REQUIRED:
        assert f"{name}.md" in index, f"{name} is missing from the runbook index"


def test_every_inventoried_job_is_reachable_from_a_runbook():
    """A scheduled job with no runbook has no first response when it breaks."""
    corpus = " ".join(_runbook(p.stem) for p in RUNBOOKS.glob("*.md"))
    unreferenced = [j.name for j in JOB_INVENTORY if j.name not in corpus]
    assert not unreferenced, (
        f"jobs with no runbook coverage: {unreferenced}"
    )
