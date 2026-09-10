"""Checkpoint exclusions must not masquerade as a healthy empty backlog."""

import pytest

from app.observability import slo


@pytest.mark.parametrize(
    "status, expected",
    [
        ({"ready": 0, "blocked": 1}, slo.UNKNOWN),
        ({"ready": 3, "blocked": 1}, slo.UNKNOWN),
        ({"ready": 0, "blocked": 0}, slo.OK),
        ({"ready": 3, "blocked": 0}, slo.OK),
        ({"ready": 21, "blocked": 0}, slo.BREACHED),
        ({"ready": 0}, slo.OK),
        ({"ready": 0, "blocked": "1"}, slo.UNKNOWN),
        ({"ready": 0, "blocked": -1}, slo.UNKNOWN),
        ({"ready": 0, "blocked": False}, slo.UNKNOWN),
        ({"ready": 0, "blocked": None}, slo.UNKNOWN),
        ({"ready": -1, "blocked": 0}, slo.UNKNOWN),
        ({"ready": False, "blocked": 0}, slo.UNKNOWN),
        ({"ready": 0.5, "blocked": 0}, slo.UNKNOWN),
        ({"blocked": 0}, slo.UNKNOWN),
        (None, slo.UNKNOWN),
    ],
)
@pytest.mark.asyncio
async def test_checkpoint_backlog_report(monkeypatch, status, expected):
    monkeypatch.setattr(
        slo, "inventory_health",
        lambda: {"counts": {"ok": 1, "late": 0, "failing": 0}},
    )

    async def distill_loader():
        return status

    report = await slo.slo_report(
        metrics_loader=lambda: {
            "recall": {
                "latency_ms": {"p95": 1},
                "performance_window": 1,
                "total": 1,
            },
        },
        distill_loader=distill_loader,
    )
    objective = next(o for o in report["objectives"] if o["id"] == "distill-backlog")
    assert objective["status"] == expected
    assert report["meeting_all"] is (expected == slo.OK)
    if expected == slo.UNKNOWN:
        assert objective["value"] is None
    else:
        assert objective["value"] == status["ready"]
