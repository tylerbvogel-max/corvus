"""Unit tests for Pattern #6 — BranchStage conditional fan-out.

Covers the contract described in docs/design/pattern-6-branch-stage.md:
- Chain-through-branch: sub-pipeline output replaces input in the parent chain.
- Null-route ("" key) pass-through: no sub-pipeline runs, inp flows through.
- ShortCircuit inside a branch: exits sub-pipeline only; parent continues.
- ShortCircuit at top level: behaves unchanged (regression check for the
  iterative-runner rewrite).
- PipelineStageError wraps the failing stage with a slash-prefixed name.
- Telemetry rows carry the branch_context field for sub-pipeline stages.
- Unknown branch key raises PipelineStageError.
- Nested branches up to MAX_BRANCH_DEPTH; one past the cap asserts.

Does not hit the DB — ctx.db = None throughout; no test stage dereferences it.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import pytest

from app.services.pipeline import (
    MAX_BRANCH_DEPTH,
    PipelineContext,
    PipelineStageError,
    ShortCircuit,
    run_pipeline,
)


# ── Test-only fake stages ───────────────────────────────────────────────

class _AddStage:
    """Regular Stage: add `inc` to an int input."""

    def __init__(self, name: str, inc: int) -> None:
        self.name = name
        self.inc = inc

    async def run(self, inp: int, _ctx: object) -> int:
        return inp + self.inc

    def describe(self, out: int) -> dict[str, Any]:
        return {"value": out}


class _BoomStage:
    def __init__(self, name: str = "boom") -> None:
        self.name = name

    async def run(self, _inp: Any, _ctx: object) -> Any:
        raise ValueError("kaboom")

    def describe(self, _out: Any) -> dict[str, Any]:
        return {}


class _ShortCircuitStage:
    def __init__(self, name: str, value: Any) -> None:
        self.name = name
        self.value = value

    async def run(self, _inp: Any, _ctx: object) -> Any:
        raise ShortCircuit(self.value)

    def describe(self, _out: Any) -> dict[str, Any]:
        return {}


class _StaticRouter:
    """BranchStage that always routes to a fixed key."""

    def __init__(
        self, name: str, branches: Mapping[str, Sequence[Any]], chosen: str,
    ) -> None:
        self.name = name
        self.branches = branches
        self.chosen = chosen

    async def route(self, _inp: Any, _ctx: object) -> str:
        return self.chosen

    def describe(self, _out: Any) -> dict[str, Any]:
        return {}


class _PredicateRouter:
    """BranchStage that routes by applying a predicate to the input."""

    def __init__(
        self, name: str, branches: Mapping[str, Sequence[Any]],
        predicate,  # callable: (inp) -> str
    ) -> None:
        self.name = name
        self.branches = branches
        self.predicate = predicate

    async def route(self, inp: Any, _ctx: object) -> str:
        return self.predicate(inp)

    def describe(self, _out: Any) -> dict[str, Any]:
        return {}


class _RouterRaises:
    """BranchStage whose route() itself raises — tests router-failure wrapping."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.branches: dict[str, Sequence[Any]] = {"unused": []}

    async def route(self, _inp: Any, _ctx: object) -> str:
        raise RuntimeError("router bug")

    def describe(self, _out: Any) -> dict[str, Any]:
        return {}


# ── Tests ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_branch_routes_and_sub_pipeline_feeds_parent():
    """Sub-pipeline runs and its output replaces `current` in the parent chain."""
    ctx = PipelineContext(db=None)  # type: ignore[arg-type]
    router = _StaticRouter(
        "router",
        branches={
            "plus_ten": [_AddStage("inner_a", 5), _AddStage("inner_b", 5)],
            "plus_one": [_AddStage("inner_c", 1)],
        },
        chosen="plus_ten",
    )
    stages = [_AddStage("pre", 1), router, _AddStage("post", 100)]
    result = await run_pipeline(stages, 0, ctx)
    # pre: 0+1=1; branch (plus_ten): 1+5=6, 6+5=11; post: 11+100=111.
    assert result == 111
    names = [t.stage for t in ctx.telemetry]
    assert names == ["pre", "router", "inner_a", "inner_b", "post"]
    # Inner stages carry the branch_context; outer stages do not.
    contexts = [t.branch_context for t in ctx.telemetry]
    assert contexts == [None, None, "router:plus_ten/", "router:plus_ten/", None]
    # Router row carries branch_taken + sub_stages.
    router_row = ctx.telemetry[1]
    assert router_row.detail == {"branch_taken": "plus_ten", "sub_stages": 2}


