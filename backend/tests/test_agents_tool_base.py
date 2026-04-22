"""Unit tests for app.agents.tool_base — registry + decorator mechanics.

Pure-Python surface: no DB, no LLM. We construct a local ToolRegistry and
register async stubs against it to verify fail-closed lookup, duplicate
registration rejection, and snapshot immutability.
"""
from __future__ import annotations

import os

os.environ.setdefault("TENANT_ID", "corvus-aero")

import pytest

from app.agents.tool_base import Tool, ToolRegistry


async def _noop_func(session, inp):  # pragma: no cover — never actually called
    return {"ok": True}


def _make_tool(name: str, is_mutating: bool = False) -> Tool:
    return Tool(
        name=name,
        description=f"stub {name}",
        input_schema={"type": "object"},
        func=_noop_func,
        is_mutating=is_mutating,
    )


def test_register_then_get():
    reg = ToolRegistry()
    t = _make_tool("alpha")
    reg.register(t)
    assert reg.has("alpha")
    assert reg.get("alpha") is t


def test_get_unknown_raises_keyerror():
    reg = ToolRegistry()
    with pytest.raises(KeyError, match="Unknown tool"):
        reg.get("does_not_exist")


def test_duplicate_registration_rejected():
    reg = ToolRegistry()
    reg.register(_make_tool("alpha"))
    with pytest.raises(AssertionError, match="already registered"):
        reg.register(_make_tool("alpha"))


def test_snapshot_is_read_only():
    reg = ToolRegistry()
    reg.register(_make_tool("alpha"))
    snap = reg.snapshot()
    assert "alpha" in snap
    with pytest.raises(TypeError):
        snap["beta"] = _make_tool("beta")  # type: ignore[index]


def test_has_returns_bool():
    reg = ToolRegistry()
    assert reg.has("nope") is False
    reg.register(_make_tool("yes"))
    assert reg.has("yes") is True


def test_tool_is_frozen_dataclass():
    t = _make_tool("alpha")
    with pytest.raises(Exception):  # FrozenInstanceError inherits from AttributeError
        t.name = "beta"  # type: ignore[misc]


def test_is_mutating_default_false():
    t = _make_tool("alpha")
    assert t.is_mutating is False


def test_is_mutating_true_when_set():
    t = _make_tool("alpha", is_mutating=True)
    assert t.is_mutating is True
