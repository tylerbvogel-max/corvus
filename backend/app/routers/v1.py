"""External customer-facing API surface (AIP Phase 1.5 GTM-A).

``POST /v1/query`` is the hardened contract that external LLM frontends
(Fluent, Claude Enterprise, Copilot, Cursor, …) call when they need
Corvus domain context injected into their prompt pipeline. It differs
from the internal ``/query`` surface in three ways:

1. **OAuth2-gated** via :func:`app.middleware.rbac.require_role` (at
   least ``reader``). When ``RBAC_MODE=azure_ad`` this is full JWT
   enforcement; in dev it degrades to header auth.
2. **Formal response contract** — :class:`V1QueryResponse` with stable
   ``answer``, ``fragment_labels``, ``fragments`` (full mode only),
   ``lineage_id``, and ``eval_run_id`` fields.
3. **Per-tenant token-bucket rate limiting** via
   :class:`app.middleware.rate_limit.RateLimiter` so a single tenant
   cannot saturate the pipeline.

Every request also flows through Pattern #7 output-policy gates — so a
blocked response surfaces as HTTP 422 with the offending rule list
instead of reaching the caller.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.middleware.rate_limit import RateLimiter
from app.middleware.rbac import UserIdentity, require_role
from app.models import TenantConfig
from app.schemas import (
    OutputViolationOut,
    V1ContextFragment,
    V1QueryRequest,
    V1QueryResponse,
)
from app.services.executor import execute_query
from app.services.pipeline import PipelineStageError
from app.tenant import tenant

router = APIRouter(prefix="/v1", tags=["external-v1"])


# Module-level limiter — capacity/refill resolved at request time so
# hot-reload of settings during dev still takes effect on restart.
_LIMITER = RateLimiter(
    capacity=settings.v1_rate_limit_capacity,
    refill_per_sec=settings.v1_rate_limit_refill_per_sec,
)


def _rate_limit_key(identity: UserIdentity) -> str:
    """Compose the per-tenant+user rate limit key."""
    return f"{tenant.tenant_id}|{identity.user_id}"


async def _enforce_rate_limit(identity: UserIdentity) -> None:
    ok = await _LIMITER.acquire(_rate_limit_key(identity))
    if not ok:
        raise HTTPException(
            status_code=429,
            detail={
                "message": "Rate limit exceeded for /v1/query",
                "tenant": tenant.tenant_id,
            },
        )


def _first_slot_response(slots: list[dict]) -> str:
    """Extract the primary neuron-slot answer, falling back to any slot."""
    for slot in slots:
        if slot.get("neurons") and slot.get("response"):
            return str(slot["response"])
    for slot in slots:
        if slot.get("response"):
            return str(slot["response"])
    return ""


def _build_fragments(
    neuron_scores: list[dict],
    mode: str,
) -> tuple[list[str], list[V1ContextFragment]]:
    """Return ``(labels, fragments)`` — fragments only populated in full mode."""
    labels: list[str] = []
    fragments: list[V1ContextFragment] = []
    for ns in neuron_scores:
        label = str(ns.get("label") or f"neuron-{ns.get('neuron_id')}")
        labels.append(label)
        if mode != "full":
            continue
        snippet_raw = ns.get("summary") or ns.get("content") or ""
        snippet = str(snippet_raw)[:400]
        fragments.append(V1ContextFragment(
            neuron_id=int(ns.get("neuron_id") or 0),
            label=label,
            snippet=snippet,
            source=ns.get("department"),
            combined_score=float(ns.get("combined") or 0.0),
        ))
    return labels, fragments


def _build_v1_response(
    result: dict,
    mode: str,
    blocked: bool,
    violations: list[OutputViolationOut],
    eval_run_id: int | None,
) -> V1QueryResponse:
    """Shape the executor output + guard result into the external contract."""
    labels, fragments = _build_fragments(result.get("neuron_scores", []), mode)
    return V1QueryResponse(
        answer=_first_slot_response(result.get("slots", [])),
        fragment_labels=labels,
        fragments=fragments,
        lineage_id=int(result["query_id"]),
        eval_run_id=eval_run_id,
        blocked=blocked,
        violations=violations,
    )


async def _certified_eval_run_id(db: AsyncSession) -> int | None:
    """Return the tenant's currently-certified EvalRun id (Pattern #3).

    ``None`` when no run has been promoted yet — callers treat that as
    "uncertified" and may surface a warning upstream.
    """
    config = await db.get(TenantConfig, 1)
    return config.certified_eval_run_id if config else None


async def _run_v1_pipeline(
    db: AsyncSession, req: V1QueryRequest,
) -> dict:
    """Execute a single haiku_neuron slot and surface HTTP errors cleanly."""
    slot_spec = [{
        "mode": "haiku_neuron",
        "token_budget": req.token_budget,
        "top_k": req.top_k,
    }]
    try:
        return await execute_query(
            db, req.message, slots=slot_spec, prior_neuron_ids=None,
        )
    except HTTPException:
        raise
    except PipelineStageError as pse:
        raise HTTPException(
            status_code=500,
            detail={
                "message": f"Pipeline stage '{pse.stage_name}' failed",
                "failed_stage": pse.stage_name,
                "cause": str(pse.original),
            },
        ) from pse
    except RuntimeError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover — defensive envelope
        raise HTTPException(
            status_code=500,
            detail=f"/v1/query execution failed: {exc}",
        ) from exc


@router.post("/query", response_model=V1QueryResponse)
async def v1_query(
    req: V1QueryRequest,
    request: Request,  # noqa: ARG001 — reserved for request-scoped telemetry
    db: AsyncSession = Depends(get_db),
    identity: UserIdentity = Depends(require_role("reader")),
):
    """External customer endpoint: classify → retrieve → answer → gate.

    Returns a stable :class:`V1QueryResponse` suitable for piping into an
    external LLM frontend. Blocking output-policy violations surface as
    HTTP 422 so callers can treat them as hard failures; warn/redact
    violations are delivered inline via the ``violations`` field.
    """
    await _enforce_rate_limit(identity)

    # Lazy import avoids a cycle with ``app.routers.query``.
    from app.routers.query import _apply_output_guards

    result = await _run_v1_pipeline(db, req)

    query_id = result.get("query_id")
    violations: list[OutputViolationOut] = []
    blocked = False
    if query_id is not None:
        violations, blocked = await _apply_output_guards(
            db, query_id, result.get("slots", []), identity,
        )
        await db.commit()

    if blocked:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Response blocked by output policy",
                "lineage_id": query_id,
                "blocking_violations": [
                    v.model_dump() for v in violations if v.action == "block"
                ],
            },
        )

    eval_run_id = await _certified_eval_run_id(db)
    return _build_v1_response(result, req.mode, blocked, violations, eval_run_id)
