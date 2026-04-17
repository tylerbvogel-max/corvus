"""EvalRun execution — Pattern #3 immutable eval artifacts.

An eval run is a frozen snapshot of a suite executed against the live
pipeline. Once ``status`` transitions from ``running`` to a terminal state
(``completed`` / ``failed``), the row and its cases are append-only.
Re-running a suite always creates a new ``EvalRun`` — never mutates an
existing one — so promoting a run to "certified" (see
``TenantConfig.certified_eval_run_id``) is a durable claim about the
specific run id.

The runner deliberately runs cases serially inside their own SAVEPOINTs so
a single failing case can be recorded with ``error_message`` without
poisoning the rest of the run. The outer ``EvalRun`` lifecycle is managed
via the action bus so the audit trail mirrors Pattern #1 conventions.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.middleware.rbac import UserIdentity
from app.models import EvalRun, EvalRunCase, NeuronScoreOverride, OutputViolation
from app.services import action_bus
from app.services.executor import execute_query
from app.services.llm_provider import MODEL_REGISTRY
from app.tenant import tenant

logger = logging.getLogger(__name__)

# Bump when any scoring-engine component (signals, weights, spread, inhibitory
# pass, assembly) changes in a way that should invalidate prior eval runs.
SCORING_ENGINE_VERSION = "1.0.0"


# ── Suite loading ──

@dataclass(frozen=True)
class SuiteCase:
    label: str
    text: str


@dataclass(frozen=True)
class Suite:
    name: str
    cases: tuple[SuiteCase, ...]
    suite_hash: str


def _suite_dir() -> Path:
    return Path(tenant.tenant_dir) / "eval_suites"


def load_suite(suite_name: str) -> Suite:
    """Load a tenant-scoped suite YAML into a hashed ``Suite``.

    The hash is computed over the normalized (sorted, stable) case list so
    the same suite content always produces the same ``suite_hash``, making
    ``(suite_name, suite_hash)`` a reliable dedup key across runs.
    """
    assert suite_name and "/" not in suite_name, "invalid suite name"
    path = _suite_dir() / f"{suite_name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"eval suite not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    raw_cases = data.get("cases") or []
    assert isinstance(raw_cases, list), "suite cases must be a list"
    cases: list[SuiteCase] = []
    for idx, case in enumerate(raw_cases):
        assert isinstance(case, dict), f"case {idx} must be a mapping"
        label = str(case.get("label") or f"case-{idx}")
        text = case.get("text") or case.get("query")
        assert text, f"case {label} missing text/query field"
        cases.append(SuiteCase(label=label, text=str(text)))

    digest_input = json.dumps(
        [{"label": c.label, "text": c.text} for c in cases],
        sort_keys=True,
    ).encode("utf-8")
    suite_hash = hashlib.sha256(digest_input).hexdigest()
    return Suite(name=suite_name, cases=tuple(cases), suite_hash=suite_hash)


# ── Snapshot helpers ──

async def _snapshot_overrides(db: AsyncSession) -> list[dict]:
    """Freeze every active NeuronScoreOverride row for posterity."""
    result = await db.execute(
        select(NeuronScoreOverride).where(NeuronScoreOverride.is_active.is_(True))
    )
    rows = list(result.scalars())
    return [
        {
            "id": row.id,
            "neuron_id": row.neuron_id,
            "signal": row.signal,
            "floor": row.floor,
            "ceiling": row.ceiling,
            "multiplier": row.multiplier,
            "reason": row.reason,
        }
        for row in rows
    ]


def _model_versions() -> dict[str, dict[str, Any]]:
    """Snapshot every model in MODEL_REGISTRY with its display + provider info."""
    snapshot: dict[str, dict[str, Any]] = {}
    for key, info in MODEL_REGISTRY.items():
        snapshot[key] = {
            "display_name": info.display_name,
            "provider": info.provider,
            "api_id": info.api_id,
            "tier": info.tier,
        }
    return snapshot


# ── Case execution ──

async def _execute_case(
    db: AsyncSession,
    eval_run: EvalRun,
    case: SuiteCase,
    actor: UserIdentity,
) -> EvalRunCase:
    """Run a single suite case, persist the result, never re-raise.

    ``execute_query`` manages its own transaction (commits on success), so a
    SAVEPOINT around the call would close-then-reclose and error. Instead we
    let the pipeline's own transaction handling stand and wrap the body in a
    try/except — per-case failures record an ``error_message`` on the case
    row without aborting the rest of the run.
    """
    from app.routers.query import _apply_output_guards

    case_row = EvalRunCase(
        eval_run_id=eval_run.id,
        case_label=case.label,
        query_text=case.text,
    )
    try:
        result = await execute_query(
            db, case.text, slots=[{
                "mode": "haiku_neuron",
                "token_budget": 2000,
                "top_k": 10,
            }],
        )
        query_id = result.get("query_id")
        violations_out: list[dict] = []
        blocked = False
        if query_id is not None:
            violations, blocked = await _apply_output_guards(
                db, query_id, result.get("slots", []), actor,
            )
            violations_out = [v.model_dump() for v in violations]
        case_row.query_id = query_id
        case_row.lineage_id = query_id
        case_row.response_text = result.get("slots", [{}])[0].get("response")
        case_row.blocked = blocked
        case_row.violations_json = {"violations": violations_out}
        case_row.scores_json = {
            "neuron_count": len(result.get("neuron_scores", [])),
            "intent": result.get("intent"),
        }
    except Exception as exc:  # noqa: BLE001 — deliberate: per-case envelope
        logger.warning("eval case %s failed: %s", case.label, exc)
        case_row.error_message = str(exc)[:500]
    db.add(case_row)
    await db.flush()
    return case_row


def _summarize(cases: list[EvalRunCase]) -> dict[str, Any]:
    """Aggregate per-case verdicts into the run-level summary blob."""
    total = len(cases)
    blocked = sum(1 for c in cases if c.blocked)
    errors = sum(1 for c in cases if c.error_message)
    violation_count = 0
    severity_counts: dict[str, int] = {}
    for c in cases:
        payload = (c.violations_json or {}).get("violations") or []
        violation_count += len(payload)
        for v in payload:
            sev = str(v.get("severity") or "unknown")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
    return {
        "total": total,
        "blocked": blocked,
        "errors": errors,
        "violation_count": violation_count,
        "severity_counts": severity_counts,
        "pass_rate": ((total - blocked - errors) / total) if total else 0.0,
    }


# ── Orchestrator ──

async def _open_run_record(
    db: AsyncSession,
    suite: Suite,
    overrides: list[dict],
    actor: UserIdentity,
) -> tuple[EvalRun, int | None]:
    """Record the start-action + create the EvalRun row in ``running`` state."""
    start_action = await action_bus.submit(
        db, kind="eval.run.start", actor=actor,
        input_data={
            "suite_name": suite.name,
            "suite_hash": suite.suite_hash,
            "tenant_id": tenant.tenant_id,
            "model_versions": _model_versions(),
            "scoring_engine_version": SCORING_ENGINE_VERSION,
            "case_count": len(suite.cases),
        },
        actor_type="user",
        reason=f"Start eval run for suite {suite.name!r}",
    )
    eval_run = EvalRun(
        suite_name=suite.name,
        suite_hash=suite.suite_hash,
        model_versions=_model_versions(),
        scoring_engine_version=SCORING_ENGINE_VERSION,
        overrides_snapshot={"overrides": overrides},
        tenant_id=tenant.tenant_id,
        status="running",
        started_by=actor.user_id,
        action_id=start_action.action_id,
    )
    db.add(eval_run)
    await db.flush()
    return eval_run, start_action.action_id


async def _close_run_record(
    db: AsyncSession,
    eval_run: EvalRun,
    case_rows: list[EvalRunCase],
    actor: UserIdentity,
    start_action_id: int | None,
) -> None:
    """Aggregate cases, flip to terminal state, record the complete-action."""
    summary = _summarize(case_rows)
    eval_run.summary_json = summary
    eval_run.completed_at = datetime.utcnow()
    # A run is "failed" only if every case errored — a single bad case still
    # produces a reviewable artifact, just with a lower pass_rate.
    all_errored = summary["errors"] == summary["total"] and summary["total"] > 0
    eval_run.status = "failed" if all_errored else "completed"
    await action_bus.submit(
        db, kind="eval.run.complete", actor=actor,
        input_data={
            "eval_run_id": eval_run.id,
            "status": eval_run.status,
            "case_count": summary["total"],
            "blocked_count": summary["blocked"],
            "violation_count": summary["violation_count"],
            "error_count": summary["errors"],
        },
        actor_type="user",
        reason=f"Complete eval run {eval_run.id}",
        parent_action_id=start_action_id,
    )
    await db.flush()


async def start_run(
    db: AsyncSession,
    suite_name: str,
    actor: UserIdentity,
) -> EvalRun:
    """Execute a suite end-to-end; persist the EvalRun + cases.

    Returns the completed EvalRun (status=completed or failed). Commits are
    the caller's responsibility — the router owns the outer transaction.
    Individual cases use SAVEPOINTs so one bad case doesn't abort the run.
    """
    suite = load_suite(suite_name)
    overrides = await _snapshot_overrides(db)
    eval_run, start_action_id = await _open_run_record(db, suite, overrides, actor)
    case_rows = [
        await _execute_case(db, eval_run, case, actor) for case in suite.cases
    ]
    await _close_run_record(db, eval_run, case_rows, actor, start_action_id)
    return eval_run


async def get_run(db: AsyncSession, run_id: int) -> EvalRun | None:
    """Fetch a single EvalRun by id (no eager case load)."""
    return await db.get(EvalRun, run_id)


async def list_runs(db: AsyncSession, limit: int = 50) -> list[EvalRun]:
    """Recent runs, newest first. Used by the admin UI index."""
    assert 0 < limit <= 200, "limit out of range (JPL-5)"
    result = await db.execute(
        select(EvalRun).order_by(EvalRun.id.desc()).limit(limit)
    )
    return list(result.scalars())


async def list_cases(db: AsyncSession, run_id: int) -> list[EvalRunCase]:
    """All cases belonging to a run, in insertion order."""
    result = await db.execute(
        select(EvalRunCase)
        .where(EvalRunCase.eval_run_id == run_id)
        .order_by(EvalRunCase.id.asc())
    )
    return list(result.scalars())
