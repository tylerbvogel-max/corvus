"""Aggregate-only analysis for Step 05 adaptive recall depth.

Reads append-only result artifacts and emits no question text or gold answers.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from statistics import mean, median


RESULTS = Path(__file__).resolve().parents[1] / "results"
ARMS = {
    "fixed": RESULTS / "conv0-memory-verified-full-lifecyclestep05-sweep-fixed-r30.json",
    "0.90": RESULTS / "conv0-memory-verified-full-lifecyclestep05-sweep-t090-r30.json",
    "0.925": RESULTS / "conv0-memory-verified-full-lifecyclestep05-sweep-t0925-r30.json",
    "0.95": RESULTS / "conv0-memory-verified-full-lifecyclestep05-sweep-t095-r30.json",
}
CATEGORY_NAMES = {
    1: "multi-hop",
    2: "temporal",
    3: "open-domain",
    4: "single-hop",
    5: "adversarial",
}


def _exact_mcnemar_p(gains: int, losses: int) -> float:
    """Two-sided exact binomial McNemar p-value for discordant pairs."""
    n = gains + losses
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(gains, losses) + 1))
    return min(1.0, 2.0 * tail / (2**n))


def _load() -> dict[str, dict]:
    payloads = {}
    for name, path in ARMS.items():
        with path.open(encoding="utf-8") as handle:
            payloads[name] = json.load(handle)
    return payloads


def analyze() -> dict:
    payloads = _load()
    baseline = payloads["fixed"]
    baseline_rows = baseline["results"]
    out = {
        "corpus": baseline["corpus"],
        "arms": {},
    }
    for arm, payload in payloads.items():
        rows = payload["results"]
        gains = sum(
            not fixed["correct"] and candidate["correct"]
            for fixed, candidate in zip(baseline_rows, rows, strict=True)
        )
        losses = sum(
            fixed["correct"] and not candidate["correct"]
            for fixed, candidate in zip(baseline_rows, rows, strict=True)
        )
        ks = [row["n_hits"] for row in rows]
        categories = {}
        for category_id, category_name in CATEGORY_NAMES.items():
            fixed_category = [
                row for row in baseline_rows if row["category"] == category_id
            ]
            arm_category = [row for row in rows if row["category"] == category_id]
            fixed_score = baseline["scores"]["per_category"][category_name]
            arm_score = payload["scores"]["per_category"][category_name]
            category_gains = sum(
                not fixed["correct"] and candidate["correct"]
                for fixed, candidate in zip(
                    fixed_category, arm_category, strict=True,
                )
            )
            category_losses = sum(
                fixed["correct"] and not candidate["correct"]
                for fixed, candidate in zip(
                    fixed_category, arm_category, strict=True,
                )
            )
            categories[category_name] = {
                "n": len(arm_category),
                "score": arm_score,
                "delta_pp": round((arm_score - fixed_score) * 100, 2),
                "gains": category_gains,
                "losses": category_losses,
                "mean_k": round(mean(row["n_hits"] for row in arm_category), 3),
            }
        out["arms"][arm] = {
            "overall": payload["scores"]["overall"],
            "delta_pp": round(
                (payload["scores"]["overall"] - baseline["scores"]["overall"]) * 100,
                2,
            ),
            "gains": gains,
            "losses": losses,
            "exact_mcnemar_p": round(_exact_mcnemar_p(gains, losses), 6),
            "exact_refusals": sum(
                row["pred"].strip().lower() == "no information available"
                for row in rows
            ),
            "mean_k": round(mean(ks), 3),
            "median_k": median(ks),
            "k_range": [min(ks), max(ks)],
            "k_distribution": dict(sorted(Counter(ks).items())),
            "categories": categories,
        }
    return out


if __name__ == "__main__":
    print(json.dumps(analyze(), indent=2))
