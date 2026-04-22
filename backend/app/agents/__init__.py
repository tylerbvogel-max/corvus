"""Phase 4 agent runtime — headless, goal-directed maintenance agents.

Agents operate as a side activity on the graph substrate (proposals, integrity
findings, document ingestion) — never the query hot path. Each agent is a YAML
definition declaring its role, a tool allow-list (fail-closed), and a system
prompt. Tools are the only way an agent can observe or mutate state.

Every tool invocation records an Action row with actor_type="agent",
actor_id=<agent_name>, and a reasoning trace, preserving full audit provenance.

Subpackages:
  - tool_base: Tool protocol, ToolRegistry, @register_tool decorator
  - registry:  AgentDefinition YAML loader + validation
  - runtime:   execute_agent() — LLM loop + tool dispatch + action recording
  - tools/:    concrete tool implementations, one module per agent concern
  - definitions/: YAML agent definitions
"""

from app.agents.tool_base import Tool, ToolRegistry, get_tool_registry, register_tool
# Import tools subpackage for side-effect registration BEFORE registry loads agents.
from app.agents import tools as _tools  # noqa: F401 — registers tools at import time
from app.agents.registry import AgentDefinition, AgentRegistry, get_agent_registry
from app.agents.runtime import AgentRunResult, execute_agent

__all__ = [
    "AgentDefinition",
    "AgentRegistry",
    "AgentRunResult",
    "Tool",
    "ToolRegistry",
    "execute_agent",
    "get_agent_registry",
    "get_tool_registry",
    "register_tool",
]
