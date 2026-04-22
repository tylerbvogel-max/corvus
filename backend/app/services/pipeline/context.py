"""Shared container threaded through every stage in the typed pipeline DAG."""

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.pipeline.stage import StageTelemetry


StageCallback = Callable[[str, dict], Awaitable[None]] | None


@dataclass
class PipelineContext:
    """Threaded through every stage.

    Carries the db session (stages issue their own queries), the optional
    `on_stage` emit callback (SSE streaming in `/query/stream`), and a
    growing telemetry list that the runner appends to after each stage.

    `metadata` is a free-form scratch dict for stages that need to stash
    side-channel data for later stages without polluting the typed input/
    output contract — use sparingly.
    """

    db: AsyncSession
    on_stage: StageCallback = None
    telemetry: list[StageTelemetry] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def telemetry_json(self) -> list[dict[str, Any]]:
        """Serializable view of telemetry for persistence on the Query row."""
        return [t.to_json() for t in self.telemetry]
