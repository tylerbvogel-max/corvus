"""Step 03 (mind-calibrated-abstention): conversion-gate sweep.

Tests the Step 02 carry-forward hypothesis — condition the verifier's
unsupported->refusal CONVERSION on raw retrieval sims (release the draft
when sims are strong). Judges every converted draft against gold with the
harness judge (same JUDGE_PROMPT + judge model, CLI provider path), caches
the judgments, then sweeps release thresholds over top1_sim and
sim_top5_mean and prints the non-adversarial-gain vs adversarial-loss curve.

VERDICT (2026-07-26, conv-0 standing corpus ebcad162): FALSIFIED — see
STEP03-ABSTENTION-VERDICT.md. Correct-but-silenced drafts sit on WEAKER
sims than rightly-silenced wrong drafts; best net is +2/199 while turning
wrong-refusals into wrong-assertions.

Run (backend venv, memory-tenant env, TENANT_ID=corvus-locomo — see
run_locomo.sh for the sourcing pattern). LLM calls: ~1 judge call per
converted draft, cached to step03_draft_judgments.json (aggregates only —
no question/gold/draft text is written out). As-run one-shot versions +
original artifacts: ~/.corvus-mind/evals/locomo/*-step03-abstention-probes/.
"""
import asyncio
import json
import os
import sys

ANALYSIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(ANALYSIS_DIR)))
sys.path.insert(0, os.path.join(REPO, "backend"))
sys.path.insert(0, os.path.join(REPO, "eval/locomo"))

from run_locomo import JUDGE_MODEL, JUDGE_PROMPT, parse_json_block  # noqa: E402

RESULTS = os.path.join(REPO, "eval/locomo/results")
OUT_DIR = os.environ.get("STEP03_OUT_DIR", os.getcwd())
CACHE = os.path.join(OUT_DIR, "step03_draft_judgments.json")
ARMS = {
    "v2": "conv0-memory-verified-full-lifecycleverifier-full-v2.json",
    "v3": "conv0-memory-verified-full-lifecycleverifier-full-v3.json",
}
SIM_FIELDS = ("top1_sim", "sim_top5_mean")
SEM = asyncio.Semaphore(2)  # >2 concurrent CLI subprocesses OOM this box


async def judge_one(r):
    from app.services.llm_provider import llm_chat
    async with SEM:
        reply = await llm_chat(
            system_prompt=JUDGE_PROMPT,
            user_message=(f"QUESTION: {r['question']}\nGOLD: {r['gold']}\n"
                          f"RESPONSE: {r['draft']}"),
            max_tokens=50, model=JUDGE_MODEL, timeout=240,
        )
    verdict = parse_json_block(reply.get("text", ""), "{", "}") or {}
    t = r.get("retrieval") or {}
    return {"category": r["category"],
            "top1_sim": t.get("top1_sim"),
            "sim_top5_mean": t.get("sim_top5_mean"),
            "draft_correct": bool(verdict.get("correct", False))}


async def build_judgments():
    out = {}
    for arm, fname in ARMS.items():
        with open(os.path.join(RESULTS, fname)) as fh:
            rs = json.load(fh)["results"]
        pool = [r for r in rs
                if (r.get("verifier") or {}).get("verdict") == "unsupported"
                and r.get("draft")]
        out[arm] = await asyncio.gather(*[judge_one(r) for r in pool])
    with open(CACHE, "w") as fh:
        json.dump(out, fh, indent=2)
    return out


def sweep(judgments):
    for arm, js in judgments.items():
        nadv = [j for j in js if j["category"] != 5]
        adv = [j for j in js if j["category"] == 5]
        ok = [j for j in nadv if j["draft_correct"]]
        bad = [j for j in nadv if not j["draft_correct"]]
        print(f"\n=== {arm}: {len(js)} conversions "
              f"({len(nadv)} non-adv, {len(adv)} adv) ===")
        print(f"non-adv drafts judged CORRECT (silenced right answers): "
              f"{len(ok)}/{len(nadv)}")
        for f in SIM_FIELDS:
            mean = lambda g: sum(j[f] for j in g) / len(g) if g else float("nan")
            print(f"  {f} means — correct-silenced {mean(ok):.3f} / "
                  f"wrong-silenced {mean(bad):.3f} / adversarial {mean(adv):.3f}")
        for f in SIM_FIELDS:
            print(f"\n  release conversion when {f} >= t   "
                  f"(gain = correct drafts released; adv_loss = correct "
                  f"refusals released; wrong_assert = wrong drafts released)")
            print("    t      released  gain  adv_loss  wrong_assert   net")
            for t in sorted({round(j[f], 3) for j in js}):
                rel = [j for j in js if j[f] >= t]
                gain = sum(1 for j in rel
                           if j["category"] != 5 and j["draft_correct"])
                loss = sum(1 for j in rel if j["category"] == 5)
                wrong = sum(1 for j in rel
                            if j["category"] != 5 and not j["draft_correct"])
                print(f"    {t:.3f}  {len(rel):>5}     {gain:>4}  {loss:>7}"
                      f"  {wrong:>11}   {gain - loss:>+4}")


async def main():
    if os.path.exists(CACHE):
        with open(CACHE) as fh:
            judgments = json.load(fh)
        print(f"using cached judgments: {CACHE}")
    else:
        judgments = await build_judgments()
    sweep(judgments)


if __name__ == "__main__":
    asyncio.run(main())
