"""Agent runtime — bounded LLM loop with tool dispatch + action recording.

One ``execute_agent(...)`` call runs a single agent to completion or turn cap.
Each iteration:

    1. Assemble prompt: system_prompt + conversation history so far.
    2. Call LLM (via ``llm_chat``) and parse the returned text as a single
       JSON envelope. Two shapes are valid:
           {"tool": "<name>", "input": {...}, "reason": "why"}
           {"done": true, "summary": "what the agent did"}
    3. If tool: validate against agent allow-list (fail-closed), dispatch
       through ToolRegistry, record an Action row for mutating tools,
       append the result to conversation history, loop.
    4. If done: persist a final AgentRun summary action, return.

Bounded in three ways (JPL-2 bounded-loop compliance):
    - max_turns (per agent YAML)
    - per-tool timeout (hard-coded below)
    - total wall-clock budget (optional)

Tool dispatch is strict: an LLM-requested tool not in the agent's allow-list
raises ``ToolNotAllowedError`` and is recorded as a failed action. The agent
sees the error in its next turn and can course-correct or give up.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import AgentDefinition, get_agent_registry
from app.agents.tool_base import Tool, get_tool_registry
from app.models import Action
from app.services.llm_provider import llm_chat, effort_var


logger = logging.getLogger(__name__)


_TOOL_TIMEOUT_SECONDS = 60
_MAX_TURNS_HARD_CAP = 20  # safety bound regardless of YAML


class ToolNotAllowedError(Exception):
    """LLM requested a tool outside the agent's allow-list."""


class AgentProtocolError(Exception):
    """LLM response was not parseable as tool-call or done envelope."""


_MAX_DONE_REJECTIONS = 2  # after this many, auto-abort run


@dataclass
class AgentRunResult:
    """Summary of a completed agent run."""

    agent_name: str
    root_action_id: int
    turns: int
    tool_calls: int
    mutations: int
    errors: int
    summary: str                      # authoritative, derived from action trail
    completed_at: datetime
    run_log: list[dict] = field(default_factory=list)
    model_self_report: str = ""       # the agent's own "done" summary for audit


@dataclass
class _LoopState:
    """Mutable state threaded through the agent turn-loop."""
    conversation: list[dict]
    run_log: list[dict] = field(default_factory=list)
    tool_calls: int = 0
    mutations: int = 0
    errors: int = 0
    model_self_report: str = ""
    done_rejections: int = 0
    called_verification_tool: bool = False
    auto_aborted: bool = False
    abort_reason: str = ""
    turn_idx: int = 0
    done: bool = False


async def execute_agent(
    session: AsyncSession,
    agent_name: str,
    input_context: dict | None = None,
    triggered_by: str = "system",
    expected_mutations: int | None = None,
    require_verification_for: str | None = None,
) -> AgentRunResult:
    """Execute one agent turn-loop to completion or cap.

    Args:
        session: DB session (open, committed as Actions record).
        agent_name: key in AgentRegistry.
        input_context: optional dict merged into the agent's initial message.
        triggered_by: identity string — recorded on the root Action.
        expected_mutations: if set, reject `{done}` when mutations < this
            value. After _MAX_DONE_REJECTIONS the run auto-aborts.
        require_verification_for: if set, `{done}` requires this tool to
            have been called during the run (mandatory post-mutation read).

    Raises:
        KeyError: unknown agent_name.
        AgentProtocolError: LLM response unparseable across all turns.
    """
    assert session is not None, "session must be provided"
    assert isinstance(agent_name, str) and agent_name, "agent_name must be non-empty"

    agent = get_agent_registry().get(agent_name)
    max_turns = min(agent.max_turns, _MAX_TURNS_HARD_CAP)
    assert max_turns >= 1, f"max_turns must be >= 1, got {max_turns}"

    input_context = dict(input_context or {})
    root_action = await _create_root_action(session, agent, input_context, triggered_by)

    state = _LoopState(conversation=[
        {"role": "user", "content": _build_initial_message(agent, input_context)},
    ])
    await _run_turn_loop(
        session, agent, root_action, state, max_turns,
        expected_mutations=expected_mutations,
        require_verification_for=require_verification_for,
    )

    return await _finalize_run(
        session, agent, root_action, state, input_context,
    )


