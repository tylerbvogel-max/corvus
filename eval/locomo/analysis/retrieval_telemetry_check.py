"""Step 01 (mind-retrieval-telemetry) acceptance + falsification test.

Read-only over eval/locomo/results/. Two jobs:

1. ACCEPTANCE — the per-question `retrieval` payload must be present and must
   VARY across questions (a constant field is another n_hits).
2. FALSIFICATION — on non-adversarial questions, are retrieval score
   distributions for REFUSED questions distinguishable from ANSWERED ones?
   Forensics hypothesis: indistinguishable (refusal is prompt disposition,
   not evidence). Rank-sum via normal approximation, stdlib only.

Usage: python retrieval_telemetry_check.py [suffix]
       default suffix: strict-full-lifecyclesmoke-telemetry-after
"""
import math
import sys
from collections import Counter

from lib import is_refusal, load

FIELDS = ["top1_score", "top1_top2_margin", "n_above_threshold",
          "mass_above_threshold", "entity_coverage", "n_candidates",
          "top1_sim", "sim_margin", "sim_top5_mean"]
# Fields that MUST vary for acceptance. top1_score is deliberately excluded:
# RRF rank normalization pins the fused top-1 to a constant — that constancy
# is itself a Step 01 finding (the fused score cannot be the Step 03/05
# confidence signal; the raw sim_* fields exist precisely because of it).
CORE = ["top1_top2_margin", "mass_above_threshold", "n_candidates",
        "top1_sim", "sim_top5_mean"]


def acceptance(results):
    missing = [i for i, r in enumerate(results) if not r.get("retrieval")]
    print(f"payload present: {len(results) - len(missing)}/{len(results)}"
          + (f"  MISSING at {missing}" if missing else ""))
    ok = not missing
    for f in FIELDS:
        vals = [r["retrieval"].get(f) for r in results if r.get("retrieval")]
        distinct = Counter(v for v in vals)
        constant = len(distinct) <= 1
        if f in CORE:
            ok = ok and not constant
        print(f"  {f}: {len(distinct)} distinct values"
              + ("  ** CONSTANT — logged another n_hits **" if constant and f in CORE else "")
              + ("  (constant — expected under RRF, informational)" if constant and f not in CORE else ""))
    vec_lens = Counter(len(r["retrieval"].get("score_vector", []))
                       for r in results if r.get("retrieval"))
    print(f"  score_vector lengths: {dict(vec_lens)}")
    lanes = Counter(lane for r in results if r.get("retrieval")
                    for ls in r["retrieval"].get("delivered_lanes", {}).values()
                    for lane in ls)
    print(f"  delivered-hit lane counts: {dict(lanes)}")
    return ok


def ranksum_p(xs, ys):
    """Two-sided Wilcoxon rank-sum p via normal approximation (ties averaged)."""
    if not xs or not ys:
        return None
    pooled = sorted((v, i) for i, grp in enumerate((xs, ys)) for v in grp)
    ranks, i = {}, 0
    vals = [p[0] for p in pooled]
    while i < len(pooled):
        j = i
        while j < len(pooled) and vals[j] == vals[i]:
            j += 1
        for k in range(i, j):
            ranks[k] = (i + j + 1) / 2
        i = j
    rx = sum(ranks[k] for k, (_, g) in enumerate(pooled) if g == 0)
    n1, n2 = len(xs), len(ys)
    mu = n1 * (n1 + n2 + 1) / 2
    sigma = math.sqrt(n1 * n2 * (n1 + n2 + 1) / 12)
    if sigma == 0:
        return 1.0
    z = (rx - mu) / sigma
    return round(2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2)))), 4)


def falsification(results):
    nonadv = [r for r in results
              if r["category"] != 5 and r.get("retrieval")]
    refused = [r for r in nonadv if is_refusal(r["pred"])]
    answered = [r for r in nonadv if not is_refusal(r["pred"])]
    print(f"\nfalsification test (non-adversarial): "
          f"{len(refused)} refused vs {len(answered)} answered")
    if len(refused) < 5 or len(answered) < 5:
        print("  ** sample too small for a verdict — wiring check only; "
              "run on a full conversation before recording an answer **")
    for f in FIELDS:
        xs = [r["retrieval"][f] for r in refused
              if r["retrieval"].get(f) is not None]
        ys = [r["retrieval"][f] for r in answered
              if r["retrieval"].get(f) is not None]
        if not xs or not ys:
            print(f"  {f}: insufficient data (refused n={len(xs)}, answered n={len(ys)})")
            continue
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        print(f"  {f}: refused mean {mx:.4f} (n={len(xs)}) vs "
              f"answered mean {my:.4f} (n={len(ys)})  p={ranksum_p(xs, ys)}")


if __name__ == "__main__":
    suffix = sys.argv[1] if len(sys.argv) > 1 else "strict-full-lifecyclesmoke-telemetry-after"
    data = load(0, "memory", suffix)
    results = data["results"]
    print(f"conv0-memory-{suffix}: {len(results)} questions")
    ok = acceptance(results)
    falsification(results)
    print(f"\nACCEPTANCE: {'PASS' if ok else 'FAIL'}")