@pytest.mark.asyncio
async def test_branch_null_route_passes_through():
    """route() returning '' means no sub-pipeline runs; inp flows through."""
    ctx = PipelineContext(db=None)  # type: ignore[arg-type]
    router = _StaticRouter(
        "opt_router",
        branches={"ignored": [_AddStage("never", 999)]},
        chosen="",
    )
    stages = [_AddStage("pre", 10), router, _AddStage("post", 100)]
    result = await run_pipeline(stages, 0, ctx)
    # pre: 10; router: pass-through; post: 10+100=110.
    assert result == 110
    assert [t.stage for t in ctx.telemetry] == ["pre", "opt_router", "post"]
    assert ctx.telemetry[1].detail == {"branch_taken": "", "sub_stages": 0}


@pytest.mark.asyncio
async def test_short_circuit_inside_branch_exits_sub_only():
    """ShortCircuit in a sub-pipeline returns its value as the branch output;
    parent chain continues with that value."""
    ctx = PipelineContext(db=None)  # type: ignore[arg-type]
    router = _StaticRouter(
        "router",
        branches={
            "short": [_AddStage("b_pre", 1), _ShortCircuitStage("bail", 777), _AddStage("b_never", 5)],
        },
        chosen="short",
    )
    stages = [_AddStage("pre", 1), router, _AddStage("post", 3)]
    result = await run_pipeline(stages, 0, ctx)
    # pre: 1; branch short-circuits to 777; post: 777+3=780.
    assert result == 780
    names = [t.stage for t in ctx.telemetry]
    assert names == ["pre", "router", "b_pre", "bail", "post"]
    # `b_never` must not have run.
    assert "b_never" not in names


@pytest.mark.asyncio
async def test_short_circuit_at_top_level_still_exits_immediately():
    """Regression: ShortCircuit at the outermost frame still returns immediately."""
    ctx = PipelineContext(db=None)  # type: ignore[arg-type]
    stages = [_AddStage("a", 1), _ShortCircuitStage("stop", "final"), _AddStage("never", 999)]
    result = await run_pipeline(stages, 0, ctx)
    assert result == "final"
    assert [t.stage for t in ctx.telemetry] == ["a", "stop"]


@pytest.mark.asyncio
async def test_branch_inner_stage_error_is_slash_prefixed():
    """A raise inside a sub-pipeline surfaces as router/key/inner_name."""
    ctx = PipelineContext(db=None)  # type: ignore[arg-type]
    router = _StaticRouter(
        "router",
        branches={"broken": [_AddStage("b_pre", 1), _BoomStage("b_kaboom")]},
        chosen="broken",
    )
    with pytest.raises(PipelineStageError) as exc_info:
        await run_pipeline([router], 0, ctx)
    assert exc_info.value.stage_name == "router:broken/b_kaboom"
    assert isinstance(exc_info.value.original, ValueError)
    # The error telemetry row is tagged with the branch context.
    err_rows = [t for t in ctx.telemetry if t.status == "error"]
    assert len(err_rows) == 1
    assert err_rows[0].stage == "b_kaboom"
    assert err_rows[0].branch_context == "router:broken/"


@pytest.mark.asyncio
async def test_router_raise_is_wrapped_at_router_name():
    """Exception from route() itself wraps at the router's name, not a sub-stage."""
    ctx = PipelineContext(db=None)  # type: ignore[arg-type]
    router = _RouterRaises("bad_router")
    with pytest.raises(PipelineStageError) as exc_info:
        await run_pipeline([router], 0, ctx)
    assert exc_info.value.stage_name == "bad_router"
    assert isinstance(exc_info.value.original, RuntimeError)