async def _create_root_action(
    session: AsyncSession,
    agent: AgentDefinition,
    input_context: dict,
    triggered_by: str,
) -> Action:
    """Insert the root `agent.run` Action for this invocation."""
    root_action = Action(
        kind="agent.run",
        actor_type="agent",
        actor_id=agent.name,
        input_json={
            "agent": agent.name, "role": agent.role, "model": agent.model,
            "context": input_context, "triggered_by": triggered_by,
        },
        reason=f"Agent run started by {triggered_by}",
        state="pending",
    )
    session.add(root_action)
    await session.flush()
    assert root_action.id is not None, "root_action must have id after flush"
    return root_action


async def _run_turn_loop(
    session: AsyncSession,
    agent: AgentDefinition,
    root_action: Action,
    state: _LoopState,
    max_turns: int,
    *,
    expected_mutations: int | None,
    require_verification_for: str | None,
) -> None:
    """Main agent turn loop — bounded by max_turns. Mutates `state`."""
    allow_list = set(agent.tool_allow_list)
    for turn_idx in range(max_turns):
        state.turn_idx = turn_idx
        try:
            llm_text = await _llm_turn(agent, state.conversation)
        except (AssertionError, ValueError, RuntimeError, OSError, json.JSONDecodeError) as exc:
            # LLM call itself failed (CLI crash, unparseable CLI output, ...).
            # Abort cleanly so the root action is finalized with the failure
            # on record instead of being left stuck in 'pending'.
            state.errors += 1
            state.auto_aborted = True
            state.abort_reason = f"LLM call failed on turn {turn_idx}: {type(exc).__name__}: {str(exc)[:300]}"
            logger.warning("agent %s %s", agent.name, state.abort_reason)
            return
        state.run_log.append({"turn": turn_idx, "llm_text_preview": llm_text[:500]})

        if not await _handle_one_turn(
            session=session,
            agent=agent,
            root_action=root_action,
            state=state,
            llm_text=llm_text,
            allow_list=allow_list,
            expected_mutations=expected_mutations,
            require_verification_for=require_verification_for,
        ):
            return


async def _handle_one_turn(
    *,
    session: AsyncSession,
    agent: AgentDefinition,
    root_action: Action,
    state: _LoopState,
    llm_text: str,
    allow_list: set[str],
    expected_mutations: int | None,
    require_verification_for: str | None,
) -> bool:
    """Process one LLM response. Returns True to continue the loop, False to exit."""
    try:
        envelope = _parse_envelope(llm_text)
    except AgentProtocolError as exc:
        _feedback_parse_error(state, agent, llm_text, exc)
        return True

    if envelope.get("done") is True:
        return _handle_done_envelope(
            state, agent, llm_text, envelope,
            expected_mutations=expected_mutations,
            require_verification_for=require_verification_for,
        )

    await _handle_tool_envelope(
        session=session, agent=agent, root_action=root_action,
        state=state, llm_text=llm_text, envelope=envelope,
        allow_list=allow_list,
        require_verification_for=require_verification_for,
    )
    return True


def _handle_done_envelope(
    state: _LoopState,
    agent: AgentDefinition,
    llm_text: str,
    envelope: dict,
    *,
    expected_mutations: int | None,
    require_verification_for: str | None,
) -> bool:
    """Apply guardrail checks to a done envelope. Returns True to keep looping,
    False to terminate. Mutates `state`."""
    state.model_self_report = str(envelope.get("summary", "")).strip()
    rejection = _evaluate_done_envelope(
        mutations=state.mutations,
        expected_mutations=expected_mutations,
        called_verification_tool=state.called_verification_tool,
        require_verification_for=require_verification_for,
    )
    if rejection is None:
        state.done = True
        return False
    state.done_rejections += 1
    logger.info("agent %s turn %d done-rejection #%d: %s",
                agent.name, state.turn_idx, state.done_rejections, rejection)
    if state.done_rejections >= _MAX_DONE_REJECTIONS:
        state.auto_aborted = True
        logger.warning("agent %s auto-aborted after %d done-rejections",
                       agent.name, state.done_rejections)
        return False
    state.conversation.append({"role": "assistant", "content": llm_text})
    state.conversation.append({"role": "user", "content": rejection})
    return True


