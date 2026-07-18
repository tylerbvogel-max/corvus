"""Golden replay eval: does the auditor rediscover the 2026-07-15 sweep?

Phase A (default, free, deterministic): microglial surveillance scores
every frozen BEFORE state. Measures candidate recall on known defects,
false-positive rate on healthy controls, and per-signal firing counts —
the baseline the mandate says thresholds must be calibrated FROM.
Calibration/test split is respected: recall is reported per split;
threshold tuning may only look at the calibration half.

Phase B (--critic N): additionally runs the Opus critic on N test-split
defect packets (episode evidence reconstructed live where the cited
session log still exists) and reports disposition-family agreement and
unsupported-fact validation results. Costs real critic calls — bounded.

Run: cd backend && TENANT_ID=corvus-mind PYTHONPATH=. \
     venv/bin/python tests/eval_auditor_golden.py [--critic N]

Not pytest-collected (like replay_nvm_throwaway.py): it prints a report
and writes baseline JSON beside the fixture.
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "fixtures", "auditor_golden")
GOLDEN = os.path.join(FIXTURE_DIR, "golden.json")
BASELINE_OUT = os.path.join(FIXTURE_DIR, "baseline.json")


def hydrate(entry):
    """Frozen BEFORE dict -> detached Neuron for the pure scorer."""
    from app.models import Neuron
    n = Neuron()
    b = entry["before"]
    for k, v in b.items():
        if k in ("created_at", "last_verified") and v:
            try:
                v = datetime.fromisoformat(str(v))
            except ValueError:
                v = None
        setattr(n, k, v)
    if not hasattr(n, "weak_edges") or n.weak_edges is None:
        n.weak_edges = None
    n.entities = None
    n.embedding = None
    n.abstraction_type = getattr(n, "abstraction_type", None) or "principle"
    n.last_accessed_at = None
    return n


def phase_a(golden) -> dict:
    from app.config import settings
    from app.services.memory_quality_auditor import score_neuron

    # Replay limitation, stated not hidden: graph-context signals
    # (hidden_duplicate, open_finding, orphaned, learning events) are
    # unavailable for historical states — this measures per-neuron lanes.
    ctx = {"neighbor": {}, "findings": {}, "learning": {}, "edges": {},
           "active_ids": set()}
    threshold = settings.auditor_risk_threshold
    report = {"threshold": threshold, "splits": {}, "signal_fires": {},
              "misses": [], "control_false_positives": []}

    for split in ("calibration", "test"):
        defects = [e for e in golden["defects"]
                   if e["split"] == split and e["expected_family"] != "keep"]
        controls = [e for e in golden["controls"] if e["split"] == split] + \
                   [e for e in golden["defects"]
                    if e["split"] == split and e["expected_family"] == "keep"]
        caught = 0
        for e in defects:
            s = score_neuron(hydrate(e), ctx)
            e["_score"] = s
            for name in s["signals"]:
                report["signal_fires"][name] = \
                    report["signal_fires"].get(name, 0) + 1
            if s["risk_score"] >= threshold:
                caught += 1
            else:
                report["misses"].append({
                    "neuron_id": e["neuron_id"], "split": split,
                    "expected": e["expected_family"],
                    "manual": e["manual_disposition"],
                    "risk_score": s["risk_score"],
                    "signals": {k: v["score"] for k, v in s["signals"].items()},
                })
        control_fp = 0
        for e in controls:
            s = score_neuron(hydrate(e), ctx)
            if s["risk_score"] >= threshold:
                control_fp += 1
                report["control_false_positives"].append({
                    "neuron_id": e["neuron_id"], "split": split,
                    "risk_score": s["risk_score"],
                    "signals": {k: v["score"] for k, v in s["signals"].items()},
                })
        report["splits"][split] = {
            "defects": len(defects), "caught": caught,
            "recall": round(caught / len(defects), 3) if defects else None,
            "controls": len(controls), "control_false_positives": control_fp,
            "control_fp_rate": round(control_fp / len(controls), 3)
            if controls else None,
        }
    return report


async def phase_b(golden, n_critic: int) -> dict:
    from app.services.memory_quality_auditor import (
        MUTATING_DISPOSITIONS, build_evidence_packet, critique_neuron,
        score_neuron, validate_verdict)
    from app.database import async_session

    ranked = sorted(
        (e for e in golden["defects"]
         if e["split"] == "test" and e["expected_family"] != "keep"),
        key=lambda e: -e.get("_score", {}).get("risk_score", 0))
    picks = ranked[:n_critic]
    out = {"reviewed": [], "family_agreement": 0, "mutating_agreement": 0,
           "validation_failures": 0, "cost_usd": 0.0}
    ctx = {"neighbor": {}, "findings": {}, "learning": {}, "edges": {},
           "active_ids": set()}
    async with async_session() as db:
        for e in picks:
            neuron = hydrate(e)
            score = e.get("_score") or score_neuron(neuron, ctx)
            packet = await build_evidence_packet(db, neuron, ctx, score)
            row = {"neuron_id": e["neuron_id"],
                   "expected": e["expected_family"]}
            try:
                verdict, usage = await critique_neuron(packet)
                violations = validate_verdict(verdict, packet, neuron)
                row.update({
                    "got": verdict.get("disposition"),
                    "confidence": verdict.get("confidence"),
                    "violations": violations,
                })
                out["cost_usd"] += float(usage.get("cost_usd") or 0)
                if violations:
                    out["validation_failures"] += 1
                elif verdict.get("disposition") == e["expected_family"]:
                    out["family_agreement"] += 1
                elif verdict.get("disposition") in MUTATING_DISPOSITIONS and \
                        e["expected_family"] in MUTATING_DISPOSITIONS:
                    out["mutating_agreement"] += 1
            except Exception as exc:  # noqa: BLE001 — eval must finish
                row["error"] = str(exc)[:200]
            out["reviewed"].append(row)
    return out


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--critic", type=int, default=0,
                        help="run the Opus critic on N test-split defects")
    args = parser.parse_args()

    with open(GOLDEN, encoding="utf-8") as fh:
        golden = json.load(fh)

    report = {"phase_a": phase_a(golden)}
    if args.critic > 0:
        report["phase_b"] = await phase_b(golden, args.critic)

    with open(BASELINE_OUT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False, default=str)
    print(json.dumps(report["phase_a"]["splits"], indent=2))
    print("signal fires:", report["phase_a"]["signal_fires"])
    print(f"misses: {len(report['phase_a']['misses'])}, "
          f"control FPs: {len(report['phase_a']['control_false_positives'])}")
    if args.critic:
        b = report["phase_b"]
        print(f"critic: {len(b['reviewed'])} reviewed, "
              f"exact family {b['family_agreement']}, "
              f"mutating-family {b['mutating_agreement']}, "
              f"validation failures {b['validation_failures']}, "
              f"cost ${b['cost_usd']:.3f}")
    print(f"baseline -> {BASELINE_OUT}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
