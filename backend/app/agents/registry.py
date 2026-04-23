"""Agent YAML loader and AgentRegistry.

Agent definitions live in ``backend/app/agents/definitions/*.yaml``. Each file
declares:

  name: dedup
  role: proposal_curator
  description: One-liner for /agents UI.
  model: haiku                   # MODEL_REGISTRY key
  max_tokens: 2048               # per LLM turn
  max_turns: 8                   # bounded agent loop
  tool_allow_list:
    - list_pending_proposals
    - get_proposal_detail
    - ...
  system_prompt: |
    You are the proposal_curator agent. Your job is to ...
  trigger:
    manual: true                 # admin-triggered via /v1/agents/:name/run
    schedule:                    # optional cron-style; empty = no scheduled run
      enabled: false
      cron: "0 */4 * * *"

Validation is fail-closed: any tool name not in the ToolRegistry raises at load
time, and unknown top-level keys are rejected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import yaml

from app.agents.tool_base import get_tool_registry


_DEFINITIONS_DIR = Path(__file__).parent / "definitions"
_ALLOWED_KEYS = frozenset({
    "name", "role", "description", "model", "max_tokens", "max_turns",
    "tool_allow_list", "system_prompt", "trigger",
    # Optional: plain-English explanation of the agent for the admin
    # Knowledge → Agents page. The `description` field is the short
    # machine-shape summary; `admin_description` is the human-written
    # "what does this actually do" paragraph. Falls back to description
    # when absent so existing YAMLs keep working unchanged.
    "admin_description",
})


@dataclass(frozen=True)
class AgentTrigger:
    manual: bool = True
    schedule_enabled: bool = False
    schedule_cron: str | None = None


@dataclass(frozen=True)
class AgentDefinition:
    """Parsed and validated agent YAML."""

    name: str
    role: str
    description: str
    model: str
    max_tokens: int
    max_turns: int
    tool_allow_list: tuple[str, ...]
    system_prompt: str
    trigger: AgentTrigger
    source_path: Path
    # Plain-English explanation shown on the admin Knowledge → Agents
    # page. Falls back to `description` when absent so legacy YAMLs
    # (or any new one that omits it) still render reasonable copy.
    admin_description: str = ""


class AgentRegistry:
    """Loads agent YAML definitions and validates tool allow-lists."""

    def __init__(self, definitions_dir: Path | None = None) -> None:
        self._dir = definitions_dir or _DEFINITIONS_DIR
        self._agents: dict[str, AgentDefinition] = {}
        self._loaded = False

    def load(self) -> None:
        """Load all YAML files in the definitions directory. Idempotent."""
        if self._loaded:
            return
        if not self._dir.exists():
            self._loaded = True
            return

        registry = get_tool_registry()
        for yaml_path in sorted(self._dir.glob("*.yaml")):
            agent = _parse_yaml(yaml_path)
            _validate_tools(agent, registry)
            assert agent.name not in self._agents, (
                f"Duplicate agent name {agent.name!r} in {yaml_path}"
            )
            self._agents[agent.name] = agent
        self._loaded = True

    def get(self, name: str) -> AgentDefinition:
        self.load()
        if name not in self._agents:
            raise KeyError(f"Unknown agent: {name!r}. Registered: {sorted(self._agents.keys())}")
        return self._agents[name]

    def has(self, name: str) -> bool:
        self.load()
        return name in self._agents

    def list_agents(self) -> list[AgentDefinition]:
        self.load()
        return sorted(self._agents.values(), key=lambda a: a.name)

    def snapshot(self) -> Mapping[str, AgentDefinition]:
        self.load()
        return MappingProxyType(dict(self._agents))


_REGISTRY = AgentRegistry()


def get_agent_registry() -> AgentRegistry:
    return _REGISTRY


def _parse_yaml(path: Path) -> AgentDefinition:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict), f"{path}: YAML root must be a mapping"

    unknown = set(raw.keys()) - _ALLOWED_KEYS
    assert not unknown, f"{path}: unknown keys {sorted(unknown)}"

    for required in ("name", "role", "model", "tool_allow_list", "system_prompt"):
        assert required in raw, f"{path}: missing required key {required!r}"

    name = str(raw["name"]).strip()
    assert name, f"{path}: name must be non-empty"

    tool_list = raw["tool_allow_list"]
    assert isinstance(tool_list, list) and all(isinstance(t, str) for t in tool_list), (
        f"{path}: tool_allow_list must be a list of strings"
    )
    assert len(tool_list) == len(set(tool_list)), (
        f"{path}: tool_allow_list has duplicates"
    )

    trigger_raw = raw.get("trigger", {}) or {}
    schedule = trigger_raw.get("schedule", {}) or {}
    trigger = AgentTrigger(
        manual=bool(trigger_raw.get("manual", True)),
        schedule_enabled=bool(schedule.get("enabled", False)),
        schedule_cron=str(schedule["cron"]) if schedule.get("cron") else None,
    )

    return AgentDefinition(
        name=name,
        role=str(raw["role"]).strip(),
        description=str(raw.get("description", "")).strip(),
        model=str(raw["model"]).strip(),
        max_tokens=int(raw.get("max_tokens", 2048)),
        max_turns=int(raw.get("max_turns", 8)),
        tool_allow_list=tuple(tool_list),
        system_prompt=str(raw["system_prompt"]).strip(),
        trigger=trigger,
        source_path=path,
        admin_description=str(raw.get("admin_description", "")).strip(),
    )


def _validate_tools(agent: AgentDefinition, registry) -> None:
    """Fail-closed: every tool in allow-list must be in the ToolRegistry."""
    unknown = [t for t in agent.tool_allow_list if not registry.has(t)]
    assert not unknown, (
        f"Agent {agent.name!r}: unknown tools in allow-list: {unknown}. "
        f"Registered tools: {sorted(registry.snapshot().keys())}"
    )
