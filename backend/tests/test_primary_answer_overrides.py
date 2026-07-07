"""Primary-answer quality floor (grounding backlog §6.5).

_apply_primary_overrides raises the primary slot's reasoning effort to the
configured floor (never lowering an explicitly higher per-request effort) and
optionally swaps the primary slot to a stronger model. Because effort flows
through a ContextVar and each slot runs in its own asyncio task (own context
copy), the override must be visible only to the primary slot's LLM calls.
"""
import asyncio
import contextvars

from app.config import settings
from app.services.executor import _apply_primary_overrides
from app.services.llm_provider import effort_var


def _run_isolated(fn):
    """Run fn in a copied context so effort_var mutations don't leak across tests."""
    ctx = contextvars.copy_context()
    return ctx.run(fn)


def test_effort_floor_raises_low_to_configured(monkeypatch):
    monkeypatch.setattr(settings, "primary_answer_effort", "medium")
    monkeypatch.setattr(settings, "primary_answer_model", "")

    def check():
        effort_var.set("low")
        model = _apply_primary_overrides("haiku")
        assert model == "haiku", "model must be unchanged when no model override set"
        assert effort_var.get() == "medium", "effort must be raised to the floor"

    _run_isolated(check)


def test_effort_floor_never_lowers_explicit_high(monkeypatch):
    monkeypatch.setattr(settings, "primary_answer_effort", "medium")
    monkeypatch.setattr(settings, "primary_answer_model", "")

    def check():
        effort_var.set("high")
        _apply_primary_overrides("haiku")
        assert effort_var.get() == "high", "floor must never lower an explicit effort"

    _run_isolated(check)


def test_effort_floor_disabled_by_empty_setting(monkeypatch):
    monkeypatch.setattr(settings, "primary_answer_effort", "")
    monkeypatch.setattr(settings, "primary_answer_model", "")

    def check():
        effort_var.set("low")
        _apply_primary_overrides("haiku")
        assert effort_var.get() == "low", "empty setting must leave effort untouched"

    _run_isolated(check)


def test_effort_floor_applies_over_default_when_no_request_effort(monkeypatch):
    monkeypatch.setattr(settings, "primary_answer_effort", "medium")
    monkeypatch.setattr(settings, "primary_answer_model", "")
    monkeypatch.setattr(settings, "default_effort", "low")

    def check():
        effort_var.set(None)  # no per-request effort
        _apply_primary_overrides("haiku")
        assert effort_var.get() == "medium"

    _run_isolated(check)


def test_effort_floor_ignores_invalid_value(monkeypatch):
    monkeypatch.setattr(settings, "primary_answer_effort", "ultra")
    monkeypatch.setattr(settings, "primary_answer_model", "")

    def check():
        effort_var.set("low")
        _apply_primary_overrides("haiku")
        assert effort_var.get() == "low", "invalid floor value must be ignored"

    _run_isolated(check)


def test_model_override_swaps_to_registry_key(monkeypatch):
    monkeypatch.setattr(settings, "primary_answer_effort", "")
    monkeypatch.setattr(settings, "primary_answer_model", "sonnet")

    assert _run_isolated(lambda: _apply_primary_overrides("haiku")) == "sonnet"


def test_model_override_ignores_unknown_key(monkeypatch):
    monkeypatch.setattr(settings, "primary_answer_effort", "")
    monkeypatch.setattr(settings, "primary_answer_model", "not-a-model")

    assert _run_isolated(lambda: _apply_primary_overrides("haiku")) == "haiku"


def test_effort_isolated_between_slot_tasks(monkeypatch):
    """The primary slot's effort bump must not leak to sibling compare slots.

    Mirrors execute_query: coroutines wrapped by asyncio.gather each get their
    own context copy, so a set() inside the primary task stays local to it.
    """
    monkeypatch.setattr(settings, "primary_answer_effort", "high")
    monkeypatch.setattr(settings, "primary_answer_model", "")

    async def primary_slot():
        _apply_primary_overrides("haiku")
        await asyncio.sleep(0)  # yield so the compare slot interleaves
        return effort_var.get()

    async def compare_slot():
        await asyncio.sleep(0)
        return effort_var.get()

    async def main():
        effort_var.set("low")
        return await asyncio.gather(primary_slot(), compare_slot())

    primary_effort, compare_effort = _run_isolated(lambda: asyncio.run(main()))
    assert primary_effort == "high", "primary slot must see the raised effort"
    assert compare_effort == "low", "compare slot must keep the request effort"
