"""Tool implementations, grouped by concern.

Import this package to register all tools with the global ToolRegistry.
Modules must be imported for their @register_tool decorators to run.
"""

from app.agents.tools import dedup_tools  # noqa: F401 — side-effect registration

__all__: list[str] = []
