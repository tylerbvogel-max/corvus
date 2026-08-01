"""Unit tests for app.agents.registry — YAML loader + AgentRegistry.

We write synthetic YAML into a tmp dir, point a fresh AgentRegistry at it,
register a couple of stub tools in a private ToolRegistry, and monkeypatch
``app.agents.registry.get_tool_registry`` so validation uses our stub. This
keeps the tests hermetic — no dependency on whichever real tools happen to
be registered at import time.
"""
from __future__ import annotations

import os
import textwrap
from pathlib import Path

os.environ.setdefault("TENANT_ID", "corvus-mind")

import pytest

from app.agents import registry as registry_mod
from app.agents.registry import AgentRegistry
from app.agents.tool_base import Tool, ToolRegistry


async def _noop(session, inp):  # pragma: no cover
    return {}


def _stub_tool_registry(*names: str) -> ToolRegistry:
    reg = ToolRegistry()
    for n in names:
        reg.register(Tool(
            name=n, description=f"stub {n}",
            input_schema={"type": "object"}, func=_noop, is_mutating=False,
        ))
    return reg


def _write_yaml(dirpath: Path, filename: str, body: str) -> Path:
    dirpath.mkdir(parents=True, exist_ok=True)
    path = dirpath / filename
    path.write_text(textwrap.dedent(body).lstrip("\n"))
    return path


VALID_YAML = """
name: testagent
role: tester
description: An agent for tests.
model: haiku
max_tokens: 512
max_turns: 4
tool_allow_list:
  - alpha
  - beta
trigger:
  manual: true
  schedule:
    enabled: false
system_prompt: |
  You are a test agent. Say hello.
"""


def test_load_valid_yaml(tmp_path, monkeypatch):
    stub = _stub_tool_registry("alpha", "beta")
    monkeypatch.setattr(registry_mod, "get_tool_registry", lambda: stub)
    _write_yaml(tmp_path, "testagent.yaml", VALID_YAML)

    reg = AgentRegistry(definitions_dir=tmp_path)
    reg.load()

    assert reg.has("testagent")
    agent = reg.get("testagent")
    assert agent.name == "testagent"
    assert agent.role == "tester"
    assert agent.model == "haiku"
    assert agent.max_tokens == 512
    assert agent.max_turns == 4
    assert agent.tool_allow_list == ("alpha", "beta")
    assert agent.trigger.manual is True
    assert agent.trigger.schedule_enabled is False


def test_load_rejects_unknown_tool(tmp_path, monkeypatch):
    stub = _stub_tool_registry("alpha")  # beta intentionally missing
    monkeypatch.setattr(registry_mod, "get_tool_registry", lambda: stub)
    _write_yaml(tmp_path, "bad.yaml", VALID_YAML)

    reg = AgentRegistry(definitions_dir=tmp_path)
    with pytest.raises(AssertionError, match="unknown tools in allow-list"):
        reg.load()


def test_load_rejects_unknown_top_level_key(tmp_path, monkeypatch):
    stub = _stub_tool_registry("alpha")
    monkeypatch.setattr(registry_mod, "get_tool_registry", lambda: stub)
    _write_yaml(tmp_path, "bad.yaml", """
        name: testagent
        role: tester
        model: haiku
        mystery_key: oops
        tool_allow_list: [alpha]
        system_prompt: hi
    """)
    reg = AgentRegistry(definitions_dir=tmp_path)
    with pytest.raises(AssertionError, match="unknown keys"):
        reg.load()


def test_load_rejects_missing_required_key(tmp_path, monkeypatch):
    stub = _stub_tool_registry("alpha")
    monkeypatch.setattr(registry_mod, "get_tool_registry", lambda: stub)
    _write_yaml(tmp_path, "bad.yaml", """
        name: testagent
        role: tester
        tool_allow_list: [alpha]
        system_prompt: hi
    """)  # missing 'model'
    reg = AgentRegistry(definitions_dir=tmp_path)
    with pytest.raises(AssertionError, match="missing required key"):
        reg.load()


def test_load_rejects_duplicate_tools_in_allow_list(tmp_path, monkeypatch):
    stub = _stub_tool_registry("alpha")
    monkeypatch.setattr(registry_mod, "get_tool_registry", lambda: stub)
    _write_yaml(tmp_path, "bad.yaml", """
        name: testagent
        role: tester
        model: haiku
        tool_allow_list: [alpha, alpha]
        system_prompt: hi
    """)
    reg = AgentRegistry(definitions_dir=tmp_path)
    with pytest.raises(AssertionError, match="duplicates"):
        reg.load()


def test_load_is_idempotent(tmp_path, monkeypatch):
    stub = _stub_tool_registry("alpha", "beta")
    monkeypatch.setattr(registry_mod, "get_tool_registry", lambda: stub)
    _write_yaml(tmp_path, "testagent.yaml", VALID_YAML)

    reg = AgentRegistry(definitions_dir=tmp_path)
    reg.load()
    reg.load()  # second call must be a no-op
    assert len(reg.list_agents()) == 1


def test_get_unknown_agent_raises(tmp_path):
    reg = AgentRegistry(definitions_dir=tmp_path)  # empty dir
    with pytest.raises(KeyError, match="Unknown agent"):
        reg.get("missing")


def test_snapshot_is_read_only(tmp_path, monkeypatch):
    stub = _stub_tool_registry("alpha", "beta")
    monkeypatch.setattr(registry_mod, "get_tool_registry", lambda: stub)
    _write_yaml(tmp_path, "testagent.yaml", VALID_YAML)

    reg = AgentRegistry(definitions_dir=tmp_path)
    snap = reg.snapshot()
    assert "testagent" in snap
    with pytest.raises(TypeError):
        snap["other"] = None  # type: ignore[index]