async def _handle_tool_envelope(
    *,
    session: AsyncSession,
    agent: AgentDefinition,
    root_action: Action,
    state: _LoopState,
    llm_text: str,
    envelope: dict,
    allow_list: set[str],
    require_verification_for: str | None,
) -> None:
    """Dispatch a tool-call envelope. Mutates `state` + records Actions."""
    tool_name = envelope.get("tool")
    tool_input = envelope.get("input", {}) or {}
    tool_reason = str(envelope.get("reason", "")).strip()

    envelope_issue = _validate_envelope_fields(tool_name, tool_input)
    if envelope_issue is not None:
        state.errors += 1
        logger.warning("agent %s turn %d envelope error: %s",
                       agent.name, state.turn_idx, envelope_issue)
        state.conversation.append({"role": "assistant", "content": llm_text})
        state.conversation.append({
            "role": "user",
            "content": _envelope_retry_message(envelope_issue),
        })
        return

    if tool_name not in allow_list:
        state.errors += 1
        err = ToolNotAllowedError(
            f"Tool {tool_name!r} is not in agent {agent.name!r} allow-list: "
            f"{sorted(allow_list)}"
        )
        await _record_tool_action(
            session, agent, root_action, tool_name, tool_input, tool_reason,
            error=str(err),
        )
        state.conversation.append({"role": "assistant", "content": llm_text})
        state.conversation.append({
            "role": "user",
            "content": f"Tool {tool_name!r} is NOT in your allow-list. Retry with an allowed tool or emit done.",
        })
        return

    await _invoke_tool(
        session=session, agent=agent, root_action=root_action,
        state=state, llm_text=llm_text, tool_name=tool_name,
        tool_input=tool_input, tool_reason=tool_reason,
        require_verification_for=require_verification_for,
    )


async def _invoke_tool(
    *,
    session: AsyncSession,
    agent: AgentDefinition,
    root_action: Action,
    state: _LoopState,
    llm_text: str,
    tool_name: str,
    tool_input: dict,
    tool_reason: str,
    require_verification_for: str | None,
) -> None:
    """Call the tool with timeout + error recovery. Mutates `state`."""
    tool = get_tool_registry().get(tool_name)

    if await _reject_on_schema_violation(
        session=session, agent=agent, root_action=root_action, state=state,
        llm_text=llm_text, tool=tool, tool_input=tool_input, tool_reason=tool_reason,
    ):
        return

    state.tool_calls += 1
    try:
        result = await asyncio.wait_for(
            tool.func(session, tool_input), timeout=_TOOL_TIMEOUT_SECONDS,
        )
        assert isinstance(result, dict), f"Tool {tool_name} must return dict"
        if tool.is_mutating:
            state.mutations += 1
        if require_verification_for is not None and tool_name == require_verification_for:
            state.called_verification_tool = True
        await _record_tool_action(
            session, agent, root_action, tool_name, tool_input, tool_reason,
            result=result,
        )
        if tool.is_mutating:
            # Durability policy: every applied mutation (and its action row)
            # is committed immediately, so a later crash can't silently roll
            # back some mutations while shared helpers committed others.
            await session.commit()
        state.run_log.append({
            "turn": state.turn_idx, "tool": tool_name,
            "mutating": tool.is_mutating, "result_keys": sorted(result.keys()),
        })
        state.conversation.append({"role": "assistant", "content": llm_text})
        state.conversation.append({
            "role": "user",
            "content": f"Tool {tool_name!r} returned: {json.dumps(result, default=str)[:4000]}",
        })
    except asyncio.TimeoutError:
        state.errors += 1
        await _record_tool_action(
            session, agent, root_action, tool_name, tool_input, tool_reason,
            error=f"tool timeout after {_TOOL_TIMEOUT_SECONDS}s",
        )
        state.conversation.append({"role": "assistant", "content": llm_text})
        state.conversation.append({
            "role": "user",
            "content": f"Tool {tool_name!r} timed out. Try again, try a different tool, or emit done.",
        })
    except HTTPException as exc:
        # Shared proposal helpers raise HTTPException (404 stale finding, 400
        # bad status). That's a recoverable tool error for an agent, never a
        # reason to crash the whole run.
        state.errors += 1
        detail = str(exc.detail)[:300]
        await _record_tool_action(
            session, agent, root_action, tool_name, tool_input, tool_reason,
            error=f"HTTP {exc.status_code}: {detail}",
        )
        state.conversation.append({"role": "assistant", "content": llm_text})
        state.conversation.append({
            "role": "user",
            "content": (
                f"Tool {tool_name!r} rejected the request ({detail}). The finding may be "
                f"stale or already resolved — skip it and move on, or emit done."
            ),
        })
    except (ValueError, KeyError, AssertionError) as exc:
        state.errors += 1
        await _record_tool_action(
            session, agent, root_action, tool_name, tool_input, tool_reason,
            error=f"{type(exc).__name__}: {exc}",
        )
        state.conversation.append({"role": "assistant", "content": llm_text})
        state.conversation.append({
            "role": "user",
            "content": f"Tool {tool_name!r} failed: {type(exc).__name__}: {exc}. Try again or emit done.",
        })


