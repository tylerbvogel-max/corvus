"""Unit tests for Pattern #3 — EvalRun immutable artifacts.

These exercise the pure-Python pieces of the runner (suite loading, hash
stability, summary aggregation, model-version snapshot shape). Full
pipeline + DB coverage lives in the verification-time live smoke, which
requires a running Postgres and the real LLM provider and is therefore
out-of-scope for pytest.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("TENANT_ID", "corvus-mind")

import pytest

from app.services import eval_runs


# ── Suite loading / hashing ─────────────────────────────────────────────


def _write_suite(tmp_path: Path, name: str, body: str) -> Path:
    suite_dir = tmp_path / "eval_suites"
    suite_dir.mkdir(parents=True, exist_ok=True)
    path = suite_dir / f"{name}.yaml"
    path.write_text(textwrap.dedent(body))
    return path


def test_load_suite_parses_cases(monkeypatch, tmp_path):
    _write_suite(tmp_path, "smoke", """
        cases:
          - label: one
            text: "first query"
          - label: two
            text: "second query"
    """)
    fake_tenant = SimpleNamespace(tenant_dir=tmp_path, tenant_id="test")
    monkeypatch.setattr(eval_runs, "tenant", fake_tenant)

    suite = eval_runs.load_suite("smoke")
    assert suite.name == "smoke"
    assert [c.label for c in suite.cases] == ["one", "two"]
    assert [c.text for c in suite.cases] == ["first query", "second query"]
    assert len(suite.suite_hash) == 64  # sha256 hex


def test_load_suite_hash_is_stable(monkeypatch, tmp_path):
    _write_suite(tmp_path, "smoke", """
        cases:
          - label: a
            text: "alpha"
    """)
    fake_tenant = SimpleNamespace(tenant_dir=tmp_path, tenant_id="test")
    monkeypatch.setattr(eval_runs, "tenant", fake_tenant)

    h1 = eval_runs.load_suite("smoke").suite_hash
    h2 = eval_runs.load_suite("smoke").suite_hash
    assert h1 == h2


def test_load_suite_hash_diverges_on_content_change(monkeypatch, tmp_path):
    fake_tenant = SimpleNamespace(tenant_dir=tmp_path, tenant_id="test")
    monkeypatch.setattr(eval_runs, "tenant", fake_tenant)

    _write_suite(tmp_path, "smoke", """
        cases:
          - label: a
            text: "alpha"
    """)
    h1 = eval_runs.load_suite("smoke").suite_hash

    _write_suite(tmp_path, "smoke", """
        cases:
          - label: a
            text: "beta"
    """)
    h2 = eval_runs.load_suite("smoke").suite_hash
    assert h1 != h2


def test_load_suite_missing_raises(monkeypatch, tmp_path):
    fake_tenant = SimpleNamespace(tenant_dir=tmp_path, tenant_id="test")
    monkeypatch.setattr(eval_runs, "tenant", fake_tenant)
    with pytest.raises(FileNotFoundError):
        eval_runs.load_suite("nonexistent")


# ── Summary aggregation ─────────────────────────────────────────────────


class _StubCase:
    def __init__(self, *, blocked=False, error=None, violations=None):
        self.blocked = blocked
        self.error_message = error
        self.violations_json = {"violations": violations or []}


def test_summarize_all_pass():
    cases = [_StubCase(), _StubCase(), _StubCase()]
    s = eval_runs._summarize(cases)
    assert s == {
        "total": 3,
        "blocked": 0,
        "errors": 0,
        "violation_count": 0,
        "severity_counts": {},
        "pass_rate": 1.0,
    }


def test_summarize_counts_blocked_and_violations():
    cases = [
        _StubCase(blocked=True, violations=[{"severity": "critical"}, {"severity": "error"}]),
        _StubCase(violations=[{"severity": "warn"}]),
        _StubCase(),
    ]
    s = eval_runs._summarize(cases)
    assert s["total"] == 3
    assert s["blocked"] == 1
    assert s["violation_count"] == 3
    assert s["severity_counts"] == {"critical": 1, "error": 1, "warn": 1}
    assert s["pass_rate"] == pytest.approx(2 / 3)


def test_summarize_errors_fail_the_case():
    cases = [_StubCase(error="boom"), _StubCase()]
    s = eval_runs._summarize(cases)
    assert s["errors"] == 1
    assert s["pass_rate"] == 0.5


def test_summarize_empty_pass_rate_is_zero():
    assert eval_runs._summarize([])["pass_rate"] == 0.0


# ── Model-version snapshot ──────────────────────────────────────────────


def test_model_versions_snapshot_shape():
    snap = eval_runs._model_versions()
    assert snap, "MODEL_REGISTRY should be non-empty"
    for key, entry in snap.items():
        assert entry["display_name"]
        assert entry["provider"]
        assert "api_id" in entry
        assert "tier" in entry


# ── Append-only router surface ──────────────────────────────────────────


def test_eval_runs_router_has_no_mutation_routes():
    """Pattern #3: runs are append-only — no PUT/PATCH/DELETE exposed."""
    from app.routers.eval_runs import router

    for route in router.routes:
        methods = getattr(route, "methods", set()) or set()
        assert not (methods & {"PUT", "PATCH", "DELETE"}), (
            f"append-only invariant violated: {route.path} exposes {methods}"
        )


def test_scoring_engine_version_is_set():
    assert eval_runs.SCORING_ENGINE_VERSION
    assert isinstance(eval_runs.SCORING_ENGINE_VERSION, str)
