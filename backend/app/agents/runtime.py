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

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.registry import AgentDefinition, get_agent_registry
from app.agents.tool_base import Tool, get_tool_registry
from app.models import Action
from app.services.llm_provider import llm_chat


logger = logging.getLogger(__name__)


_TOOL_TIMEOUT_SECONDS = 60
_MAX_TURNS_HARD_CAP = 20  # safety bound regardless of YAML


class ToolNotAllowedError(Exception):
    """LLM requested a tool outside the agent's allow-list."""


class AgentProtocolError(Exception):
    """LLM response was not parseable as tool-call or done envelope."""


@dataclass
class AgentRunResult:
    """Summary of a completed agent run."""

    agent_name: str
    root_action_id: int
    turns: int
    tool_calls: int
    mutations: int
    errors: int
    summary: str
    completed_at: datetime
    run_log: list[dict] = field(default_factory=list)


async def execute_agent(
    session: AsyncSession,
    agent_name: str,
    input_context: dict | None = None,
    triggered_by: str = "system",
) -> AgentRunResult:
    """Execute one agent turn-loop to completion or cap. Returns a summary.

    Args:
        session: DB session (open, will be committed as the agent records Actions).
        agent_name: key in AgentRegistry.
        input_context: optional dict merged into the agent's initial user message.
        triggered_by: identity string — recorded on the root Action.

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

    root_action = Action(
        kind="agent.run",
        actor_type="agent",
        actor_id=agent.name,
        input_json={
            "agent": agent.name,
            "role": agent.role,
            "model": agent.model,
            "context": input_context,
            "triggered_by": triggered_by,
        },
        reason=f"Agent run started by {triggered_by}",
        state="pending",
    )
    session.add(root_action)
    await session.flush()
    assert root_action.id is not None, "root_action must have id after flush"

    tool_registry = get_tool_registry()
    allow_list = set(agent.tool_allow_list)

    conversation: list[dict] = [
        {"role": "user", "content": _build_initial_message(agent, input_context)}
    ]
    run_log: list[dict] = []
    tool_calls = 0
    mutations = 0
    errors = 0
    final_summary = "(no summary — hit turn cap)"

    for turn_idx in range(max_turns):
        llm_text = await _llm_turn(agent, conversation)
        run_log.append({"turn": turn_idx, "llm_text_preview": llm_text[:500]})

        try:
            envelope = _parse_envelope(llm_text)
        except AgentProtocolError as exc:
            errors += 1
            logger.warning("agent %s turn %d parse error: %s", agent.name, turn_idx, exc)
            conversation.append({"role": "assistant", "content": llm_text})
            conversation.append({
                "role": "user",
                "content": (
                    f"Your last response was not parseable JSON. "
                    f"Error: {exc}. "
                    f"Reply with exactly one JSON object — either "
                    f'{{"tool": "<name>", "input": {{...}}, "reason": "..."}} '
                    f'or {{"done": true, "summary": "..."}}.'
                ),
            })
            continue

        if envelope.get("done") is True:
            final_summary = str(envelope.get("summary", "")).strip() or "(empty summary)"
            break

        tool_name = envelope.get("tool")
        tool_input = envelope.get("input", {}) or {}
        tool_reason = str(envelope.get("reason", "")).strip()

        # Recoverable protocol errors — feed back to LLM rather than crash.
        envelope_issue = _validate_envelope_fields(tool_name, tool_input)
        if envelope_issue is not None:
            errors += 1
            logger.warning(
                "agent %s turn %d envelope error: %s",
                agent.name, turn_idx, envelope_issue,
            )
            conversation.append({"role": "assistant", "content": llm_text})
            conversation.append({
                "role": "user",
                "content": _envelope_retry_message(envelope_issue),
            })
            continue

        if tool_name not in allow_list:
            errors += 1
            err = ToolNotAllowedError(
                f"Tool {tool_name!r} is not in agent {agent.name!r} allow-list: "
                f"{sorted(allow_list)}"
            )
            await _record_tool_action(
                session, agent, root_action, tool_name, tool_input, tool_reason,
                error=str(err),
            )
            conversation.append({"role": "assistant", "content": llm_text})
            conversation.append({
                "role": "user",
                "content": f"Tool {tool_name!r} is NOT in your allow-list. Retry with an allowed tool or emit done.",
            })
            continue

        tool = tool_registry.get(tool_name)
        tool_calls += 1
        try:
            result = await asyncio.wait_for(
                tool.func(session, tool_input), timeout=_TOOL_TIMEOUT_SECONDS,
            )
            assert isinstance(result, dict), f"Tool {tool_name} must return dict"
            if tool.is_mutating:
                mutations += 1
            await _record_tool_action(
                session, agent, root_action, tool_name, tool_input, tool_reason,
                result=result,
            )
            run_log.append({
                "turn": turn_idx, "tool": tool_name, "mutating": tool.is_mutating,
                "result_keys": sorted(result.keys()),
            })
            conversation.append({"role": "assistant", "content": llm_text})
            conversation.append({
                "role": "user",
                "content": f"Tool {tool_name!r} returned: {json.dumps(result, default=str)[:4000]}",
            })
        except asyncio.TimeoutError:
            errors += 1
            await _record_tool_action(
                session, agent, root_action, tool_name, tool_input, tool_reason,
                error=f"tool timeout after {_TOOL_TIMEOUT_SECONDS}s",
            )
            conversation.append({
                "role": "user",
                "content": f"Tool {tool_name!r} timed out. Try again, try a different tool, or emit done.",
            })
        except (ValueError, KeyError, AssertionError) as exc:
            errors += 1
            await _record_tool_action(
                session, agent, root_action, tool_name, tool_input, tool_reason,
                error=f"{type(exc).__name__}: {exc}",
            )
            conversation.append({
                "role": "user",
                "content": f"Tool {tool_name!r} failed: {type(exc).__name__}: {exc}. Try again or emit done.",
            })

    root_action.state = "applied"
    root_action.applied_at = datetime.utcnow()
    root_action.result_json = {
        "turns": turn_idx + 1,
        "tool_calls": tool_calls,
        "mutations": mutations,
        "errors": errors,
        "summary": final_summary,
    }
    await session.commit()

    return AgentRunResult(
        agent_name=agent.name,
        root_action_id=root_action.id,
        turns=turn_idx + 1,
        tool_calls=tool_calls,
        mutations=mutations,
        errors=errors,
        summary=final_summary,
        completed_at=datetime.utcnow(),
        run_log=run_log,
    )


def _build_initial_message(agent: AgentDefinition, context: dict) -> str:
    """First user message = context dict + tool catalog reminder."""
    tool_registry = get_tool_registry()
    catalog_lines = []
    for tool_name in agent.tool_allow_list:
        tool = tool_registry.get(tool_name)
        catalog_lines.append(f"- {tool.name}: {tool.description}")
    catalog = "\n".join(catalog_lines)
    ctx_json = json.dumps(context, default=str, indent=2) if context else "(none)"
    return (
        f"Context for this run:\n{ctx_json}\n\n"
        f"Tools available to you:\n{catalog}\n\n"
        f"Reply with exactly one JSON object per turn — either a tool call or a done envelope."
    )


async def _llm_turn(agent: AgentDefinition, conversation: list[dict]) -> str:
    """One LLM call. Folds conversation history into a single user message."""
    transcript_parts = []
    for msg in conversation:
        role_tag = "USER" if msg["role"] == "user" else "ASSISTANT"
        transcript_parts.append(f"[{role_tag}]\n{msg['content']}")
    user_message = "\n\n".join(transcript_parts)
    result = await llm_chat(
        system_prompt=agent.system_prompt,
        user_message=user_message,
        max_tokens=agent.max_tokens,
        model=agent.model,
    )
    return str(result.get("text", "")).strip()


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_envelope(text: str) -> dict:
    """Extract a single JSON object from the LLM response text."""
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
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise AgentProtocolError(f"JSON parse failed: {exc}") from exc


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