async def _reject_on_schema_violation(
    *,
    session: AsyncSession,
    agent: AgentDefinition,
    root_action: Action,
    state: _LoopState,
    llm_text: str,
    tool: Tool,
    tool_input: dict,
    tool_reason: str,
) -> bool:
    """Enforce the tool's declared input schema BEFORE dispatch (required
    keys, types, string length bounds, enums) — this is what makes contract
    rules like "rationale must be 20-500 chars" real instead of decorative.
    Returns True (and records + feeds back the error) on violation."""
    schema_issue = _validate_input_schema(tool.input_schema, tool_input)
    if schema_issue is None:
        return False
    state.errors += 1
    await _record_tool_action(
        session, agent, root_action, tool.name, tool_input, tool_reason,
        error=f"input schema violation: {schema_issue}",
    )
    state.conversation.append({"role": "assistant", "content": llm_text})
    state.conversation.append({
        "role": "user",
        "content": f"Tool {tool.name!r} input invalid: {schema_issue}. Fix the input and retry.",
    })
    return True


def _feedback_parse_error(
    state: _LoopState, agent: AgentDefinition, llm_text: str, exc: AgentProtocolError,
) -> None:
    """Re-prompt the agent after a parse failure."""
    state.errors += 1
    logger.warning("agent %s turn %d parse error: %s", agent.name, state.turn_idx, exc)
    state.conversation.append({"role": "assistant", "content": llm_text})
    state.conversation.append({
        "role": "user",
        "content": (
            f"Your last response was not parseable JSON. Error: {exc}. "
            f"Reply with exactly one JSON object — either "
            f'{{"tool": "<name>", "input": {{...}}, "reason": "..."}} '
            f'or {{"done": true, "summary": "..."}}.'
        ),
    })


async def _finalize_run(
    session: AsyncSession,
    agent: AgentDefinition,
    root_action: Action,
    state: _LoopState,
    input_context: dict,
) -> AgentRunResult:
    """Derive summary, persist result, return AgentRunResult."""
    child_actions = await _fetch_child_actions(session, root_action.id)
    derived_summary = _derive_summary(
        child_actions=child_actions,
        mutations=state.mutations,
        tool_calls=state.tool_calls,
        errors=state.errors,
        auto_aborted=state.auto_aborted,
        abort_reason=state.abort_reason,
        model_self_report=state.model_self_report,
        input_context=input_context,
    )

    root_action.state = "applied"
    root_action.applied_at = datetime.utcnow()
    root_action.result_json = {
        "turns": state.turn_idx + 1,
        "tool_calls": state.tool_calls,
        "mutations": state.mutations,
        "errors": state.errors,
        "summary": derived_summary,
        "model_self_report": state.model_self_report,
        "auto_aborted": state.auto_aborted,
        "abort_reason": state.abort_reason,
        "done_rejections": state.done_rejections,
    }
    await session.commit()

    return AgentRunResult(
        agent_name=agent.name,
        root_action_id=root_action.id,
        turns=state.turn_idx + 1,
        tool_calls=state.tool_calls,
        mutations=state.mutations,
        errors=state.errors,
        summary=derived_summary,
        completed_at=datetime.utcnow(),
        run_log=state.run_log,
        model_self_report=state.model_self_report,
    )


