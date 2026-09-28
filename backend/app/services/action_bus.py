"""Action bus — universal write primitive (AIP governance pattern #1).

Every state mutation in Corvus passes through this bus. Each action is:
  - typed: registered with a name + Pydantic input schema
  - validated: input parsed/checked before any DB write
  - audited: persisted as an Action row with actor, payload, outcome
  - optionally gated: requires_approval=True actions stop in "pending" state
    until a reviewer calls approve()

Step 1 of the roadmap wires up the bus and migrates `eval.score.set` as
the first proof. Subsequent steps migrate remaining write paths.

Reference: ~/.claude/plans/staged-booping-globe.md, pattern #1.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.rbac import UserIdentity
from app.models import Action

logger = logging.getLogger(__name__)


# ── Types ──

# A handler receives the validated input + caller identity + an active DB session
# (the bus opens a SAVEPOINT around it). It returns a dict with two keys:
#   - "audit": small JSON-safe dict captured into Action.result_json
#   - "payload": opaque return value handed back to the caller
HandlerResult = dict[str, Any]
HandlerFn = Callable[
    [BaseModel, UserIdentity, AsyncSession, Action],
    Awaitable[HandlerResult],
]


@dataclass(frozen=True)
class ActionRegistration:
    """Static metadata for one registered action kind."""

    kind: str
    schema: type[BaseModel]
    handler: HandlerFn
    requires_approval: bool = False


@dataclass
class ActionResult:
    """Outcome of a submit/approve call."""

    action_id: int
    state: str  # "applied" | "pending" | "rejected" | "failed"
    payload: Any = None
    audit: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


# ── Registry (class-based to avoid module-level mutable global / JPL-6) ──

class _ActionRegistry:
    """Registry of action kinds. Single instance lives below."""

    def __init__(self) -> None:
        self._handlers: dict[str, ActionRegistration] = {}

    def register(
        self,
        kind: str,
        schema: type[BaseModel],
        handler: HandlerFn,
        requires_approval: bool = False,
    ) -> None:
        assert kind not in self._handlers, f"action kind already registered: {kind!r}"
        assert "." in kind, f"action kind must be namespaced (e.g. 'eval.score.set'): {kind!r}"
        self._handlers[kind] = ActionRegistration(
            kind=kind,
            schema=schema,
            handler=handler,
            requires_approval=requires_approval,
        )

    def get(self, kind: str) -> ActionRegistration:
        reg = self._handlers.get(kind)
        if reg is None:
            raise KeyError(f"unregistered action kind: {kind!r}")
        return reg

_action_registry = _ActionRegistry()


def register_action(
    kind: str,
    schema: type[BaseModel],
    handler: HandlerFn,
    requires_approval: bool = False,
) -> None:
    """Register an action kind. Called once at startup from init_registry."""
    _action_registry.register(kind, schema, handler, requires_approval)


# ── Public submit API ──

async def _check_idempotency(
    db: AsyncSession, idempotency_key: str | None,
) -> ActionResult | None:
    """Return the prior ActionResult if this idempotency key was already submitted."""
    if idempotency_key is None:
        return None
    existing = await db.execute(
        select(Action).where(Action.idempotency_key == idempotency_key)
    )
    prior = existing.scalar_one_or_none()
    if prior is None:
        return None
    return ActionResult(
        action_id=prior.id,
        state=prior.state,
        payload=None,
        audit=prior.result_json or {},
        error=prior.error_message,
    )


async def submit(
    db: AsyncSession,
    kind: str,
    actor: UserIdentity,
    input_data: dict[str, Any],
    *,
    actor_type: str = "user",
    reason: str | None = None,
    source_query_id: int | None = None,
    source_proposal_id: int | None = None,
    parent_action_id: int | None = None,
    idempotency_key: str | None = None,
) -> ActionResult:
    """Submit an action for execution.

    Validates the input against the registered schema, persists an Action row,
    runs the handler inside a SAVEPOINT (so a handler exception rolls back only
    the handler's writes, not the audit row), and returns an ActionResult.

    The caller is responsible for committing the outer transaction.
    """
    assert actor_type in ("user", "autopilot", "system", "external_agent"), (
        f"unknown actor_type: {actor_type!r}"
    )
    reg = _action_registry.get(kind)

    prior_result = await _check_idempotency(db, idempotency_key)
    if prior_result is not None:
        return prior_result

    try:
        validated = reg.schema.model_validate(input_data)
    except ValidationError as exc:
        raise ValueError(f"action {kind!r} input validation failed: {exc}") from exc

    action_row = Action(
        kind=kind,
        actor_type=actor_type,
        actor_id=actor.user_id,
        input_json=validated.model_dump(mode="json"),
        reason=reason,
        source_query_id=source_query_id,
        source_proposal_id=source_proposal_id,
        parent_action_id=parent_action_id,
        requires_approval=reg.requires_approval,
        state="pending",
        idempotency_key=idempotency_key,
    )
    db.add(action_row)
    await db.flush()  # assign action_row.id

    if reg.requires_approval:
        return ActionResult(
            action_id=action_row.id,
            state="pending",
            payload=None,
            audit={},
        )

    return await _run_handler(db, reg, validated, actor, action_row)


async def approve(
    db: AsyncSession,
    action_id: int,
    reviewer: UserIdentity,
    notes: str | None = None,
) -> ActionResult:
    """Approve a pending action and run its handler."""
    action_row = await db.get(Action, action_id)
    if action_row is None:
        raise KeyError(f"action {action_id} not found")
    if action_row.state != "pending":
        raise ValueError(
            f"action {action_id} is in state {action_row.state!r}, not pending"
        )

    reg = _action_registry.get(action_row.kind)
    validated = reg.schema.model_validate(action_row.input_json)

    action_row.reviewed_by = reviewer.user_id
    action_row.reviewed_at = datetime.datetime.utcnow()
    action_row.review_notes = notes
    await db.flush()

    return await _run_handler(db, reg, validated, reviewer, action_row)


async def reject(
    db: AsyncSession,
    action_id: int,
    reviewer: UserIdentity,
    notes: str | None = None,
) -> ActionResult:
    """Reject a pending action without running it."""
    action_row = await db.get(Action, action_id)
    if action_row is None:
        raise KeyError(f"action {action_id} not found")
    if action_row.state != "pending":
        raise ValueError(
            f"action {action_id} is in state {action_row.state!r}, not pending"
        )

    action_row.state = "rejected"
    action_row.reviewed_by = reviewer.user_id
    action_row.reviewed_at = datetime.datetime.utcnow()
    action_row.review_notes = notes
    await db.flush()

    return ActionResult(
        action_id=action_row.id,
        state="rejected",
        payload=None,
        audit={},
    )


# ── Internal: handler invocation with savepoint ──

async def _run_handler(
    db: AsyncSession,
    reg: ActionRegistration,
    validated: BaseModel,
    actor: UserIdentity,
    action_row: Action,
) -> ActionResult:
    """Run a handler inside a SAVEPOINT and update the action row with the outcome."""
    try:
        async with db.begin_nested():
            handler_out = await reg.handler(validated, actor, db, action_row)
        assert isinstance(handler_out, dict), (
            f"action {reg.kind!r} handler must return a dict, got {type(handler_out)}"
        )
        audit = handler_out.get("audit") or {}
        payload = handler_out.get("payload")

        action_row.state = "applied"
        action_row.applied_at = datetime.datetime.utcnow()
        action_row.result_json = audit
        await db.flush()

        return ActionResult(
            action_id=action_row.id,
            state="applied",
            payload=payload,
            audit=audit,
        )
    except Exception as exc:
        # Savepoint rolled back; record failure on the audit row.
        logger.exception("action %s (%s) handler failed", action_row.id, reg.kind)
        action_row.state = "failed"
        action_row.error_message = f"{type(exc).__name__}: {exc}"
        await db.flush()
        return ActionResult(
            action_id=action_row.id,
            state="failed",
            payload=None,
            audit={},
            error=action_row.error_message,
        )
