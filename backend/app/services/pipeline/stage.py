"""Stage protocol + telemetry envelope + error type for the typed pipeline DAG."""

from dataclasses import dataclass, field
from typing import Any, Generic, Mapping, Protocol, Sequence, TypeVar, runtime_checkable


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


@runtime_checkable
class BranchStage(Protocol, Generic[IN, OUT]):
    """Pattern #6 — conditional fan-out. Routes the input to one of N sub-pipelines.

    The runner detects BranchStage instances by duck-typing (presence of both
    `branches` and `route`) and invokes `route()` to pick a branch key. The
    selected sub-pipeline is executed with `inp` threaded as its initial input;
    its final output becomes the BranchStage's output and is handed to the next
    parent stage.

    Routing contract:
    - `route()` returns a string key that is either in `self.branches` or `""`.
    - `""` means "pass-through" — no sub-pipeline runs and `inp` flows through
      unchanged. Chosen over `Optional[str]` for homogeneity with the key type.
    - Any other key not present in `self.branches` is a programmer error; the
      runner raises `PipelineStageError`.

    See `docs/design/pattern-6-branch-stage.md` for the full design rationale.
    """

    name: str
    branches: Mapping[str, Sequence[Any]]  # branch_key → Sequence[Stage | BranchStage]

    async def route(self, inp: IN, ctx: "object") -> str: ...

    def describe(self, out: OUT) -> dict[str, Any]: ...


def is_branch_stage(stage: Any) -> bool:
    """Duck-typed check — runtime_checkable Protocols don't inspect method bodies,
    so we combine `isinstance(stage, BranchStage)` with a `branches` attr check
    to ensure we found a real BranchStage and not a plain Stage that happens to
    implement `name`.
    """
    return hasattr(stage, "branches") and hasattr(stage, "route") and callable(
        getattr(stage, "route", None)
    )


@dataclass
class StageTelemetry:
    """Per-stage record appended to `PipelineContext.telemetry`."""
    stage: str
    status: str  # "done" | "error" | "skipped"
    duration_ms: float
    detail: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None
    # Pattern #6: path of enclosing BranchStage decisions, e.g.
    # "query_generation_router:gap_targeted/". Empty for stages in the
    # top-level pipeline. Included in to_json() only when set.
    branch_context: str | None = None

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
        if self.branch_context:
            out["branch_context"] = self.branch_context
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
