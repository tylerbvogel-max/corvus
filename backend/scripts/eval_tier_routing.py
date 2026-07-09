"""Eval evidence for tier-elastic escalation routing (arch-tier-routing).

Two phases:

1. Decision sweep ($0, embed-only): run prepare_context + decide_tier_escalation
   over the smoke suite + a sample of live queries. Reports the escalation
   rate, per-signal trigger counts, and the expected cost per answer vs the
   all-haiku and all-sonnet baselines.

2. Quality-holds check (LLM, optional): for questions routing KEEPS on haiku,
   generate both the haiku answer (what production now serves) and the sonnet
   answer (the counterfactual ceiling) at production settings (priming +
   primary effort floor), then judge pairwise with a counterbalanced sonnet
   judge. Routing holds quality iff haiku ties-or-wins on most of the stay
   set — the escalated set gets sonnet by construction, so no check needed.

Run inside the backend venv:
    TENANT_ID=corvus-aero python scripts/eval_tier_routing.py --live-sample 40 --quality-n 8
    TENANT_ID=corvus-aero python scripts/eval_tier_routing.py --skip-llm   # phase 1 only
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from types import MappingProxyType

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text as sa_text

# Per-answer costs measured in the 2026-07-08 3-mode eval (post tools-off
# stack, corrected Anthropic prices) — refreshed with realized costs when
# phase 2 runs.
MEASURED_COST = MappingProxyType({"haiku": 0.019, "sonnet": 0.052})

JUDGE_SYSTEM = (
    "You are judging two answers to an aerospace organization's internal "
    "knowledge question. Prefer the answer that is more accurate, complete, "
    "and directly useful to a practitioner; penalize invented specifics. "
    'Respond with ONLY a JSON object: {"verdict": "A"|"B"|"tie", "reason": "<one line>"}'
)

# Same rubric as the 2026-07-08 3-mode eval that produced the routing
# evidence (haiku 4.4 / sonnet 4.7 overall) — keeps the stay-set adequacy
# scores comparable to the measured baselines.
RUBRIC_SYSTEM = (
    "You are grading one answer to an aerospace organization's internal "
    "knowledge question. Score 1-5 on: accuracy (factually correct), "
    "completeness (covers what the question needs), clarity, and faithfulness "
    "(no invented specifics beyond the provided context). Respond with ONLY a "
    'JSON object: {"accuracy": n, "completeness": n, "clarity": n, "faithfulness": n}'
)


async def _load_questions(live_sample: int) -> list[str]:
    """Smoke suite + up to live_sample distinct real user queries."""
    from app.database import async_session
    from app.services.eval_runs import load_suite

    questions = [c.text for c in load_suite("smoke").cases]
    async with async_session() as db:
        rows = await db.execute(sa_text("""
            SELECT DISTINCT ON (user_message) user_message
            FROM queries
            WHERE length(user_message) < 400
              AND user_message NOT LIKE '[Conversation%%'
              AND user_message NOT LIKE '[Knowledge%%'
            ORDER BY user_message, id DESC
            LIMIT :n
        """), {"n": live_sample})
        for (msg,) in rows.all():
            if msg not in questions:
                questions.append(msg)
    assert questions, "no questions loaded"
    return questions


async def _decide(question: str) -> dict:
    """One prep pass ($0) + routing decision for a question."""
    from app.database import async_session
    from app.services.executor import prepare_context
    from app.services.tier_routing import decide_tier_escalation

    async with async_session() as db:
        ctx = await prepare_context(db, question)
    decision = decide_tier_escalation(ctx, question)
    return {"question": question, "ctx": ctx, **decision.to_payload()}


def _cost_report(decisions: list[dict]) -> dict:
    """Expected per-answer cost under routing vs the flat baselines."""
    assert decisions, "cost report needs at least one decision"
    n = len(decisions)
    esc = sum(1 for d in decisions if d["escalated"])
    expected = ((n - esc) * MEASURED_COST["haiku"] + esc * MEASURED_COST["sonnet"]) / n
    reasons: dict[str, int] = {}
    for d in decisions:
        for r in d["reasons"]:
            reasons[r] = reasons.get(r, 0) + 1
    return {
        "n": n,
        "escalated": esc,
        "escalation_rate": round(esc / n, 3),
        "reason_counts": reasons,
        "expected_cost_routed": round(expected, 4),
        "cost_all_haiku": MEASURED_COST["haiku"],
        "cost_all_sonnet": MEASURED_COST["sonnet"],
        "saving_vs_all_sonnet": round(1 - expected / MEASURED_COST["sonnet"], 3),
    }


async def _answer(ctx, question: str, model: str) -> dict:
    """One production-shaped answer: primed context, primary effort floor."""
    from app.config import settings
    from app.services.executor import _primed_ctx
    from app.services.llm_provider import effort_var, llm_chat

    effort_var.set(settings.primary_answer_effort or settings.default_effort)
    primed = _primed_ctx(ctx)
    result = await llm_chat(
        system_prompt=primed.system_prompt, user_message=question,
        max_tokens=4096, model=model,
    )
    assert result.get("text"), f"{model} returned an empty answer"
    return {"text": result["text"], "cost_usd": result.get("cost_usd", 0.0)}


async def _judge_once(question: str, first: str, second: str) -> str:
    """One judge pass; returns 'first' | 'second' | 'tie'."""
    from app.services.llm_provider import llm_chat

    prompt = (
        f"Question:\n{question}\n\n--- Answer A ---\n{first}\n\n"
        f"--- Answer B ---\n{second}\n\nWhich answer is better?"
    )
    result = await llm_chat(
        system_prompt=JUDGE_SYSTEM, user_message=prompt,
        max_tokens=200, model="sonnet",
    )
    match = re.search(r'"verdict"\s*:\s*"(A|B|tie)"', result.get("text", ""))
    if not match:
        return "tie"
    return {"A": "first", "B": "second", "tie": "tie"}[match.group(1)]


async def _judge_pair(question: str, haiku_text: str, sonnet_text: str) -> str:
    """Counterbalanced pairwise verdict: 'haiku' | 'sonnet' | 'tie'.

    Two passes with the answers swapped; disagreement on WHO won (both passes
    picking the same position) is position bias and counts as a tie.
    """
    fwd = await _judge_once(question, haiku_text, sonnet_text)   # first=haiku
    rev = await _judge_once(question, sonnet_text, haiku_text)   # first=sonnet
    fwd_winner = {"first": "haiku", "second": "sonnet", "tie": "tie"}[fwd]
    rev_winner = {"first": "sonnet", "second": "haiku", "tie": "tie"}[rev]
    if fwd_winner == rev_winner:
        return fwd_winner
    if "tie" in (fwd_winner, rev_winner):
        return fwd_winner if rev_winner == "tie" else rev_winner
    return "tie"


async def _rubric_score(question: str, answer: str) -> dict | None:
    """Absolute rubric grade (sonnet judge) for one answer; None on parse miss."""
    from app.services.llm_provider import llm_chat

    result = await llm_chat(
        system_prompt=RUBRIC_SYSTEM,
        user_message=f"Question:\n{question}\n\n--- Answer ---\n{answer}",
        max_tokens=200, model="sonnet",
    )
    match = re.search(r"\{[^{}]*\}", result.get("text", ""))
    if not match:
        return None
    try:
        scores = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    keys = ("accuracy", "completeness", "clarity", "faithfulness")
    if not all(isinstance(scores.get(k), (int, float)) for k in keys):
        return None
    scores["overall"] = round(sum(scores[k] for k in keys) / len(keys), 2)
    return scores


async def _rubric_check(stay: list[dict], quality_n: int) -> dict:
    """Absolute adequacy of the routed (haiku) answer on the stay set, on the
    SAME rubric as the 2026-07-08 baselines — pairwise preference alone can't
    show adequacy (a judge may prefer sonnet even when haiku suffices)."""
    assert quality_n > 0, "quality_n must be positive"
    rows: list[dict] = []
    for d in stay[:quality_n]:
        q = d["question"]
        haiku = await _answer(d["ctx"], q, "haiku")
        scores = await _rubric_score(q, haiku["text"])
        rows.append({"question": q[:90], "scores": scores})
        head = scores["overall"] if scores else "parse-miss"
        print(f"  [rubric {head}] {q[:66]}", file=sys.stderr, flush=True)
    graded = [r["scores"] for r in rows if r["scores"]]
    means = {}
    for k in ("accuracy", "completeness", "clarity", "faithfulness", "overall"):
        vals = [g[k] for g in graded]
        means[k] = round(sum(vals) / len(vals), 2) if vals else None
    return {"n": len(rows), "graded": len(graded), "means": means, "rows": rows}


async def _quality_check(stay: list[dict], quality_n: int) -> dict:
    """Haiku-vs-sonnet judged head-to-head on the stay-on-haiku set."""
    assert quality_n > 0, "quality_n must be positive"
    verdicts: list[dict] = []
    costs = {"haiku": [], "sonnet": []}
    for d in stay[:quality_n]:
        q = d["question"]
        haiku = await _answer(d["ctx"], q, "haiku")
        sonnet = await _answer(d["ctx"], q, "sonnet")
        verdict = await _judge_pair(q, haiku["text"], sonnet["text"])
        costs["haiku"].append(haiku["cost_usd"])
        costs["sonnet"].append(sonnet["cost_usd"])
        verdicts.append({"question": q[:90], "verdict": verdict})
        print(f"  [{verdict:^6}] {q[:70]}", file=sys.stderr, flush=True)
    wins = sum(1 for v in verdicts if v["verdict"] == "haiku")
    ties = sum(1 for v in verdicts if v["verdict"] == "tie")
    return {
        "n": len(verdicts),
        "haiku_wins": wins,
        "ties": ties,
        "sonnet_wins": len(verdicts) - wins - ties,
        "haiku_holds_rate": round((wins + ties) / len(verdicts), 3) if verdicts else None,
        "realized_cost_haiku": round(sum(costs["haiku"]) / len(costs["haiku"]), 4) if costs["haiku"] else None,
        "realized_cost_sonnet": round(sum(costs["sonnet"]) / len(costs["sonnet"]), 4) if costs["sonnet"] else None,
        "verdicts": verdicts,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-sample", type=int, default=40)
    parser.add_argument("--quality-n", type=int, default=8)
    parser.add_argument("--skip-llm", action="store_true")
    parser.add_argument("--skip-pairwise", action="store_true",
                        help="skip the pairwise haiku-vs-sonnet phase (rubric-only runs)")
    parser.add_argument("--rubric", action="store_true",
                        help="also rubric-grade the routed haiku answers on the stay set")
    parser.add_argument("--out", type=str, default="")
    args = parser.parse_args()

    questions = await _load_questions(args.live_sample)
    print(f"Phase 1: routing decisions over {len(questions)} questions", file=sys.stderr)
    decisions = []
    for q in questions:
        d = await _decide(q)
        decisions.append(d)
        mark = "ESC " + ",".join(d["reasons"]) if d["escalated"] else "stay"
        print(f"  [{mark:<32}] {q[:60]}", file=sys.stderr, flush=True)

    report = {"cost": _cost_report(decisions)}
    if not args.skip_llm:
        stay = [d for d in decisions if not d["escalated"]]
        if not args.skip_pairwise:
            print(f"Phase 2: quality check on {min(args.quality_n, len(stay))} stay-set questions", file=sys.stderr)
            report["quality"] = await _quality_check(stay, args.quality_n)
        if args.rubric:
            print("Phase 3: rubric adequacy of routed answers on the stay set", file=sys.stderr)
            report["rubric"] = await _rubric_check(stay, args.quality_n)

    report["decisions"] = [
        {k: v for k, v in d.items() if k != "ctx"} for d in decisions
    ]
    payload = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(payload)
        print(f"report written to {args.out}", file=sys.stderr)
    print(payload)


if __name__ == "__main__":
    asyncio.run(main())
