# AIP Pattern #6 — BranchStage (ontology branching, pipeline primitive)

**Status:** design, 2026-04-22
**Scope:** Interpretation 1 (pipeline primitive). Interpretation 2 (data-level branch-scoped IDs on neurons/edges/proposals) is explicitly deferred — see `gov-aip-p2b` in master-corvus `roadmap-state.json` for the scope-decision record.

## Context

Phase 2b pattern #6. Builds on Patterns #5 (typed pipeline DAG, shipped 2026-04-21) and #8 (autopilot-as-typed-agent, shipped 2026-04-22).

Today the query pipeline and autopilot pipeline are linear chains of `Stage` instances. Stages that need to behave differently based on runtime state encode that branching *inside themselves* — most visibly in `app/routers/autopilot.py::_generate_query` (gap-targeted vs. directive prompt) and in a couple of `if settings.*_enabled` gates inside the query-pipeline stages. The branch is invisible to telemetry, adding a third variant means editing the stage's body, and swapping strategies for measurement requires code changes not configuration.

`BranchStage` adds a first-class "conditional fan-out" primitive on top of the existing `Stage` / `run_pipeline` contract. A BranchStage has a `route(state) -> str` method and a `dict[str, sub_pipeline]`. The runner asks `route()` which branch to take, executes the matching sub-pipeline, and composes the result back into the parent chain. Each branch's stages get their own telemetry rows tagged with the branch context.

## Non-goals

- **Data-level branching.** No `branch_id` column on neurons, edges, or proposals. No mainline/feature-branch merge semantics. No admin UI for branch inspection. These may become their own roadmap item if/when `#204`/`#205` demand A/B agent measurement; they are not part of #6.
- **Parallel branches.** Exactly one branch runs per BranchStage invocation. If we ever need fan-out-and-join (all branches run, results merged), that is a separate primitive — call it `ParallelStage` — and outside this design.
- **Dynamic branch registration.** The `branches` dict is fixed at construction. Adding a new branch at runtime is out of scope.

## Protocol

```python
# backend/app/services/pipeline/stage.py

from typing import Mapping, Protocol, Sequence, runtime_checkable

@runtime_checkable
class BranchStage(Protocol, Generic[IN, OUT]):
    """Conditional fan-out — route the input to one of N sub-pipelines.

    The runner detects BranchStage instances via isinstance() and invokes
    route() to pick a sub-pipeline. The sub-pipeline's final output becomes
    the BranchStage's output and is handed to the next parent stage.
    """

    name: str
    branches: Mapping[str, Sequence["Stage | BranchStage"]]

    async def route(self, inp: IN, ctx: "PipelineContext") -> str:
        """Return a key in `branches`, or "" for pass-through (no branch)."""
        ...

    def describe(self, out: OUT) -> dict[str, Any]:
        """Per-BranchStage telemetry detail. Default: {}."""
        ...
```

**Contract:**

- `route()` returns a branch key that is either in `self.branches` or the empty string.
- Empty string (`""`) means "no branch, pass-through" — the runner skips the sub-pipeline and returns `inp` unchanged. Cleaner than making `branches` carry a sentinel `"default": []` entry or making the return type `str | None`.
- Returning a key not in `self.branches` and not `""` is a programmer error: the runner raises `PipelineStageError(branch_stage.name, KeyError(key))`.
- `branches` values can themselves contain BranchStages — nesting is supported up to a hard depth cap (see below).

## Runner integration

One change in `backend/app/services/pipeline/runner.py::run_pipeline`: when the current stage is a `BranchStage`, call `route(inp, ctx)`, look up the sub-pipeline, and execute it. To keep JPL-1 (no recursion) satisfied, the runner is rewritten as an explicit stack-based iteration rather than recursing into a sub-`run_pipeline` call:

```python
async def run_pipeline(stages, initial_input, ctx):
    # One work-item per (stages, current, depth). Append branch sub-pipelines
    # as new work items rather than recursing.
    stack = [_Frame(stages=list(stages), current=initial_input, depth=0, label="")]
    ...
    while stack:
        frame = stack[-1]
        if frame.idx >= len(frame.stages):
            stack.pop()
            if stack:
                stack[-1].current = frame.current  # bubble up sub-pipeline output
            continue
        stage = frame.stages[frame.idx]
        frame.idx += 1
        if _is_branch_stage(stage):
            key = await stage.route(frame.current, ctx)
            if key == "":
                # pass-through: record telemetry, move on
                ...
                continue
            sub = stage.branches.get(key)
            if sub is None:
                raise PipelineStageError(stage.name, KeyError(key))
            assert frame.depth + 1 <= MAX_BRANCH_DEPTH, \
                f"branch depth > {MAX_BRANCH_DEPTH}"
            # Record the BranchStage's own telemetry row before descending
            ...
            stack.append(_Frame(
                stages=list(sub),
                current=frame.current,
                depth=frame.depth + 1,
                label=f"{stage.name}:{key}",
            ))
        else:
            # existing Stage path, unchanged
            ...
```

`MAX_BRANCH_DEPTH = 4` is the hard cap. Deeper than 4 branches-inside-branches is almost certainly a design mistake; assertion will catch it in dev.

## Telemetry

