"""AIP Phase 3 — Query Dossier aggregation service.

On-read composition of the five per-query governance/measurement signals.
Returns None if the query does not exist. No new tables, no caching; each
call issues five bounded, index-backed queries.

See `docs/design/aip-phase-3-query-dossier.md` for scope + caveats.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Action,
    EvalRun,
    EvalRunCase,
    EvalScore,
    IntegrityFinding,
    OutputViolation,
    Query,
    TenantConfig,
)
from app.schemas import (
    DossierActionOut,
    DossierEvalRunParticipation,
    DossierIntegrityFindingOut,
    DossierOutputViolationOut,
    EvalScoreOut,
    QueryDossier,
    QueryDossierActionsSection,
    QueryDossierEvalSection,
    QueryDossierIntegritySection,
    QueryDossierOutputSection,
    QueryDossierPipelineSection,
)


# ── Pure composers (unit-testable without a DB) ─────────────────────────

def compose_eval_section(
    ad_hoc_scores: list[EvalScore],
    participations: list[tuple[EvalRunCase, EvalRun]],
    certified_eval_run_id: int | None,
) -> QueryDossierEvalSection:
    """Build the eval section from already-loaded model rows.

    Pure — accepts pre-loaded SQLAlchemy rows so the logic is testable
    without a live database session.
    """
    assert isinstance(ad_hoc_scores, list), "ad_hoc_scores must be a list"
    assert isinstance(participations, list), "participations must be a list"
    scores_out = [
        EvalScoreOut(
            answer_label=s.answer_label,
            answer_mode=s.answer_mode,
            accuracy=s.accuracy,
            completeness=s.completeness,
            clarity=s.clarity,
            faithfulness=s.faithfulness,
            overall=s.overall,
        )
        for s in ad_hoc_scores
    ]
    parts_out: list[DossierEvalRunParticipation] = []
    # JPL-2 bounded loop — participations is a finite DB result list.
    for case, run in participations:
        parts_out.append(DossierEvalRunParticipation(
            eval_run_id=run.id,
            eval_run_case_id=case.id,
            case_label=case.case_label,
            suite_name=run.suite_name,
            suite_hash=run.suite_hash,
            certified=(certified_eval_run_id == run.id),
            blocked=case.blocked,
            scores_json=case.scores_json,
            violations_json=case.violations_json,
            run_status=run.status,
            run_started_at=run.started_at.isoformat() if run.started_at else None,
            run_completed_at=run.completed_at.isoformat() if run.completed_at else None,
        ))
    return QueryDossierEvalSection(
        ad_hoc_scores=scores_out,
        eval_run_participations=parts_out,
    )


def compose_output_section(
    violations: list[OutputViolation],
) -> QueryDossierOutputSection:
    """Build the output-check section from OutputViolation rows."""
    assert isinstance(violations, list), "violations must be a list"
    out: list[DossierOutputViolationOut] = []
    for v in violations:
        out.append(DossierOutputViolationOut(
            id=v.id,
            rule_id=v.rule_id,
            severity=v.severity,
            action=v.action,
            matched_span=v.matched_span,
            redaction=v.redaction,
            detail=v.detail,
            action_id=getattr(v, "action_id", None),
            created_at=v.created_at.isoformat() if getattr(v, "created_at", None) else None,
        ))
    return QueryDossierOutputSection(violations=out)


def compose_actions_section(actions: list[Action]) -> QueryDossierActionsSection:
    """Build the actions section from Action rows ordered by id."""
    assert isinstance(actions, list), "actions must be a list"
    out: list[DossierActionOut] = []
    for a in actions:
        out.append(DossierActionOut(
            id=a.id,
            kind=a.kind,
            actor_type=a.actor_type,
            actor_id=a.actor_id,
            state=a.state,
            requires_approval=a.requires_approval,
            reason=a.reason,
            parent_action_id=a.parent_action_id,
            applied_at=a.applied_at.isoformat() if a.applied_at else None,
            error_message=a.error_message,
            created_at=a.created_at.isoformat() if a.created_at else None,
        ))
    return QueryDossierActionsSection(actions=out)


def _parse_id_list(raw: str | None) -> set[int]:
    """Parse a JSON-text column holding a list of ints into a set."""
    if not raw:
        return set()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return set()
    if not isinstance(parsed, list):
        return set()
    return {int(n) for n in parsed if isinstance(n, (int, str)) and str(n).lstrip("-").isdigit()}


def compose_integrity_section(
    findings: list[IntegrityFinding],
    selected_neuron_ids_raw: str | None,
) -> QueryDossierIntegritySection:
    """Attribute findings to the query via neuron-id overlap.

    Pure — accepts pre-loaded findings and the raw JSON-text from
    query.selected_neuron_ids, returns only the overlapping subset.
    """
    assert isinstance(findings, list), "findings must be a list"
    selected_ids = _parse_id_list(selected_neuron_ids_raw)
    if not selected_ids:
        return QueryDossierIntegritySection(findings=[])
    out: list[DossierIntegrityFindingOut] = []
    # JPL-2 bounded loop — findings is a finite DB result list.
    for f in findings:
        finding_ids = _parse_id_list(f.neuron_ids_json)
        overlap = selected_ids & finding_ids
        if not overlap:
            continue
        out.append(DossierIntegrityFindingOut(
            id=f.id,
            scan_id=f.scan_id,
            finding_type=f.finding_type,
            severity=f.severity,
            priority_score=f.priority_score,
            description=f.description,
            status=f.status,
            resolution=f.resolution,
            attributed_via="selected_neurons",
            overlapping_neuron_ids=sorted(overlap),
            created_at=f.created_at.isoformat() if f.created_at else None,
        ))
    return QueryDossierIntegritySection(findings=out)


# ── DB loaders ──────────────────────────────────────────────────────────

async def _load_ad_hoc_scores(db: AsyncSession, query_id: int) -> list[EvalScore]:
    result = await db.execute(
        select(EvalScore).where(EvalScore.query_id == query_id).order_by(EvalScore.id)
    )
    return list(result.scalars())


async def _load_eval_participations(
    db: AsyncSession, query_id: int,
) -> list[tuple[EvalRunCase, EvalRun]]:
    result = await db.execute(
        select(EvalRunCase, EvalRun)
        .join(EvalRun, EvalRunCase.eval_run_id == EvalRun.id)
        .where(EvalRunCase.query_id == query_id)
        .order_by(EvalRunCase.id)
    )
    return [(row[0], row[1]) for row in result.all()]


async def _load_certified_eval_run_id(db: AsyncSession) -> int | None:
    cfg = await db.get(TenantConfig, 1)
    return getattr(cfg, "certified_eval_run_id", None) if cfg else None


async def _load_output_violations(
    db: AsyncSession, query_id: int,
) -> list[OutputViolation]:
    result = await db.execute(
        select(OutputViolation)
        .where(OutputViolation.query_id == query_id)
        .order_by(OutputViolation.id)
    )
    return list(result.scalars())


async def _load_actions(db: AsyncSession, query_id: int) -> list[Action]:
    result = await db.execute(
        select(Action)
        .where(Action.source_query_id == query_id)
        .order_by(Action.id)
    )
    return list(result.scalars())


async def _load_recent_integrity_findings(db: AsyncSession) -> list[IntegrityFinding]:
    """Load all integrity findings with non-null neuron_ids_json.

    The dossier scans in-memory for neuron-id overlap with the query's
    selected_neuron_ids. In practice the findings table is small (tens to
    low-hundreds at typical tenant scale) so a full scan is cheap and
    avoids needing a JSONB path expression against a TEXT column.
    """
    result = await db.execute(
        select(IntegrityFinding).where(IntegrityFinding.neuron_ids_json.isnot(None))
    )
    return list(result.scalars())


async def _build_pipeline_section(query: Query) -> QueryDossierPipelineSection:
    raw: Any = getattr(query, "stage_telemetry_json", None)
    stage_telem = raw if isinstance(raw, list) else []
    return QueryDossierPipelineSection(stage_telemetry=stage_telem)


# ── Top-level entrypoint ────────────────────────────────────────────────

async def build_dossier(db: AsyncSession, query_id: int) -> QueryDossier | None:
    """Compose the full dossier for `query_id`, or return None if it doesn't exist.

    Six bounded queries (pipeline from the Query row, ad-hoc eval scores,
    eval-run participations, tenant_config for certified flag, output
    violations, actions, integrity findings). Each is index-backed.
    """
    assert isinstance(query_id, int), "query_id must be int"
    assert query_id > 0, "query_id must be positive"

    query = await db.get(Query, query_id)
    if query is None:
        return None

    ad_hoc_scores = await _load_ad_hoc_scores(db, query_id)
    participations = await _load_eval_participations(db, query_id)
    certified_id = await _load_certified_eval_run_id(db)
    violations = await _load_output_violations(db, query_id)
    actions = await _load_actions(db, query_id)
    integrity_findings = await _load_recent_integrity_findings(db)

    pipeline = await _build_pipeline_section(query)
    eval_section = compose_eval_section(ad_hoc_scores, participations, certified_id)
    output_section = compose_output_section(violations)
    actions_section = compose_actions_section(actions)
    integrity_section = compose_integrity_section(
        integrity_findings, query.selected_neuron_ids,
    )

    return QueryDossier(
        query_id=query.id,
        user_message=query.user_message,
        created_at=query.created_at.isoformat() if query.created_at else None,
        pipeline=pipeline,
        eval=eval_section,
        output_checks=output_section,
        actions=actions_section,
        integrity=integrity_section,
    )