def _build_initial_message(agent: AgentDefinition, context: dict) -> str:
    """First user message = context dict + tool catalog reminder.

    Includes a unique per-run marker derived from input_context (typically
    a job_id or artifact_id) to bust the Anthropic prompt cache so the
    model doesn't treat this call as a continuation of a prior request.
    """
    tool_registry = get_tool_registry()
    catalog_lines = []
    for tool_name in agent.tool_allow_list:
        tool = tool_registry.get(tool_name)
        catalog_lines.append(f"- {tool.name}: {tool.description}")
    catalog = "\n".join(catalog_lines)
    ctx_json = json.dumps(context, default=str, indent=2) if context else "(none)"
    # Pick the most specific identifier available for the cache-bust marker.
    marker_bits = [
        f"{k}={v}" for k, v in context.items()
        if k in ("artifact_id", "job_id", "proposal_id", "finding_id")
    ]
    marker = " ".join(marker_bits) if marker_bits else "(no id)"
    return (
        f"=== STANDALONE REQUEST — not a continuation ({marker}) ===\n\n"
        f"Context for this run:\n{ctx_json}\n\n"
        f"Tools available to you:\n{catalog}\n\n"
        f"Reply with exactly one JSON object per turn — either a tool call or a done envelope."
    )


async def _llm_turn(agent: AgentDefinition, conversation: list[dict]) -> str:
    """One LLM call. Folds conversation history into a single user message.

    Per-agent reasoning effort (agent YAML `effort:`) is applied via the
    effort ContextVar for exactly this call, then restored — agent runs must
    not inherit or leak a request-level effort.
    """
    transcript_parts = []
    for msg in conversation:
        role_tag = "USER" if msg["role"] == "user" else "ASSISTANT"
        transcript_parts.append(f"[{role_tag}]\n{msg['content']}")
    user_message = "\n\n".join(transcript_parts)
    token = effort_var.set(agent.effort) if agent.effort else None
    try:
        result = await llm_chat(
            system_prompt=agent.system_prompt,
            user_message=user_message,
            max_tokens=agent.max_tokens,
            model=agent.model,
        )
    finally:
        if token is not None:
            effort_var.reset(token)
    return str(result.get("text", "")).strip()


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_envelope(text: str) -> dict:
    """Extract a single JSON object from the LLM response text.

    Models sometimes emit SEVERAL envelopes in one turn (batching tool calls
    despite the one-per-turn protocol — observed live with opus). Rather than
    failing the whole turn, take the FIRST complete object and execute it;
    the conversation feedback shows the model its result, so it naturally
    re-issues the rest one at a time.
    """
    text = text.strip()
    if not text:
        raise AgentProtocolError("empty LLM response")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        raise AgentProtocolError(f"no JSON object found in response: {text[:200]!r}")
    candidate = match.group(0)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Greedy span failed — likely multiple objects. Balanced-decode the
        # first one starting at the span's opening brace (JPL-2: bounded by
        # raw_decode's single pass).
        try:
            first, _end = json.JSONDecoder().raw_decode(candidate)
        except json.JSONDecodeError as exc:
            raise AgentProtocolError(f"JSON parse failed: {exc}") from exc
        if not isinstance(first, dict):
            raise AgentProtocolError(f"first JSON value is not an object: {type(first).__name__}")
        return first


