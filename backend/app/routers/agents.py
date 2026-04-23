"""Customer-facing /v1/agents/* surface (AIP Pattern #208 A-UI).

Exposes read-only listing and run-history for Phase 4 agents, plus an
admin-gated trigger endpoint. All writes go through the Action Bus, so
this router never touches neurons directly.

Endpoints:
  GET  /v1/agents                        — list registered agents
  GET  /v1/agents/{name}                 — get agent definition
  GET  /v1/agents/runs                   — list recent agent runs
  GET  /v1/agents/runs/{action_id}       — get one run with child actions
  POST /v1/agents/{name}/run             — trigger an agent run (admin)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import execute_agent, get_agent_registry
from app.database import get_db
from app.middleware.rbac import UserIdentity, require_role
from app.models import Action


router = APIRouter(prefix="/v1/agents", tags=["agents"])


# ── Response schemas ──


class AgentSummaryOut(BaseModel):
    name: str
    role: str
    description: str
    model: str
    max_turns: int
    tool_count: int
    manual_trigger: bool
    schedule_enabled: bool
    # Plain-English explanation for the admin Knowledge → Agents page.
    # Empty when the YAML doesn't define it; the frontend falls back to
    # `description` in that case.
    admin_description: str = ""


class AgentDetailOut(AgentSummaryOut):
    tool_allow_list: list[str]
    system_prompt_preview: str = Field(description="First 500 chars of system prompt")


class AgentRunOut(BaseModel):
    action_id: int
    agent_name: str
    triggered_by: str
    started_at: str | None
    completed_at: str | None
    state: str
    turns: int | None = None
    tool_calls: int | None = None
    mutations: int | None = None
    errors: int | None = None
    summary: str | None = None


class AgentRunDetailOut(AgentRunOut):
    child_actions: list[dict[str, Any]]


class AgentRunTriggerIn(BaseModel):
    input_context: dict[str, Any] | None = None


class AgentRunTriggerOut(BaseModel):
    action_id: int
    agent_name: str
    summary: str
    turns: int
    tool_calls: int
    mutations: int
    errors: int


# ── Endpoints ──


@router.get("", response_model=list[AgentSummaryOut])
async def list_agents(
    _user: UserIdentity = Depends(require_role("reader")),
) -> list[AgentSummaryOut]:
    """List all registered agents. Role: reader."""
    registry = get_agent_registry()
    return [
        AgentSummaryOut(
            name=a.name,
            role=a.role,
            description=a.description,
            model=a.model,
            max_turns=a.max_turns,
            tool_count=len(a.tool_allow_list),
            manual_trigger=a.trigger.manual,
            schedule_enabled=a.trigger.schedule_enabled,
            admin_description=a.admin_description,
        )
        for a in registry.list_agents()
    ]


@router.get("/runs", response_model=list[AgentRunOut])
async def list_agent_runs(
    db: AsyncSession = Depends(get_db),
    agent_name: str | None = None,
    limit: int = 50,
    _user: UserIdentity = Depends(require_role("reader")),
) -> list[AgentRunOut]:
    """List recent agent runs. Filter by agent_name optional. Role: reader."""
    assert 1 <= limit <= 500, "limit must be 1..500"
    clauses = [Action.kind == "agent.run"]
    if agent_name:
        clauses.append(Action.actor_id == agent_name)
    stmt = (
        select(Action)
        .where(and_(*clauses))
        .order_by(desc(Action.id))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [_action_to_run(row) for row in rows]


@router.get("/runs/{action_id}", response_model=AgentRunDetailOut)
async def get_agent_run(
    action_id: int,
    db: AsyncSession = Depends(get_db),
    _user: UserIdentity = Depends(require_role("reader")),
) -> AgentRunDetailOut:
    """Get one agent run with all child tool-call actions. Role: reader."""
    root = await db.get(Action, action_id)
    if root is None or root.kind != "agent.run":
        raise HTTPException(404, f"Agent run {action_id} not found")

    child_stmt = (
        select(Action)
        .where(Action.parent_action_id == action_id)
        .order_by(Action.id.asc())
    )
    children = (await db.execute(child_stmt)).scalars().all()
    child_list = [
        {
            "action_id": c.id,
            "kind": c.kind,
            "tool": c.kind.removeprefix("agent.tool."),
            "input": c.input_json,
            "reason": c.reason,
            "state": c.state,
            "result": c.result_json,
            "error": c.error_message,
            "applied_at": c.applied_at.isoformat() if c.applied_at else None,
        }
        for c in children
    ]
    run = _action_to_run(root)
    return AgentRunDetailOut(**run.model_dump(), child_actions=child_list)


@router.get("/{name}", response_model=AgentDetailOut)
async def get_agent(
    name: str,
    _user: UserIdentity = Depends(require_role("reader")),
) -> AgentDetailOut:
    """Get the definition of one agent. Role: reader."""
    registry = get_agent_registry()
    if not registry.has(name):
        raise HTTPException(404, f"Agent {name!r} not found")
    a = registry.get(name)
    return AgentDetailOut(
        name=a.name,
        role=a.role,
        description=a.description,
        model=a.model,
        max_turns=a.max_turns,
        tool_count=len(a.tool_allow_list),
        manual_trigger=a.trigger.manual,
        schedule_enabled=a.trigger.schedule_enabled,
        admin_description=a.admin_description,
        tool_allow_list=list(a.tool_allow_list),
        system_prompt_preview=a.system_prompt[:500],
    )


@router.post("/{name}/run", response_model=AgentRunTriggerOut)
async def trigger_agent_run(
    name: str,
    body: AgentRunTriggerIn,
    db: AsyncSession = Depends(get_db),
    user: UserIdentity = Depends(require_role("admin")),
) -> AgentRunTriggerOut:
    """Trigger a manual agent run. Role: admin."""
    registry = get_agent_registry()
    if not registry.has(name):
        raise HTTPException(404, f"Agent {name!r} not found")
    agent = registry.get(name)
    if not agent.trigger.manual:
        raise HTTPException(400, f"Agent {name!r} does not allow manual triggering")

    result = await execute_agent(
        session=db,
        agent_name=name,
        input_context=body.input_context or {},
        triggered_by=f"user:{user.user_id}",
    )
    return AgentRunTriggerOut(
        action_id=result.root_action_id,
        agent_name=result.agent_name,
        summary=result.summary,
        turns=result.turns,
        tool_calls=result.tool_calls,
        mutations=result.mutations,
        errors=result.errors,
    )


# ── Helpers ──


def _action_to_run(action: Action) -> AgentRunOut:
    """Project an Action row onto the AgentRunOut shape."""
    result = action.result_json or {}
    return AgentRunOut(
        action_id=action.id,
        agent_name=action.actor_id or "unknown",
        triggered_by=str((action.input_json or {}).get("triggered_by", "unknown")),
        started_at=action.applied_at.isoformat() if action.applied_at else None,
        completed_at=action.applied_at.isoformat() if action.applied_at else None,
        state=action.state,
        turns=result.get("turns"),
        tool_calls=result.get("tool_calls"),
        mutations=result.get("mutations"),
        errors=result.get("errors"),
        summary=result.get("summary"),
    )