@pytest.mark.asyncio
async def test_unknown_branch_key_raises():
    """route() returning a key not in `branches` is a PipelineStageError."""
    ctx = PipelineContext(db=None)  # type: ignore[arg-type]
    router = _StaticRouter(
        "router",
        branches={"a": [_AddStage("a1", 1)]},
        chosen="does_not_exist",
    )
    with pytest.raises(PipelineStageError) as exc_info:
        await run_pipeline([router], 0, ctx)
    assert exc_info.value.stage_name == "router"
    assert isinstance(exc_info.value.original, KeyError)


@pytest.mark.asyncio
async def test_predicate_router_selects_by_input():
    """route() can read the input — real routers dispatch on runtime state."""
    ctx = PipelineContext(db=None)  # type: ignore[arg-type]
    router = _PredicateRouter(
        "by_sign",
        branches={
            "pos": [_AddStage("pos_stage", 1)],
            "neg": [_AddStage("neg_stage", -1)],
            "zero": [_AddStage("zero_stage", 42)],
        },
        predicate=lambda n: "pos" if n > 0 else "neg" if n < 0 else "zero",
    )
    ctx2 = PipelineContext(db=None)  # type: ignore[arg-type]
    ctx3 = PipelineContext(db=None)  # type: ignore[arg-type]
    assert await run_pipeline([router], 5, ctx) == 6
    assert await run_pipeline([router], -5, ctx2) == -6
    assert await run_pipeline([router], 0, ctx3) == 42


@pytest.mark.asyncio
async def test_telemetry_json_preserves_branch_context():
    """to_json() surfaces branch_context when set and omits it when not."""
    ctx = PipelineContext(db=None)  # type: ignore[arg-type]
    router = _StaticRouter(
        "r",
        branches={"k": [_AddStage("inner", 1)]},
        chosen="k",
    )
    await run_pipeline([_AddStage("outer", 0), router], 0, ctx)
    serialized = ctx.telemetry_json()
    outer_row = next(r for r in serialized if r["stage"] == "outer")
    inner_row = next(r for r in serialized if r["stage"] == "inner")
    assert "branch_context" not in outer_row
    assert inner_row["branch_context"] == "r:k/"


@pytest.mark.asyncio
async def test_nested_branches_up_to_depth_cap():
    """Nesting up to MAX_BRANCH_DEPTH levels works; one deeper asserts."""
    # Build a tower of routers, each routing into the next one, N levels deep,
    # ending at an _AddStage leaf. Iterative (JPL-1) — construct from the leaf
    # outward, wrapping one router at each level.
    def tower(depth: int) -> list:
        stages: list = [_AddStage("leaf", 7)]
        # JPL-2 bounded loop — depth is a finite int (0 ≤ depth ≤ MAX_BRANCH_DEPTH+1).
        for i in range(1, depth + 1):
            stages = [_StaticRouter(f"r{i}", branches={"down": stages}, chosen="down")]
        return stages

    # At exactly MAX_BRANCH_DEPTH, the runner should complete.
    ctx = PipelineContext(db=None)  # type: ignore[arg-type]
    result = await run_pipeline(tower(MAX_BRANCH_DEPTH), 0, ctx)
    assert result == 7
    # The leaf telemetry row carries a fully-nested branch_context prefix.
    leaf_row = next(r for r in ctx.telemetry if r.stage == "leaf")
    assert leaf_row.branch_context is not None
    assert leaf_row.branch_context.count("/") == MAX_BRANCH_DEPTH

    # One deeper — depth MAX_BRANCH_DEPTH+1 — must trip the assertion.
    ctx_deep = PipelineContext(db=None)  # type: ignore[arg-type]
    with pytest.raises(AssertionError, match="MAX_BRANCH_DEPTH"):
        await run_pipeline(tower(MAX_BRANCH_DEPTH + 1), 0, ctx_deep)