def _validate_input_schema(schema: dict, tool_input: dict) -> str | None:
    """Minimal JSON-Schema enforcement: required keys, primitive types, string
    length bounds, enums. Returns an issue string or None if valid.

    Deliberately not a full validator — it enforces exactly the constraint
    kinds our tool schemas declare, so rules like "rationale must be 20-500
    chars" are enforced at dispatch instead of being decorative.
    """
    assert isinstance(tool_input, dict), "tool_input must be a dict"
    if not isinstance(schema, dict):
        return None
    for key in schema.get("required") or []:
        if key not in tool_input:
            return f"missing required field {key!r}"
    for key, spec in (schema.get("properties") or {}).items():
        if key not in tool_input or not isinstance(spec, dict):
            continue
        val = tool_input[key]
        expected = spec.get("type")
        if expected == "string":
            if not isinstance(val, str):
                return f"{key!r} must be a string"
            if "minLength" in spec and len(val) < spec["minLength"]:
                return f"{key!r} must be at least {spec['minLength']} chars (got {len(val)})"
            if "maxLength" in spec and len(val) > spec["maxLength"]:
                return f"{key!r} must be at most {spec['maxLength']} chars (got {len(val)})"
        elif expected == "integer":
            if isinstance(val, bool) or not isinstance(val, int):
                return f"{key!r} must be an integer"
        elif expected == "number":
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                return f"{key!r} must be a number"
        elif expected == "boolean":
            if not isinstance(val, bool):
                return f"{key!r} must be a boolean"
        if "enum" in spec and val not in spec["enum"]:
            return f"{key!r} must be one of {spec['enum']}"
    return None


def _validate_envelope_fields(tool_name: object, tool_input: object) -> str | None:
    """Return a human-readable issue string, or None if envelope fields are OK."""
    if not (isinstance(tool_name, str) and tool_name):
        return (
            "envelope missing required 'tool' string "
            "(or 'done': true for completion)"
        )
    if not isinstance(tool_input, dict):
        return "'input' must be a JSON object"
    return None


def _envelope_retry_message(issue: str) -> str:
    """Build the retry prompt fed back to the LLM on an invalid envelope."""
    return (
        f"Your last envelope was invalid: {issue}. "
        f'Reply with exactly one JSON object — either '
        f'{{"tool": "<name>", "input": {{...}}, "reason": "..."}} '
        f'or {{"done": true, "summary": "..."}}.'
    )


async def _record_tool_action(
    session: AsyncSession,
    agent: AgentDefinition,
    root_action: Action,
    tool_name: str,
    tool_input: dict,
    reasoning: str,
    result: dict | None = None,
    error: str | None = None,
) -> None:
    """Record one tool invocation as a child Action on the Action Bus."""
    state = "applied" if error is None else "failed"
    child = Action(
        kind=f"agent.tool.{tool_name}",
        actor_type="agent",
        actor_id=agent.name,
        input_json=tool_input,
        reason=reasoning or None,
        parent_action_id=root_action.id,
        state=state,
        applied_at=datetime.utcnow() if state == "applied" else None,
        result_json=result,
        error_message=error,
    )
    session.add(child)
    await session.flush()


# ── Guardrail helpers (Layer 1, Layer 2, Layer 3) ────────────────────────


def _evaluate_done_envelope(
    *,
    mutations: int,
    expected_mutations: int | None,
    called_verification_tool: bool,
    require_verification_for: str | None,
) -> str | None:
    """Return the rejection message to send back to the agent, or None to accept
    the `{done}` envelope. Rejection enforces Guardrail 1 (mutation count) and
    Layer 2 (mandatory verification tool)."""
    if expected_mutations is not None and mutations < expected_mutations:
        return (
            f"You emitted 'done' but have not committed the expected mutation. "
            f"mutations={mutations}, expected={expected_mutations}. "
            f"Call the appropriate mutating tool now (e.g. "
            f"refine_ingest_classification or flag_ingest_uncertain for a "
            f"placement run) before emitting done."
        )
    if require_verification_for is not None and not called_verification_tool:
        return (
            f"You emitted 'done' but have not called the required "
            f"verification tool {require_verification_for!r}. Call it first "
            f"to read the post-mutation state before emitting done."
        )
    return None


