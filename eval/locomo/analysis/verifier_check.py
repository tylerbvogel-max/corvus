"""Step 02 (mind-answer-verifier-split) acceptance analysis.

Read-only over eval/locomo/results/. Jobs:

1. VERDICT PROFILE — verifier verdict counts overall and per category. The
   node's named failure mode: if the unsupported rate is ~flat across
   categories the way the old refusal rate was (19.06% vs 19.15%), the bug
   moved a layer down instead of dying.
2. EVIDENCE COUPLING (the fix-vs-relocation criterion) — the verifier's
   unsupported verdicts must sit on measurably weaker retrieval than its
   supported ones. Raw pre-RRF sim_* fields ONLY: RRF rank-pins every fused
   score (step 01 finding), so a fused-score "signal" would be an artifact.
3. BEFORE/AFTER — per-category accuracy and refusal against the step 01
   telemetry-full baseline arm, confounders printed alongside.
4. COST — verifier calls made, skip rate, added latency.

Usage: python verifier_check.py [after_suffix] [before_suffix]
       defaults: verified-full-lifecycleverifier-full
                 strict-full-lifecycletelemetry-full
"""
import sys
from collections import Counter, defaultdict

from lib import CATNAME, is_refusal, load, pct
from retrieval_telemetry_check import ranksum_p

# Raw pre-RRF cosine fields — the only usable retrieval-confidence signals.
SIM_FIELDS = ["top1_sim", "sim_margin", "sim_top5_mean"]


def verdict_of(r):
    return (r.get("verifier") or {}).get("verdict", "absent")


def verdict_profile(results):
    print("== verdict profile ==")
    overall = Counter(verdict_of(r) for r in results)
    print(f"overall: {dict(overall)}")
    print(f"{'category':<12} {'n':>4} {'unsup':>6} {'partial':>8} "
          f"{'supported':>10} {'draft-ref':>10} {'unsup%':>7}")
    for cat in sorted({r["category"] for r in results}):
        sub = [r for r in results if r["category"] == cat]
        c = Counter(verdict_of(r) for r in sub)
        checked = [r for r in sub if verdict_of(r) not in
                   ("draft-refused", "absent")]
        rate = pct(c["unsupported"] + c["unparseable"], len(checked))
        print(f"{CATNAME[cat]:<12} {len(sub):>4} {c['unsupported']:>6} "
              f"{c['partially-supported']:>8} {c['supported']:>10} "
              f"{c['draft-refused']:>10} {rate:>6}%")


def evidence_coupling(results):
    """Non-adversarial only: adversarial questions have no gold evidence, so
    their (correctly) unsupported verdicts would smear the comparison."""
    print("\n== evidence coupling (non-adversarial, raw sims only) ==")
    checked = [r for r in results
               if r["category"] != 5 and r.get("retrieval")
               and verdict_of(r) in ("supported", "partially-supported",
                                     "unsupported", "unparseable")]
    unsup = [r for r in checked
             if verdict_of(r) in ("unsupported", "unparseable")]
    sup = [r for r in checked
           if verdict_of(r) in ("supported", "partially-supported")]
    print(f"{len(unsup)} unsupported vs {len(sup)} supported/partial")
    for f in SIM_FIELDS:
        xs = [r["retrieval"][f] for r in unsup
              if r["retrieval"].get(f) is not None]
        ys = [r["retrieval"][f] for r in sup
              if r["retrieval"].get(f) is not None]
        if not xs or not ys:
            print(f"  {f}: insufficient data (n={len(xs)} vs {len(ys)})")
            continue
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        print(f"  {f}: unsupported mean {mx:.4f} (n={len(xs)}) vs "
              f"supported mean {my:.4f} (n={len(ys)})  p={ranksum_p(xs, ys)}")


def before_after(after, before):
    print("\n== before/after per category ==")
    print("confounders: BEFORE is single-pass strict prompt; AFTER is "
          "soft-draft + verifier — prompt wording and policy change "
          "together (deliberate: soft-alone is a measured ablation). Same "
          "conv, same models otherwise, separate ingests of the same "
          "sessions (graph state differs run to run).")
    print(f"{'category':<12} {'acc before':>11} {'acc after':>10} "
          f"{'ref before':>11} {'ref after':>10}")
    for cat in sorted({r["category"] for r in after}):
        a = [r for r in after if r["category"] == cat]
        b = [r for r in before if r["category"] == cat]
        acc_a = pct(sum(r.get("correct", False) for r in a), len(a))
        acc_b = pct(sum(r.get("correct", False) for r in b), len(b))
        ref_a = pct(sum(is_refusal(r["pred"]) for r in a), len(a))
        ref_b = pct(sum(is_refusal(r["pred"]) for r in b), len(b))
        print(f"{CATNAME[cat]:<12} {acc_b:>10}% {acc_a:>9}% "
              f"{ref_b:>10}% {ref_a:>9}%")
    nonadv_a = [r for r in after if r["category"] != 5]
    nonadv_b = [r for r in before if r["category"] != 5]
    print(f"non-adversarial refusal: before "
          f"{pct(sum(is_refusal(r['pred']) for r in nonadv_b), len(nonadv_b))}% "
          f"-> after "
          f"{pct(sum(is_refusal(r['pred']) for r in nonadv_a), len(nonadv_a))}%")
    adv_a = [r for r in after if r["category"] == 5]
    if adv_a:
        acc = pct(sum(r.get("correct", False) for r in adv_a), len(adv_a))
        print(f"adversarial accuracy after: {acc}% "
              f"(acceptance gate: >= 85 on a real measurement run)")


def cost(results):
    print("\n== cost ==")
    lat = [r["verifier"]["latency_ms"] for r in results
           if r.get("verifier") and not verdict_of(r) == "draft-refused"
           and "latency_ms" in r["verifier"] and r["verifier"]["latency_ms"] > 0]
    skipped = sum(1 for r in results if verdict_of(r) == "draft-refused")
    print(f"verifier calls: {len(lat)}; drafts self-refused (call skipped): "
          f"{skipped}")
    if lat:
        lat.sort()
        mean = sum(lat) / len(lat)
        p50 = lat[len(lat) // 2]
        p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
        print(f"verifier latency ms: mean {mean:.0f}, p50 {p50}, p95 {p95}")


if __name__ == "__main__":
    after_sfx = (sys.argv[1] if len(sys.argv) > 1
                 else "verified-full-lifecycleverifier-full")
    before_sfx = (sys.argv[2] if len(sys.argv) > 2
                  else "strict-full-lifecycletelemetry-full")
    after = load(0, "memory", after_sfx)["results"]
    print(f"AFTER  conv0-memory-{after_sfx}: {len(after)} questions")
    verdict_profile(after)
    evidence_coupling(after)
    try:
        before = load(0, "memory", before_sfx)["results"]
        print(f"\nBEFORE conv0-memory-{before_sfx}: {len(before)} questions")
        before_after(after, before)
    except FileNotFoundError:
        print(f"\n(no BEFORE file for suffix {before_sfx!r}; skipping "
              "before/after)")
    cost(after)
