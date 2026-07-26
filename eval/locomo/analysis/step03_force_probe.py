"""Step 03 (mind-calibrated-abstention): forced-attempt probe.

Tests the other candidate gate site — the drafter's self-refusal. For every
draft-refused question in the v3 memory arm, against the LIVE standing
corpus: re-run recall (drift cross-checked against recorded telemetry),
force a draft with refusal forbidden, run the v3 verifier on it
(fail-closed), judge BOTH the forced draft and the final prediction.

VERDICT (2026-07-26, conv-0 standing corpus ebcad162): the mechanism is
dead — see STEP03-ABSTENTION-VERDICT.md. The verifier re-silences most
forced drafts (the evidence genuinely is not in the delivered top-10);
forcing recovers ~1/16 wrongful refusals while minting new wrong
assertions. The adversarial guardrail held under maximal forcing pressure
(1/34 leak).

Run (backend venv, memory-tenant env, TENANT_ID=corvus-locomo — see
run_locomo.sh for the sourcing pattern). Output is aggregates only — no
question/gold/draft text. As-run one-shot versions (force_attempt_probe.py,
force_attempt_draftjudge.py) + original artifacts:
~/.corvus-mind/evals/locomo/*-step03-abstention-probes/.
"""
import asyncio
import json
import os
import sys

ANALYSIS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(ANALYSIS_DIR)))
sys.path.insert(0, os.path.join(REPO, "backend"))
sys.path.insert(0, os.path.join(REPO, "eval/locomo"))

os.environ["CODEX_PATH"] = "/nonexistent/locomo-probe-fallback-disabled"

from run_locomo import (  # noqa: E402
    _ANSWER_PROMPT_BASE, ARM_CONFIG, JUDGE_MODEL, JUDGE_PROMPT, VERIFY_MODEL,
    VERIFY_PROMPT, ANSWER_MODEL, REFUSAL_TEXT, is_refusal_text,
    parse_json_block, parse_verdict, recall_hits,
)

RESULTS = os.path.join(REPO, "eval/locomo/results")
V3 = "conv0-memory-verified-full-lifecycleverifier-full-v3.json"
OUT = os.path.join(os.environ.get("STEP03_OUT_DIR", os.getcwd()),
                   "step03_force_probe.json")
SEM = asyncio.Semaphore(2)  # >2 concurrent CLI subprocesses OOM this box

# Forced-attempt rules: the drafter may not refuse; the verifier is the only
# refusal authority on this path.
_FORCE_RULES = """- Commit to the best-supported answer the memories allow, even if support is partial or indirect.
- Never reply "No information available" — if you see no direct answer, state the closest inference the memories support."""
FORCE_PROMPT = _ANSWER_PROMPT_BASE + _FORCE_RULES


async def llm(system_prompt, user_message, model, max_tokens):
    from app.services.llm_provider import llm_chat
    return await llm_chat(system_prompt=system_prompt,
                          user_message=user_message,
                          max_tokens=max_tokens, model=model, timeout=300)


async def judge(question, gold, response):
    reply = await llm(JUDGE_PROMPT,
                      f"QUESTION: {question}\nGOLD: {gold}\nRESPONSE: {response}",
                      JUDGE_MODEL, 50)
    return bool((parse_json_block(reply.get("text", ""), "{", "}") or {})
                .get("correct", False))


async def probe_one(r):
    from app.database import async_session
    async with SEM:
        async with async_session() as db:
            hits, telemetry = await recall_hits(db, r["question"])
        mem = "\n".join(hits) if hits else "(no memories retrieved)"
        draft_reply = await llm(FORCE_PROMPT,
                                f"MEMORIES:\n{mem}\n\nQUESTION: {r['question']}",
                                ANSWER_MODEL, 200)
        draft = draft_reply.get("text", "").strip()
        if is_refusal_text(draft):
            verdict, final = "forced-draft-refused", REFUSAL_TEXT
        else:
            v_reply = await llm(
                VERIFY_PROMPT,
                f"MEMORIES:\n{mem}\n\nQUESTION: {r['question']}\n\nDRAFT: {draft}",
                VERIFY_MODEL, 50)
            verdict = parse_verdict(v_reply.get("text", ""))
            final = (REFUSAL_TEXT if verdict in ("unsupported", "unparseable")
                     else draft)
        rec_t = r.get("retrieval") or {}
        return {
            "category": r["category"],
            "top1_sim_recorded": rec_t.get("top1_sim"),
            "top1_sim_reconstructed": telemetry.get("top1_sim"),
            "sim_top5_mean": rec_t.get("sim_top5_mean"),
            "verdict": verdict,
            "draft_correct": (False if is_refusal_text(draft)
                              else await judge(r["question"], r["gold"], draft)),
            "final_is_refusal": is_refusal_text(final),
            "final_correct": await judge(r["question"], r["gold"], final),
        }


async def main():
    from app.config import settings
    for flag, value in ARM_CONFIG["memory"].items():
        object.__setattr__(settings, flag, value)
    from app.services.actions.init_registry import init_actions_registry
    init_actions_registry()

    with open(os.path.join(RESULTS, V3)) as fh:
        rs = json.load(fh)["results"]
    pool = [r for r in rs
            if (r.get("verifier") or {}).get("verdict") == "draft-refused"]
    print(f"probing {len(pool)} draft-refused questions", flush=True)
    out = await asyncio.gather(*[probe_one(r) for r in pool])
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=2)

    drift = [o for o in out
             if o["top1_sim_recorded"] is not None
             and o["top1_sim_reconstructed"] is not None
             and abs(o["top1_sim_recorded"] - o["top1_sim_reconstructed"]) > 0.005]
    print(f"recall drift >0.005: {len(drift)} of {len(out)}")
    nadv = [o for o in out if o["category"] != 5]
    adv = [o for o in out if o["category"] == 5]
    silenced = [o for o in nadv
                if o["verdict"] in ("unsupported", "unparseable")]
    print(f"non-adv ({len(nadv)}): forced drafts correct "
          f"{sum(o['draft_correct'] for o in nadv)}; final correct "
          f"{sum(o['final_correct'] for o in nadv)}; re-silenced "
          f"{len(silenced)} (of which draft was correct: "
          f"{sum(o['draft_correct'] for o in silenced)})")
    print(f"adv ({len(adv)}): points LOST (fabrication waved through): "
          f"{sum(not o['final_correct'] for o in adv)}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