Each sub-pipeline `StageTelemetry` row gains an optional `branch_context: str | None = None` field. Values are the `label` from the stack frame — e.g. `"query_generation_router:gap_targeted"`. The parent BranchStage's own row has `branch_context=None` and its `detail` dict carries `{"branch_taken": key, "sub_stages": N}`.

**JSONB migration:** none. `stage_telemetry_json` is already schemaless JSONB; adding a new optional field is a no-op on disk. The existing waterfall UI in `AutopilotPage.tsx::StageTimingTable` renders extra rows without modification. The `pipelineDetail.tsx::categorizeDetail` helper picks up `branch_taken` as a flag chip and `sub_stages` as a count chip automatically.

## Error semantics

Inside a branch, any exception other than `ShortCircuit` raised by a sub-stage gets wrapped:

```python
raise PipelineStageError(
    stage_name=f"{branch_stage.name}/{branch_key}/{inner_stage.name}",
    original=exc,
) from exc
```

The slash-prefixed name makes failure location unambiguous in logs, the `failed_stage` field on `/query/stream` SSE errors, and the `error_message` on autopilot run records.

## ShortCircuit semantics

Inside a branch, `ShortCircuit(value)` exits the *sub-pipeline* only. Its payload becomes the BranchStage's output; the parent pipeline's remaining stages continue. This matches the current rule (ShortCircuit exits the immediately-enclosing `run_pipeline` call) — no new behavior.

## Two concrete use cases

### (a) Autopilot query generation — first consumer

Today `_generate_query` in `app/routers/autopilot.py` contains:

```python
if gap and gap.source != "directive":
    system_prompt = "You generate targeted test queries ..."  # gap-targeted
    user_prompt = f"...{gap_section}{recent_section}"
else:
    system_prompt = "You generate realistic test queries ..."  # directive
    user_prompt = f"...{focus_section}{recent_section}"
result = await llm_chat(system_prompt, user_prompt, ...)
```

Refactor: replace `QueryGenerationStage` with a `QueryGenerationRouter(BranchStage)` whose `route()` returns `"gap_targeted"` if `state.gap and state.gap_source != "directive"` else `"directive"`. Two single-stage sub-pipelines: `GapTargetedGenerationStage` and `DirectiveGenerationStage`. Each owns its prompt text and LLM call.

Outcome in the waterfall: three rows instead of one (router + the chosen generator), with the branch context visible.

### (b) Query-flow structural resolve — NOT the first consumer

`StructuralResolveStage` already uses `ShortCircuit(prepared_context)` to skip the rest of the pipeline on a direct match. Converting it to a BranchStage (`fast_match` branch vs. `full_pipeline` branch) is technically cleaner — both outcomes become observable — but ShortCircuit is already a first-class citizen and the `fast_match` case is a one-line happy-path in telemetry today. Not worth the churn unless we grow a third outcome. **Revisit when/if a third structural outcome appears.**

## Phased ship plan

| Ship | What | Commit |
|---|---|---|
| 1 | This design doc | One commit. Pure documentation — no runtime change. |
| 2 | BranchStage Protocol + runner rewrite (iterative) + telemetry field + 5-test unit suite. No consumer yet. | One commit. All-green pytest, nasa_lint strict-clean. |
| 3 | First consumer — autopilot QueryGenerationRouter refactor. Waterfall UI sanity-checked. | One commit. |

Each ship is independently reviewable and independently shippable. Ship 3 is optional — the primitive is useful on its own and a consumer can land later — but shipping it validates the design in production.

## Caveats / revisit notes

1. **Null-route sentinel.** Using `""` for "no branch taken" is a string-based choice made to keep the return type `str`. Alternative was `str | None`. Either is fine; the empty-string form was picked for homogeneity with the routing key.

2. **Depth cap of 4.** Arbitrary upper bound that catches obvious design mistakes. If we ever find a legit use case for depth > 4, loosen it then — not speculatively.

3. **Telemetry field name (`branch_context`).** Alternate names considered: `branch`, `branch_path`, `enclosing_branch`. Picked `branch_context` because it's what the value actually represents (the full path of enclosing branch decisions, not just the innermost one).

4. **Slash-prefixed error names.** `router_name/branch_key/inner_stage` may produce long names when deeply nested. Acceptable — the trade-off favors failure-location clarity over log brevity.

5. **Interpretation 2 (data-level branching) revisit trigger.** If the second agent wave (`#204`/`#205`) ships and we find that eval-run deltas against the pre-agent baseline (locked 2026-04-20, eval_run_id=3) are insufficient for attributing drift to specific agents, open a new roadmap node — do not back-fit under #6. Keep pattern labels stable.

## Related files

- `backend/app/services/pipeline/stage.py` — BranchStage Protocol lives here
- `backend/app/services/pipeline/runner.py` — iterative runner lives here
- `backend/app/services/pipeline/__init__.py` — re-export BranchStage
- `backend/tests/test_branch_stage.py` — new unit-test file (ship 2)
- `backend/app/services/autopilot_pipeline/stages/query_generation_stage.py` — first consumer, rewritten as a router (ship 3)
- `~/.claude/plans/glowing-whistling-pebble.md` Phase F — original design sketch this elaborates on
- `~/Projects/master-corvus/public/roadmap-state.json::gov-aip-p2b` — scope-decision record
