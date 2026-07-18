"""Repeated fixed-corpus LoCoMo confirmation triangle.

This is an evaluation runner, not a pytest test. It never ingests or mutates
the LoCoMo graph. Every Anthropic call routes through ``llm_provider``.

Run from backend/:
  TENANT_ID=corvus-mind DATABASE_URL=postgresql+asyncpg://yggdrasil:yggdrasil@localhost:5432/corvus_locomo \
    PYTHONPATH=. venv/bin/python \
    tests/benchmarks/eval_token_assembly_triangle.py \
    --data /tmp/locomo10.json --output ~/.corvus-mind/evals/token-assembly
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select


ANSWER_MODEL = os.environ.get("LOCOMO_ANSWER_MODEL", "sonnet")
JUDGE_MODEL = os.environ.get("LOCOMO_JUDGE_MODEL", "sonnet")
CONCURRENCY = int(os.environ.get("LOCOMO_CONCURRENCY", "2"))
CATEGORY_NAMES = {
    1: "multi-hop", 2: "temporal", 3: "open-domain",
    4: "single-hop", 5: "adversarial",
}

ANSWER_PROMPT = """You answer questions from a personal long-term memory system.

You are given MEMORIES retrieved for the question. Answer using ONLY these memories.
- Be concise: a short phrase or sentence, no preamble.
- For date questions, give the specific date (e.g. "7 May 2023").
- If the memories do not contain the answer, reply exactly: No information available."""

JUDGE_PROMPT = """You are grading a question-answering system against a gold answer.

Decide whether the RESPONSE is factually consistent with the GOLD answer for the QUESTION. Paraphrases, reworded dates (e.g. "May 7, 2023" vs "7 May 2023"), and answers containing the gold plus extra correct detail are CORRECT. Missing the key fact, contradicting it, or answering a different question is WRONG.

Special case — unanswerable questions: if GOLD is "No information available", the response is CORRECT only if it states the information is unavailable/unknown (any phrasing), and WRONG if it asserts a substantive answer.

