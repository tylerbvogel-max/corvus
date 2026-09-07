"""Explicit, content-free summaries of the four maintenance report contracts.

Counts describe the named unit, not database mutations. A rejected evidence
candidate is not an operational error; a failed provider verdict is.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BatchOutcome:
    unit: str
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0

    def __post_init__(self) -> None:
        if self.unit not in {"sessions", "reviews", "passes", "operations"}:
            raise ValueError("unsupported maintenance count unit")
        for count in (self.succeeded, self.failed, self.skipped):
            if type(count) is not int or count < 0:
                raise ValueError("maintenance counts must be nonnegative integers")

    @property
    def outcome(self) -> str:
        if self.failed:
            return "partial" if self.succeeded else "error"
        return "ok" if self.succeeded else "no-work"

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome, "unit": self.unit,
            "attempted": self.succeeded + self.failed,
            "succeeded": self.succeeded, "failed": self.failed,
            "skipped": self.skipped, "counts_known": True,
            "reason": "returned-item-failure" if self.failed else None,
        }


def _count(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("invalid maintenance report count")
    return value


def _items(value: Any) -> list:
    if not isinstance(value, list):
        raise ValueError("invalid maintenance report collection")
    return value


JANITOR_PASSES = frozenset({
    "stale_approved_sweep", "corpus_health", "consolidation", "staleness",
    "decay", "homeostasis", "charter", "delivery", "reference_promotion",
    "scope_lint", "plasticity",
})


def summarize_report(job: str, report: dict) -> BatchOutcome:
    """Fail closed on malformed reports; never stringify report contents."""
    if not isinstance(report, dict):
        raise ValueError("maintenance report must be an object")
    if job == "distill":
        results = _items(report.get("results"))
        attempted = _count(report.get("processed"))
        ready = _count(report.get("ready"))
        if attempted != len(results) or ready < attempted:
            raise ValueError("inconsistent distillation report counts")
        if any(not isinstance(row, dict) for row in results):
            raise ValueError("invalid distillation result")
        failed = sum("error" in row for row in results)
        return BatchOutcome("sessions", attempted - failed, failed, ready - attempted)
    if job == "auditor":
        if report.get("skipped"):
            return BatchOutcome("reviews")
        funnel = report.get("candidate_funnel")
        if not isinstance(funnel, dict):
            raise ValueError("missing auditor review counts")
        attempted = _count(funnel.get("critic_batch"))
        failed = _count(report.get("verdict_failures"))
        skipped = _count(funnel.get("dropped_by_critic_cap", 0))
        return BatchOutcome("reviews", attempted - failed, failed, skipped)
    if job == "janitor":
        succeeded = failed = skipped = 0
        for name, result in report.items():
            if name == "ran_at":
                continue
            if name not in JANITOR_PASSES or not isinstance(result, dict):
                raise ValueError("unrecognized janitor pass report")
            broken = "error" in result
            if name == "consolidation":
                judged = _items(result.get("judged", []))
                if any(not isinstance(row, dict) for row in judged):
                    raise ValueError("invalid consolidation verdict report")
                broken |= any(row.get("verdict") == "error" for row in judged)
            if name == "delivery":
                broken |= _count(result.get("unjudged", 0)) > 0
            if broken:
                failed += 1
            elif result.get("skipped"):
                skipped += 1
            else:
                succeeded += 1
        return BatchOutcome("passes", succeeded, failed, skipped)
    if job == "compile":
        attempted = _count(report.get("composition_attempted"))
        failed = _count(report.get("composition_failed"))
        emitted = _items(report.get("emitted"))
        if attempted != len(emitted) + failed:
            raise ValueError("inconsistent compiler composition counts")
        clusters = _count(report.get("clusters"))
        charter = report.get("charter")
        if not isinstance(charter, dict):
            raise ValueError("invalid charter compilation report")
        # Each retraction, stale-manifest reconciliation, and charter rendering
        # is a completed operation, even when no new cluster was eligible.
        completed = (len(emitted) + len(_items(report.get("retracted")))
                     + len(_items(report.get("reconciled_ghosts")))
                     + int(bool(charter.get("path"))))
        skipped = clusters - attempted + int(not charter.get("path"))
        return BatchOutcome("operations", completed, failed, skipped)
    raise ValueError("unsupported maintenance job")


def attach_outcome(job: str, report: dict, detail: dict) -> dict:
    batch = summarize_report(job, report)
    detail["batch"] = batch
    return {**report, "maintenance": batch.as_dict()}


def safe_detail(detail: dict) -> dict:
    """Allowlisted wrapper metadata only, never arbitrary report/error prose."""
    result: dict[str, Any] = {}
    batch = detail.get("batch")
    if isinstance(batch, BatchOutcome):
        result["batch"] = batch.as_dict()
    limit = detail.get("limit")
    if type(limit) is int and 0 <= limit <= 200:
        result["limit"] = limit
    mode = detail.get("mode")
    if isinstance(mode, str) and mode in {"auto", "light", "deep", "event"}:
        result["mode"] = mode
    passes = detail.get("passes")
    if isinstance(passes, list):
        result["passes"] = sorted({p for p in passes
                                   if isinstance(p, str) and p in JANITOR_PASSES})
    return result


def exception_reason(exc: Exception) -> str:
    """Stable categories, not exception messages, class names, or tracebacks."""
    if isinstance(exc, OSError):
        return "job-io-failure"
    if isinstance(exc, (ValueError, TypeError, AssertionError)):
        return "job-invalid-result"
    return "job-execution-failure"
