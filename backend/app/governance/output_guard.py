"""Output policy gate — runs tenant-configured policies against an LLM response.

Wired between the LLM call and response persistence in routers that serve
AI-generated text (``/query``, ``/v1/query``). Each enabled policy emits
zero or more :class:`ViolationDraft`s; the guard:

1. Applies the worst per-rule action to the response text (redactions
   mutate in place; a single ``block`` short-circuits).
2. Persists one :class:`OutputViolation` row per draft, linked to a
   single ``output.policy.check`` action-bus entry so the whole check
   has a root audit row.

Pattern #7 — Runtime output policy gates (Phase 1.5 GTM gate).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.governance.policies import (
    POLICY_CLASSES,
    PolicyContext,
    ViolationDraft,
)
from app.middleware.rbac import UserIdentity
from app.models import NeuronFiring, OutputViolation, Query
from app.schemas import OutputViolationOut
from app.services import action_bus
from app.tenant import tenant


# Bound the evaluation loop; tenants cannot define more than this many
# policies (registry is static at ~5, giving a 5x ceiling). JPL-2.
_MAX_POLICIES = 32


@dataclass
class GuardResult:
    """Outcome of one ``run_guards`` call.

    Attributes:
        final_text:  response after redactions (equals ``response_text``
                     if nothing was redacted).
        blocked:     True if any violation had ``action="block"``.
        violations:  persisted :class:`OutputViolation` rows (empty when
                     every policy was clean or disabled).
        action_id:   action_bus row id for the ``output.policy.check``
                     audit entry (``None`` when no policies ran).
    """

    final_text: str
    blocked: bool
    violations: list[OutputViolation]
    action_id: int | None


def _collect_drafts(
    response_text: str,
    firings: Sequence[Any],
    policy_cfg: dict,
) -> list[ViolationDraft]:
    """Run every enabled policy and return the combined draft list."""
    drafts: list[ViolationDraft] = []
    policies_run = 0
    for policy_cls in POLICY_CLASSES:
        assert policies_run < _MAX_POLICIES, "policy loop unbounded"  # JPL-2
        policies_run += 1
        cfg = policy_cfg.get(policy_cls.rule_id) or {}
        if not cfg.get("enabled", False):
            continue
        policy = policy_cls(cfg)
        ctx = PolicyContext(
            response_text=response_text,
            firings=list(firings),
            tenant_policy=cfg,
        )
        drafts.extend(policy.check(ctx))
    return drafts


def _apply_redactions(text: str, drafts: Sequence[ViolationDraft]) -> str:
    """Apply every ``redact`` draft to ``text``. Replacements are literal."""
    out = text
    for draft in drafts:
        if draft.action != "redact":
            continue
        if not draft.matched_span or draft.redaction is None:
            continue
        out = out.replace(draft.matched_span, draft.redaction)
    return out


async def _log_action(
    db: AsyncSession,
    actor: UserIdentity,
    query_id: int | None,
    drafts: Sequence[ViolationDraft],
    blocked: bool,
    redacted: bool,
) -> int | None:
    """Submit a single ``output.policy.check`` action and return its id."""
    input_data = {
        "query_id": query_id,
        "rule_count": len(drafts),
        "blocked": blocked,
        "redacted": redacted,
        "rules": sorted({d.rule_id for d in drafts}),
    }
    result = await action_bus.submit(
        db=db,
        kind="output.policy.check",
        actor=actor,
        actor_type="system",
        input_data=input_data,
        source_query_id=query_id,
        reason="runtime output policy gate",
    )
    return result.action_id


def _persist_violations(
    db: AsyncSession,
    query_id: int | None,
    action_id: int | None,
    drafts: Sequence[ViolationDraft],
) -> list[OutputViolation]:
    """Create one ``OutputViolation`` row per draft. Caller flushes."""
    rows: list[OutputViolation] = []
    for draft in drafts:
        row = OutputViolation(
            query_id=query_id,
            rule_id=draft.rule_id,
            severity=draft.severity,
            action=draft.action,
            matched_span=draft.matched_span,
            redaction=draft.redaction,
            detail=draft.detail or None,
            action_id=action_id,
        )
        db.add(row)
        rows.append(row)
    return rows


async def run_guards(
    db: AsyncSession,
    *,
    query_id: int | None,
    response_text: str,
    firings: Sequence[Any],
    actor: UserIdentity,
) -> GuardResult:
    """Run all enabled output policies. Persist violations + log an action.

    Args:
        db:             active session; caller owns the outer transaction.
        query_id:       Query.id if this guard runs for a persisted query,
                        else ``None`` (e.g., /context-only paths).
        response_text:  raw LLM output about to be persisted or returned.
        firings:        NeuronFiring rows (or compatible objects) with a
                        ``was_included`` attribute; may be empty.
        actor:          resolved identity of the caller for audit.

    Returns:
        :class:`GuardResult` with final text, block flag, violations, and
        the audit action id.
    """
    assert isinstance(response_text, str), "response_text must be a string"
    policy_cfg = tenant.output_policies
    drafts = _collect_drafts(response_text, firings, policy_cfg)

    if not drafts:
        return GuardResult(
            final_text=response_text,
            blocked=False,
            violations=[],
            action_id=None,
        )

    redacted_text = _apply_redactions(response_text, drafts)
    blocked = any(d.action == "block" for d in drafts)
    action_id = await _log_action(
        db, actor, query_id, drafts,
        blocked=blocked,
        redacted=redacted_text != response_text,
    )
    rows = _persist_violations(db, query_id, action_id, drafts)
    await db.flush()

    return GuardResult(
        final_text=redacted_text,
        blocked=blocked,
        violations=rows,
        action_id=action_id,
    )


async def apply_output_guards(
    db: AsyncSession,
    query_id: int,
    slots: list[dict],
    actor: UserIdentity,
) -> tuple[list[OutputViolationOut], bool]:
    """Apply output policy to every response slot and persist governed text.

    This is a domain service shared by the internal query route, the public v1
    adapter, and immutable eval runs. Keeping it below the transport layer
    prevents service code from importing a FastAPI router.
    """
    assert len(slots) <= 16, "slot count exceeds sanity cap (JPL-2)"
    firing_result = await db.execute(
        select(NeuronFiring).where(NeuronFiring.query_id == query_id)
    )
    firings = list(firing_result.scalars())
    out: list[OutputViolationOut] = []
    blocked = False
    query_row: Query | None = None
    for slot in slots:
        text = slot.get("response") or ""
        if not text:
            continue
        guard = await run_guards(
            db,
            query_id=query_id,
            response_text=text,
            firings=firings,
            actor=actor,
        )
        if guard.final_text != text:
            slot["response"] = guard.final_text
            if query_row is None:
                query_row = await db.get(Query, query_id)
            if query_row is not None:
                mode = slot.get("mode")
                if mode == "haiku_neuron" and query_row.response_text == text:
                    query_row.response_text = guard.final_text
                elif mode == "opus_raw" and query_row.opus_response_text == text:
                    query_row.opus_response_text = guard.final_text
        out.extend(
            OutputViolationOut(
                id=row.id,
                rule_id=row.rule_id,
                severity=row.severity,
                action=row.action,
                matched_span=row.matched_span,
                redaction=row.redaction,
                detail=row.detail,
            )
            for row in guard.violations
        )
        blocked = blocked or guard.blocked
    return out, blocked