Respond with ONLY a JSON object: {"correct": true} or {"correct": false}"""

ARM_CONFIG = {
    "inhibited-low": {"token_mode": False, "memory_budget": None},
    "token-3k": {"token_mode": True, "memory_budget": 3000},
    "zep-5760": {"token_mode": True, "memory_budget": 5760},
}


def _parse_json_object(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        value = json.loads(text[start:end + 1])
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def _gold(qa: dict) -> str:
    return "No information available" if qa.get("category") == 5 else str(qa.get("answer", ""))


def _total_input(usage: dict) -> int:
    return int(usage.get("input_tokens") or 0) + int(
        usage.get("cache_creation_tokens") or 0
    ) + int(usage.get("cache_read_tokens") or 0)


async def _llm_retry(**kwargs) -> dict:
    from app.services.llm_provider import llm_chat

    # eval:* workloads are excluded from the maintenance cost report —
    # benchmark spend is the operator's burden, not system running cost.
    kwargs.setdefault("workload", "eval:token-assembly-confirmation")
    delay = 15
    last_error: Exception | None = None
    for attempt in range(8):
        try:
            return await llm_chat(**kwargs)
        except (AssertionError, RuntimeError, ValueError, OSError) as exc:
            last_error = exc
            print(
                f"[llm-retry] attempt {attempt + 1}/8: {type(exc).__name__}: {str(exc)[:200]}",
                flush=True,
            )
            if attempt == 7:
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120)
    raise RuntimeError(f"LLM unavailable after retries: {last_error}") from last_error


async def _corpus_receipt() -> dict:
    from app.database import async_session
    from app.models import Neuron

    async with async_session() as db:
        rows = (await db.execute(
            select(Neuron.id, Neuron.label, Neuron.content, Neuron.is_active)
            .order_by(Neuron.id)
        )).all()
    active = [row for row in rows if row.is_active]
    digest = hashlib.sha256()
    for row in active:
        digest.update(f"{row.id}\0{row.label}\0{row.content or ''}\n".encode("utf-8"))
    return {
        "active_neurons": len(active),
        "total_neurons": len(rows),
        "sha256": digest.hexdigest(),
        "first_id": active[0].id if active else None,
        "last_id": active[-1].id if active else None,
    }


def _configure_arm(arm: str) -> None:
    from app.config import settings

    cfg = ARM_CONFIG[arm]
    object.__setattr__(settings, "token_bounded_assembly_enabled", cfg["token_mode"])
    object.__setattr__(settings, "inhibition_enabled", True)
    object.__setattr__(settings, "memory_candidate_limit", 150)
    object.__setattr__(settings, "memory_max_delivered_neurons", 250)
    if cfg["memory_budget"] is not None:
        object.__setattr__(settings, "memory_context_token_budget", cfg["memory_budget"])
    object.__setattr__(settings, "keyword_lane_enabled", True)
    object.__setattr__(settings, "entity_lane_enabled", True)
    object.__setattr__(settings, "hybrid_relevance_enabled", True)
    object.__setattr__(settings, "spread_enabled", True)


def _legacy_memory_text(ctx) -> str:
    """Render low-arm survivors in the same production block shape."""
    from app.services.memory_assembly import render_memory_entry

    entries = []
    for index, score in enumerate(ctx.all_scored):
        neuron = ctx.neuron_map.get(score.neuron_id)
        if neuron is None:
            continue
        entries.append(render_memory_entry(
            score, neuron, f"FQ-{index + 1:06X}", "full"))
    return "\n\n".join(entries)


async def _answer_one(qa: dict, arm: str, semaphore: asyncio.Semaphore) -> dict:
    from app.database import async_session
    from app.services.executor import prepare_context

    async with semaphore:
        async with async_session() as db:
            ctx = await prepare_context(
                db, qa["question"], top_k=300, token_budget=8000,
                recall_mode="cheap",
            )
        memory_text = (
            ctx.memory_context_text if ARM_CONFIG[arm]["token_mode"]
            else _legacy_memory_text(ctx)
        )
        reply = await _llm_retry(
            system_prompt=ANSWER_PROMPT,
            user_message=f"MEMORIES:\n{memory_text or '(no memories retrieved)'}\n\nQUESTION: {qa['question']}",
            max_tokens=200, model=ANSWER_MODEL, timeout=300,
        )
        return {
            "question": qa["question"],
            "category": qa["category"],
            "gold": _gold(qa),
            "pred": (reply.get("text") or "").strip(),
            "candidates_considered": ctx.candidates_considered,
            "neurons_activated": ctx.neurons_activated,
            "neurons_delivered": ctx.neurons_delivered,
            "estimated_memory_tokens": ctx.estimated_memory_tokens,
            "memory_chars": ctx.memory_context_chars,
            "memory_utf8_bytes": ctx.memory_context_utf8_bytes,
            "memory_budget": ctx.memory_token_budget,
            "stop_reason": ctx.assembly_stop_reason,
            "redundancy_suppressed": ctx.redundancy_suppressed,
            "recall_latency_ms": ctx.recall_latency_ms,
            "answer_input_tokens": _total_input(reply),
            "answer_output_tokens": int(reply.get("output_tokens") or 0),
            "answer_cost_usd": float(reply.get("cost_usd") or 0),
        }


async def _judge_one(row: dict, semaphore: asyncio.Semaphore) -> None:
    async with semaphore:
        reply = {}
        verdict = {}
        for parse_attempt in range(3):
            reply = await _llm_retry(
                system_prompt=JUDGE_PROMPT,
                user_message=(
                    f"QUESTION: {row['question']}\nGOLD: {row['gold']}\n"
                    f"RESPONSE: {row['pred']}"
                ),
                max_tokens=50, model=JUDGE_MODEL, timeout=240,
            )
            verdict = _parse_json_object(reply.get("text") or "")
            if isinstance(verdict.get("correct"), bool):
                break
            print(f"[judge-retry] malformed verdict attempt {parse_attempt + 1}/3", flush=True)
        if not isinstance(verdict.get("correct"), bool):
            raise ValueError("judge returned no boolean 'correct' after 3 attempts")
        row["correct"] = bool(verdict.get("correct", False))
        row["judge_input_tokens"] = _total_input(reply)
        row["judge_output_tokens"] = int(reply.get("output_tokens") or 0)
        row["judge_cost_usd"] = float(reply.get("cost_usd") or 0)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return round(float(ordered[index]), 2)


def _score(rows: list[dict]) -> dict:
    per_category = {}
    for category in sorted({row["category"] for row in rows}):
        subset = [row for row in rows if row["category"] == category]
        per_category[CATEGORY_NAMES[category]] = round(
            100 * sum(row["correct"] for row in subset) / len(subset), 2)
    wrong = [row for row in rows if not row["correct"]]
    refusals = sum(
        re.sub(r"[^a-z]+$", "", row["pred"].strip().lower())
        == "no information available"
        for row in wrong
    )
    return {
        "n": len(rows),
        "overall": round(100 * sum(row["correct"] for row in rows) / len(rows), 2),
        "per_category": per_category,
        "wrong": len(wrong),
        "wrong_refusal": refusals,
        "wrong_substantive": len(wrong) - refusals,
        "mean_candidates": round(statistics.mean(row["candidates_considered"] for row in rows), 2),
        "mean_activated": round(statistics.mean(row["neurons_activated"] for row in rows), 2),
        "mean_delivered": round(statistics.mean(row["neurons_delivered"] for row in rows), 2),
        "mean_estimated_memory_tokens": round(statistics.mean(row["estimated_memory_tokens"] for row in rows), 2),
        "mean_observed_answer_input_tokens": round(statistics.mean(row["answer_input_tokens"] for row in rows), 2),
        # The provider reports whole-request input, not an authoritative
        # memory-only token count. Keep this signed comparison explicitly
        # named as a cross-measurement delta rather than an exact tokenizer
        # error for the memory packet.
        "mean_observed_total_minus_estimated_memory_tokens": round(statistics.mean(
            row["answer_input_tokens"] - row["estimated_memory_tokens"]
            for row in rows
        ), 2),
        "answer_cost_usd": round(sum(row["answer_cost_usd"] for row in rows), 4),
        "judge_cost_usd": round(sum(row["judge_cost_usd"] for row in rows), 4),
        "recall_latency_ms": {
            "p50": _percentile([row["recall_latency_ms"] for row in rows], 0.50),
            "p95": _percentile([row["recall_latency_ms"] for row in rows], 0.95),
            "p99": _percentile([row["recall_latency_ms"] for row in rows], 0.99),
        },
        "stop_reasons": dict(sorted({
            reason: sum(row["stop_reason"] == reason for row in rows)
            for reason in {row["stop_reason"] for row in rows}
        }.items())),
    }


def _paired(candidate: list[dict], comparator: list[dict]) -> dict:
    by_question = {row["question"]: row for row in comparator}
    gains, losses = [], []
    category_flips: dict[str, dict[str, int]] = defaultdict(lambda: {"gains": 0, "losses": 0})
    for row in candidate:
        other = by_question[row["question"]]
        category = CATEGORY_NAMES[row["category"]]
        if row["correct"] and not other["correct"]:
            gains.append(row["question"])
            category_flips[category]["gains"] += 1
        elif other["correct"] and not row["correct"]:
            losses.append(row["question"])
            category_flips[category]["losses"] += 1
    return {
        "gains": len(gains), "losses": len(losses), "net": len(gains) - len(losses),
        "category_flips": dict(category_flips),
        "gain_questions": gains, "loss_questions": losses,
    }


async def _run_arm(
    qas: list[dict], arm: str, repeat: int, path: Path, corpus: dict,
) -> dict:
    existing = None
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("status") == "complete" and existing.get("corpus") == corpus:
            print(f"[resume] {arm} repeat {repeat}: complete", flush=True)
            return existing

    _configure_arm(arm)
    if (existing and existing.get("status") in {"answers_complete", "judging"}
            and existing.get("corpus") == corpus
            and len(existing.get("results") or []) == len(qas)):
        rows = existing["results"]
        print(f"[resume] {arm} repeat {repeat}: reuse {len(rows)} answers", flush=True)
    else:
        semaphore = asyncio.Semaphore(CONCURRENCY)
        answer_count = 0

        async def tracked_answer(qa: dict) -> dict:
            nonlocal answer_count
            row = await _answer_one(qa, arm, semaphore)
            answer_count += 1
            if answer_count % 20 == 0 or answer_count == len(qas):
                print(f"[answer-progress] {arm} r{repeat}: {answer_count}/{len(qas)}", flush=True)
            return row

        rows = list(await asyncio.gather(*[tracked_answer(qa) for qa in qas]))
    partial = {
        "status": "answers_complete", "arm": arm, "repeat": repeat,
        "models": {"answer": ANSWER_MODEL, "judge": JUDGE_MODEL},
        "strict_prompt": True, "corpus": corpus, "results": rows,
    }
    if not existing or existing.get("status") not in {"answers_complete", "judging"}:
        path.write_text(json.dumps(partial, indent=2), encoding="utf-8")
        print(f"[answers] {arm} repeat {repeat}: {len(rows)}", flush=True)

    judge_semaphore = asyncio.Semaphore(CONCURRENCY)
    judge_count = sum(isinstance(row.get("correct"), bool) for row in rows)
    pending_rows = [row for row in rows if not isinstance(row.get("correct"), bool)]
    if judge_count:
        print(f"[resume] {arm} repeat {repeat}: {judge_count}/{len(rows)} judges", flush=True)

    async def tracked_judge(row: dict) -> None:
        nonlocal judge_count
        await _judge_one(row, judge_semaphore)
        judge_count += 1
        if judge_count % 20 == 0 or judge_count == len(rows):
            partial["status"] = "judging"
            partial["results"] = rows
            path.write_text(json.dumps(partial, indent=2), encoding="utf-8")
            print(f"[judge-progress] {arm} r{repeat}: {judge_count}/{len(rows)}", flush=True)

    await asyncio.gather(*[tracked_judge(row) for row in pending_rows])
    payload = {
        **partial,
        "status": "complete",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "scores": _score(rows),
        "results": rows,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[scored] {arm} repeat {repeat}: {payload['scores']}", flush=True)
    return payload


def _write_summary(
    runs: dict[str, dict], metadata: dict, output: Path, repeats: int,
) -> dict:
    paired = {}
    for repeat in range(1, repeats + 1):
        candidate = runs[f"token-3k-r{repeat}"]["results"]
        paired[f"r{repeat}-vs-low"] = _paired(
            candidate, runs[f"inhibited-low-r{repeat}"]["results"])
        paired[f"r{repeat}-vs-zep"] = _paired(
            candidate, runs[f"zep-5760-r{repeat}"]["results"])
    summary = {
        **metadata,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "runs": {key: value["scores"] for key, value in runs.items()},
        "paired": paired,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    return summary


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument(
        "--summarize-only", action="store_true",
        help="Recompute score/refusal/paired summaries from completed checkpoints",
    )
    args = parser.parse_args()
    from app.config import settings
    assert "corvus_locomo" in settings.database_url, \
        "DATABASE_URL must point to the throwaway corvus_locomo DB"
    assert args.repeats >= 2, "promotion gate requires at least two repeats"

    data_bytes = args.data.read_bytes()
    dataset = json.loads(data_bytes)
    qas = dataset[0]["qa"]
    assert len(qas) == 199, f"expected conv0's 199 questions, got {len(qas)}"
    corpus = await _corpus_receipt()
    assert corpus["active_neurons"] == corpus["total_neurons"] == 300, corpus

    args.output.mkdir(parents=True, exist_ok=True)
    if args.summarize_only:
        metadata_path = args.output / "metadata.json"
        assert metadata_path.exists(), "metadata checkpoint is missing"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert metadata["dataset_sha256"] == hashlib.sha256(data_bytes).hexdigest()
        assert metadata["corpus"] == corpus
        runs: dict[str, dict] = {}
        for repeat in range(1, args.repeats + 1):
            for arm in ARM_CONFIG:
                key = f"{arm}-r{repeat}"
                path = args.output / f"{key}.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                assert payload.get("status") == "complete", f"{key} is incomplete"
                assert payload.get("corpus") == corpus, f"{key} corpus changed"
                payload["scores"] = _score(payload["results"])
                path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
                runs[key] = payload
        print(json.dumps(
            _write_summary(runs, metadata, args.output, args.repeats), indent=2,
        ), flush=True)
        return

    metadata = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "dataset_sha256": hashlib.sha256(data_bytes).hexdigest(),
        "question_count": len(qas),
        "corpus": corpus,
        "models": {"answer": ANSWER_MODEL, "judge": JUDGE_MODEL},
        "strict_prompt": True,
        "concurrency": CONCURRENCY,
    }
    if args.validate_only:
        print(json.dumps(metadata, indent=2), flush=True)
        return
    (args.output / "metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8")

    runs: dict[str, dict] = {}
    for repeat in range(1, args.repeats + 1):
        for arm in ARM_CONFIG:
            key = f"{arm}-r{repeat}"
            runs[key] = await _run_arm(
                qas, arm, repeat, args.output / f"{key}.json", corpus)
            assert await _corpus_receipt() == corpus, "fixed corpus changed during triangle"

    summary = _write_summary(runs, metadata, args.output, args.repeats)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
