"""Stage protocol + telemetry envelope + error type for the typed pipeline DAG."""

from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable


IN = TypeVar("IN")
OUT = TypeVar("OUT")


@runtime_checkable
class Stage(Protocol, Generic[IN, OUT]):
    """One named step in the pipeline.

    Implementations declare the input and output types they expect. The
    runner passes the previous stage's output as `inp` and threads a shared
    `PipelineContext` for cross-stage state (db session, telemetry sink, etc.).
    """

    name: str

    async def run(self, inp: IN, ctx: "object") -> OUT: ...

    def describe(self, out: OUT) -> dict[str, Any]:
        """Optional per-stage detail extracted from the output for telemetry.

        Default is empty; stages override to surface counts, intent labels,
        etc. Keep the payload small — it's persisted on the query row.
        """
        ...


@dataclass
class StageTelemetry:
    """Per-stage record appended to `PipelineContext.telemetry`."""
    stage: str
    status: str  # "done" | "error" | "skipped"
    duration_ms: float
    detail: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "stage": self.stage,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 2),
        }
        if self.detail:
            out["detail"] = self.detail
        if self.error_message:
            out["error_message"] = self.error_message
        return out


class PipelineStageError(RuntimeError):
    """Raised by `run_pipeline` when a stage's `.run()` raises.

    Wraps the original exception and tags it with the stage name so the HTTP
    layer can format a structured failure response (`failed_stage` field)
    without parsing the traceback.
    """

    def __init__(self, stage_name: str, original: BaseException):
        super().__init__(f"Pipeline stage '{stage_name}' failed: {original}")
        self.stage_name = stage_name
        self.original = original


class ShortCircuit(Exception):  # noqa: N818 — not an error, it's a control-flow signal
    """Raised by a stage to abort the remaining pipeline with a final value.

    Example: the structural-resolve stage can short-circuit the rest of the
    pipeline when it finds a direct match, skipping classify/score/spread/etc.
    The runner catches this and returns the payload without marking a failure.
    """

    def __init__(self, final_value: Any):
        super().__init__("pipeline short-circuit")
        self.final_value = final_value
