"""Service-level objectives derived from user-visible behaviour.

A small set, deliberately. The record this implements says to define SLOs from
what a user would notice, set thresholds from a MEASURED baseline, and label
initial constants as hypotheses — not to stand up an observability platform
before the signals exist. So this reads signals Corvus already produces and
judges them; it introduces no new collection path and no new dependency.

Every objective carries its threshold's PROVENANCE. A number whose origin is
unrecorded gets defended in review as though it were evidence, and the three
kinds here are not equally trustworthy:

    stated-budget  a human decided this is the acceptable bound
    measured       taken from observed behaviour on this system
    hypothesis     a guess, placed to be falsified by the first real breach

Each also names an owner and a first response, because an alert that says only
"threshold exceeded" moves work to whoever happens to read it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.observability.jobs import inventory_health


STATED_BUDGET = "stated-budget"
MEASURED = "measured"
HYPOTHESIS = "hypothesis"

OK = "ok"
BREACHED = "breached"
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Objective:
    """One user-visible property, its bound, and what to do when it breaks."""

    id: str
    statement: str
    # OpenTelemetry-style metric name, so the signal can move to a collector
    # later without renaming what operators already grep for.
    metric: str
    unit: str
    threshold: float
    comparison: str               # "<=" or ">="
    threshold_basis: str
    basis_detail: str
    owner: str
    first_response: str

    def judge(self, value: float | None) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "id": self.id,
            "statement": self.statement,
            "metric": self.metric,
            "unit": self.unit,
            "threshold": self.threshold,
            "comparison": self.comparison,
            "threshold_basis": self.threshold_basis,
            "basis_detail": self.basis_detail,
            "owner": self.owner,
            "first_response": self.first_response,
            "value": value,
        }
        if value is None:
            entry["status"] = UNKNOWN
            entry["detail"] = "signal unavailable"
            return entry
        ok = value <= self.threshold if self.comparison == "<=" else value >= self.threshold
        entry["status"] = OK if ok else BREACHED
        entry["detail"] = (
            f"{value} {self.unit} vs {self.comparison} {self.threshold} {self.unit}"
        )
        return entry


OBJECTIVES: tuple[Objective, ...] = (
    Objective(
        id="recall-latency",
        statement="A recall answers fast enough that an agent turn is not held up by memory.",
        metric="corvus.recall.duration",
        unit="ms",
        threshold=10_000,
        comparison="<=",
        threshold_basis=STATED_BUDGET,
        basis_detail=(
            "10s per query is the budget Tyler set: LLM answers take minutes, so "
            "recall latency is explicitly not a constraint to optimise. Measured "
            "p95 at the time of writing was 3,448ms — well inside it. The bound "
            "exists to catch a REGRESSION, not to chase a faster number."
        ),
        owner="recall-graph",
        first_response=(
            "Check GET /metrics/mind for recall.stage_mean_ms to find which stage "
            "grew, then compare against the frozen-fixture replay before assuming "
            "the corpus is at fault."
        ),
    ),
    Objective(
        id="recall-availability",
        statement="Recall is actually serving queries, not silently returning nothing.",
        metric="corvus.recall.requests",
        unit="queries",
        threshold=1,
        comparison=">=",
        threshold_basis=MEASURED,
        basis_detail=(
            "Any query at all in the performance window means the path is live. "
            "Zero means recall stopped being reached — the failure a user notices "
            "first, and one that no latency percentile can express. A tenant that "
            "has NEVER served a query reports unknown rather than breached: a "
            "freshly deployed instance is untested, not broken."
        ),
        owner="recall-graph",
        first_response=(
            "Confirm the hook is firing (GET /metrics/mind/injection-channels) "
            "before suspecting the backend; a silent client is the common cause."
        ),
    ),
    Objective(
        id="distill-backlog",
        statement="Episode logs are distilled faster than sessions produce them.",
        metric="corvus.distill.backlog",
        unit="sessions",
        threshold=20,
        comparison="<=",
        threshold_basis=HYPOTHESIS,
        basis_detail=(
            "GUESSED. The distiller runs every 30 minutes and takes up to 10 "
            "sessions per run, so a backlog above ~20 means two consecutive runs "
            "could not keep up. Observed backlog when this was written: 0. "
            "Revisit against real backlog behaviour rather than defending 20."
        ),
        owner="memory-ingestion",
        first_response=(
            "Check GET /distill/status for blocked inputs first: blocked or "
            "invalid input counts make the backlog unknown, not healthy. "
            "Then check whether the distiller is failing or merely slow: "
            "GET /metrics/mind/jobs for the distill receipt, then the structured "
            "logs for job=distill. Opus spend through the Claude CLI is the usual "
            "bottleneck."
        ),
    ),
    Objective(
        id="scheduled-jobs",
        statement="No scheduled job is failing or overdue.",
        metric="corvus.jobs.unhealthy",
        unit="jobs",
        threshold=0,
        comparison="<=",
        threshold_basis=MEASURED,
        basis_detail=(
            "Zero is the only defensible bound: a job that is failing or late is "
            "either doing no work or doing it wrong, and both are silent. "
            "never-run is excluded — a job that has not yet reached its first "
            "tick is not a breach, it is a job waiting."
        ),
        owner="maintenance-integrity",
        first_response=(
            "GET /metrics/mind/jobs names the job and carries its remediation. "
            "A failure also raises corvus-job-alert with the same fields."
        ),
    ),
)

OBJECTIVES_BY_ID = {o.id: o for o in OBJECTIVES}


# ---- signal extraction ------------------------------------------------------

def _dig(payload: dict[str, Any], *path: str) -> Any:
    node: Any = payload
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def collect_signals(
    mind_metrics: dict[str, Any] | None,
    distill_status: dict[str, Any] | None,
    jobs: dict[str, Any] | None,
) -> dict[str, float | None]:
    """Map existing reports onto objective values. Missing is None, never 0.

    Conflating "no signal" with "signal reads zero" is how a dead collector
    reports perfect health, so an absent input stays None and its objective
    reports unknown.
    """
    latency = _dig(mind_metrics or {}, "recall", "latency_ms", "p95")
    window = _dig(mind_metrics or {}, "recall", "performance_window")
    lifetime = _dig(mind_metrics or {}, "recall", "total")
    backlog = _dig(distill_status or {}, "ready")
    # Blocked sources have no trustworthy unprocessed-session count. Do not
    # equate the eligible subset with the complete backlog or fabricate a
    # threshold breach. Older reports omit `blocked` and remain compatible.
    blocked = distill_status.get("blocked", 0) if isinstance(distill_status, dict) else None
    if type(backlog) is not int or backlog < 0 or type(blocked) is not int or blocked != 0:
        backlog = None

    # FRESH IS NOT BROKEN. Found by the release dry-run: a just-deployed
    # instance has served zero queries, and reporting that as a BREACH made an
    # objective no healthy new deployment could ever meet. The distinction is
    # lifetime volume — never served (fresh, nothing to judge) versus served
    # before but not now (stopped, which is the real failure this watches for).
    if isinstance(lifetime, (int, float)) and lifetime == 0:
        window = None

    unhealthy: float | None = None
    if jobs is not None:
        counts = jobs.get("counts") or {}
        unhealthy = float(counts.get("failing", 0) + counts.get("late", 0))

    return {
        "recall-latency": float(latency) if isinstance(latency, (int, float)) else None,
        "recall-availability": float(window) if isinstance(window, (int, float)) else None,
        "distill-backlog": float(backlog) if isinstance(backlog, (int, float)) else None,
        "scheduled-jobs": unhealthy,
    }


def evaluate(signals: dict[str, float | None]) -> dict[str, Any]:
    """Judge every objective. Breaches first — the list is read top-down."""
    entries = [o.judge(signals.get(o.id)) for o in OBJECTIVES]
    entries.sort(key=lambda e: {BREACHED: 0, UNKNOWN: 1, OK: 2}[e["status"]])
    return {
        "objectives": entries,
        "counts": {
            status: sum(1 for e in entries if e["status"] == status)
            for status in (OK, BREACHED, UNKNOWN)
        },
        "meeting_all": all(e["status"] == OK for e in entries),
        "threshold_provenance": {
            basis: sorted(o.id for o in OBJECTIVES if o.threshold_basis == basis)
            for basis in (STATED_BUDGET, MEASURED, HYPOTHESIS)
        },
    }


async def slo_report(
    metrics_loader: Callable[[], Any],
    distill_loader: Callable[[], Any],
) -> dict[str, Any]:
    """Assemble the live SLO report.

    Loaders are injected so this stays testable without a database, and so a
    failure in one signal source degrades that objective to unknown instead of
    taking down the whole report — an SLO endpoint that 500s during an incident
    is worse than no SLO endpoint.
    """
    async def _safe(loader: Callable[[], Any]) -> Any:
        try:
            result = loader()
            if hasattr(result, "__await__"):
                return await result
            return result
        except Exception:  # noqa: BLE001 - see docstring
            return None

    metrics = await _safe(metrics_loader)
    distill = await _safe(distill_loader)
    try:
        jobs = inventory_health()
    except Exception:  # noqa: BLE001
        jobs = None

    return evaluate(collect_signals(metrics, distill, jobs))
