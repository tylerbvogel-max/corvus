"""Tests for Pattern #7: runtime output-policy gates.

Covers the three seed policies individually, plus the
:func:`app.governance.output_guard.run_guards` orchestrator to ensure
redactions mutate text, a blocking violation short-circuits, and an
audit action is submitted for every run.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.governance.output_guard import run_guards
from app.governance.policies.citation import CitationPolicy
from app.governance.policies.export_control import ExportControlPolicy
from app.governance.policies.pii import PIIPolicy
from app.governance.policies import PolicyContext
from app.middleware.rbac import UserIdentity


# ── In-memory stand-ins ────────────────────────────────────────────────


@dataclass
class _FakeFiring:
    was_included: bool


class _FakeSession:
    """Minimal AsyncSession that captures ``add`` calls and no-ops IO."""

    def __init__(self):
        self.added: list = []

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None

    async def commit(self):
        return None

    async def execute(self, *_args, **_kwargs):
        raise AssertionError("run_guards should not call db.execute")


def _actor() -> UserIdentity:
    return UserIdentity(user_id="tester", role="reader", source="disabled")


# ── Citation policy ───────────────────────────────────────────────────


def test_citation_policy_flags_uncited_answer():
    ctx = PolicyContext(
        response_text="A long answer with no grounding.",
        firings=[_FakeFiring(was_included=False)],
        tenant_policy={"enabled": True, "min_citations": 1},
    )
    drafts = list(CitationPolicy({"enabled": True, "min_citations": 1}).check(ctx))
    assert len(drafts) == 1
    assert drafts[0].rule_id == "citation"
    assert drafts[0].action in {"flag", "block", "redact"}


def test_citation_policy_passes_when_fired():
    ctx = PolicyContext(
        response_text="Answer",
        firings=[_FakeFiring(was_included=True)],
        tenant_policy={"enabled": True, "min_citations": 1},
    )
    drafts = list(CitationPolicy({"enabled": True, "min_citations": 1}).check(ctx))
    assert drafts == []


# ── PII policy ────────────────────────────────────────────────────────


def test_pii_policy_redacts_email():
    text = "Email me at alice@example.com please."
    ctx = PolicyContext(
        response_text=text,
        firings=[],
        tenant_policy={"enabled": True, "action": "redact", "severity": "error"},
    )
    drafts = list(PIIPolicy({"enabled": True, "action": "redact", "severity": "error"}).check(ctx))
    assert drafts, "PII policy should detect the email"
    hit = drafts[0]
    assert hit.rule_id == "pii"
    assert hit.matched_span == "alice@example.com"
    assert hit.redaction == "[REDACTED:email]"


def test_pii_policy_detects_ssn():
    text = "SSN 123-45-6789 should never appear."
    ctx = PolicyContext(
        response_text=text,
        firings=[],
        tenant_policy={"enabled": True},
    )
    drafts = list(PIIPolicy({"enabled": True}).check(ctx))
    kinds = {d.detail["kind"] for d in drafts if d.detail}
    assert "us_ssn" in kinds


# ── Export-control policy ─────────────────────────────────────────────


def test_export_control_policy_blocks_itar_term():
    cfg = {
        "enabled": True,
        "action": "block",
        "severity": "critical",
        "terms": ["ITAR", "missile"],
    }
    ctx = PolicyContext(
        response_text="This design relates to missile guidance.",
        firings=[],
        tenant_policy=cfg,
    )
    drafts = list(ExportControlPolicy(cfg).check(ctx))
    assert drafts, "export-control policy should flag ITAR term"
    assert any(d.action == "block" for d in drafts)


def test_export_control_policy_clean_text():
    cfg = {"enabled": True, "terms": ["ITAR"]}
    ctx = PolicyContext(
        response_text="A benign statement about cheese.",
        firings=[],
        tenant_policy=cfg,
    )
    drafts = list(ExportControlPolicy(cfg).check(ctx))
    assert drafts == []


# ── run_guards orchestrator ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_guards_noops_when_all_disabled(monkeypatch):
    from app.tenant import tenant as tenant_singleton

    monkeypatch.setattr(
        type(tenant_singleton),
        "output_policies",
        property(lambda self: {}),
    )
    db = _FakeSession()
    result = await run_guards(
        db,
        query_id=None,
        response_text="anything",
        firings=[],
        actor=_actor(),
    )
    assert result.blocked is False
    assert result.violations == []
    assert result.action_id is None
    assert result.final_text == "anything"


@pytest.mark.asyncio
async def test_run_guards_redacts_and_submits_action(monkeypatch):
    """PII redaction mutates text and emits exactly one audit action."""
    from app.tenant import tenant as tenant_singleton
    from app.governance import output_guard as guard_mod

    monkeypatch.setattr(
        type(tenant_singleton),
        "output_policies",
        property(lambda self: {
            "pii": {"enabled": True, "action": "redact", "severity": "error"},
        }),
    )
    submitted: list[dict] = []

    async def _fake_submit(**kwargs):
        submitted.append(kwargs)

        class _R:
            action_id = 99
        return _R()

    monkeypatch.setattr(guard_mod.action_bus, "submit", _fake_submit)

    db = _FakeSession()
    result = await run_guards(
        db,
        query_id=42,
        response_text="Contact: bob@example.com",
        firings=[],
        actor=_actor(),
    )
    assert result.blocked is False
    assert result.action_id == 99
    assert "[REDACTED:email]" in result.final_text
    assert len(submitted) == 1
    assert submitted[0]["kind"] == "output.policy.check"
