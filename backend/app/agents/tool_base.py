"""Tool protocol + registry for agent runtime.

A Tool is a typed, async callable that takes a dict input and returns a dict
result. Each tool declares an input_schema (JSON Schema fragment) and a short
description — the runtime passes these to the LLM so it knows how to call them.

Tools are registered at import time via @register_tool("name"). Agent YAML
definitions carry a tool_allow_list; the runtime validates that every name in
the allow-list resolves to a registered tool (fail-closed) and rejects any
LLM-requested tool that is not in the agent's allow-list (also fail-closed).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping

from sqlalchemy.ext.asyncio import AsyncSession


ToolFunc = Callable[[AsyncSession, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Tool:
    """A callable action an agent can invoke.

    Attributes:
        name: unique identifier (kebab-case by convention).
        description: one-line intent — shown to the LLM.
        input_schema: JSON Schema fragment for the input dict.
        func: async callable (session, input_dict) -> output_dict.
        is_mutating: True if the tool writes state. Mutating tools record
            an Action row; read-only tools do not (audit noise reduction).
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    func: ToolFunc
    is_mutating: bool = False


class ToolRegistry:
    """Global, append-only registry of Tool instances.

    Registered at import time via @register_tool. Lookup is strict —
    unknown names raise KeyError, so agent allow-lists fail-closed.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        assert tool.name, "Tool.name must be non-empty"
        assert tool.name not in self._tools, f"Tool already registered: {tool.name!r}"
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        assert name, "Tool name must be non-empty"
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name!r}. Registered: {sorted(self._tools.keys())}")
        return self._tools[name]

    def has(self, name: str) -> bool:
        return name in self._tools

    def snapshot(self) -> Mapping[str, Tool]:
        """Read-only view of the registry — for diagnostics and tests."""
        return MappingProxyType(dict(self._tools))


_REGISTRY = ToolRegistry()


def get_tool_registry() -> ToolRegistry:
    return _REGISTRY


def register_tool(
    name: str,
    *,
    description: str,
    input_schema: dict[str, Any],
    is_mutating: bool = False,
) -> Callable[[ToolFunc], ToolFunc]:
    """Decorator to register a tool at import time.

    Example:
        @register_tool(
            "list_pending_proposals",
            description="List pending proposals in 'proposed' state.",
            input_schema={"type": "object", "properties": {"limit": {"type": "integer"}}},
            is_mutating=False,
        )
        async def list_pending_proposals(session, inp):
            ...
    """
    assert isinstance(name, str) and name, "tool name must be a non-empty string"
    assert isinstance(description, str) and description, "description must be non-empty"
    assert isinstance(input_schema, dict), "input_schema must be a dict (JSON Schema fragment)"

    def decorator(func: ToolFunc) -> ToolFunc:
        assert asyncio.iscoroutinefunction(func), f"Tool {name!r} must be async"
        _REGISTRY.register(
            Tool(
                name=name,
                description=description,
                input_schema=input_schema,
                func=func,
                is_mutating=is_mutating,
            )
        )
        return func

    return decorator