async def _fetch_child_actions(
    session: AsyncSession, root_action_id: int,
) -> list[Action]:
    """Return all child Action rows for a given root agent run, ordered by id."""
    from sqlalchemy import select  # noqa: PLC0415 — local import keeps module top clean

    stmt = (
        select(Action)
        .where(Action.parent_action_id == root_action_id)
        .order_by(Action.id.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


def _derive_summary(
    *,
    child_actions: list[Action],
    mutations: int,
    tool_calls: int,
    errors: int,
    auto_aborted: bool,
    abort_reason: str = "",
    model_self_report: str,
    input_context: dict,
) -> str:
    """Layer 3: compute an authoritative summary from the child Action trail.

    The model's self-report is preserved as evidence but not treated as
    truth; this function walks the observed mutations and composes a
    summary that matches what was actually committed."""
    if auto_aborted:
        if abort_reason:
            return (
                f"Run aborted: {abort_reason}. mutations={mutations}, "
                f"tool_calls={tool_calls}, errors={errors}."
            )
        artifact_id = input_context.get("artifact_id", "?")
        return (
            f"Run auto-aborted: agent emitted 'done' without committing the "
            f"expected mutation after 2 retries. Artifact #{artifact_id} "
            f"remains for human review. Model self-report: "
            f"{model_self_report[:200]!r}"
        )

    mutation_actions = [
        a for a in child_actions
        if a.state == "applied" and a.kind.startswith("agent.tool.")
        and not _tool_is_readonly(a.kind)
    ]
    if not mutation_actions:
        return (
            f"No mutations committed. tool_calls={tool_calls}, errors={errors}. "
            f"Model self-report: {model_self_report[:200]!r}"
        )

    parts: list[str] = []
    # JPL-2: bounded by mutation_actions length, which is bounded by max_turns.
    for a in mutation_actions:
        tool_name = a.kind.removeprefix("agent.tool.")
        line = _describe_mutation(tool_name, a.input_json or {}, a.result_json or {})
        if line:
            parts.append(line)
    if parts:
        return "; ".join(parts)
    return (
        f"{mutations} mutation(s) committed but could not derive human-readable "
        f"summary. Model self-report: {model_self_report[:200]!r}"
    )


_READONLY_TOOL_NAMES = frozenset({
    "list_pending_ingest_proposals",
    "get_ingest_proposal_detail",
    "search_graph_parents",
    "get_placement_status",
    "list_pending_duplicates",
    "get_finding_detail",
    "compute_embedding_similarity",
    "compare_neurons_semantic",
    "list_pending_contradictions",
    "get_contradiction_detail",
})


def _tool_is_readonly(kind: str) -> bool:
    """Best-effort classification for summary derivation — the tool registry
    is the authority, but we don't have a handle to it here and read-only
    tools never produce mutations anyway."""
    tool_name = kind.removeprefix("agent.tool.")
    return tool_name in _READONLY_TOOL_NAMES


def _describe_mutation(tool_name: str, inp: dict, out: dict) -> str:
    """Produce one human-readable line per mutation tool call."""
    if tool_name == "refine_ingest_classification":
        pid = inp.get("proposal_id") or out.get("proposal_id")
        conf = out.get("confidence")
        promoted = out.get("promoted")
        updates = inp.get("item_updates") or []
        head = updates[0] if updates else {}
        parent = head.get("parent_id")
        layer = head.get("layer")
        dept = head.get("department")
        role = head.get("role_key")
        bits = [f"Placed proposal #{pid}"]
        if parent is not None:
            bits.append(f"→ parent #{parent}")
        if layer is not None:
            bits.append(f"(layer {layer}, department={dept}, role_key={role})")
        if conf is not None:
            bits.append(f"confidence={conf}")
        if promoted:
            bits.append("state=artifact→proposed")
        return " ".join(bits)
    if tool_name == "flag_ingest_uncertain":
        pid = inp.get("proposal_id") or out.get("proposal_id")
        cc = out.get("candidate_count") or len(inp.get("candidates", []))
        return f"Flagged proposal #{pid} uncertain with {cc} candidate placement(s)"
    if tool_name == "mark_duplicate":
        pid = inp.get("finding_id") or out.get("finding_id")
        return f"Marked duplicate finding #{pid}"
    if tool_name == "mark_reviewed_as_unique":
        pid = inp.get("finding_id") or out.get("finding_id")
        res = inp.get("resolution") or out.get("resolution") or "unique"
        return f"Reviewed finding #{pid} as {res}"
    if tool_name == "propose_contradiction_resolution":
        pid = inp.get("finding_id") or out.get("finding_id")
        res = inp.get("resolution") or out.get("resolution") or "resolved"
        return f"Resolved contradiction #{pid} as {res}"
    if tool_name == "dismiss_contradiction":
        pid = inp.get("finding_id") or out.get("finding_id")
        return f"Dismissed contradiction #{pid}"
    # Fallback
    return f"Mutation via {tool_name}"
