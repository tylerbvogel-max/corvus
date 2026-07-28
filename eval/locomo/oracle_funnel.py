"""Deterministic per-question loss attribution for the LoCoMo harness.

The funnel observes the exact PreparedContext used by the current answer
path. It never changes retrieval, assembly, answering, verification, or
judging. LoCoMo's gold dialogue ids make these stages inspectable:

    ingest -> candidate -> rank -> assembly -> synthesis -> judge -> success

Category-5 adversarial questions are excluded because their success condition
is refusal rather than recovery of a gold fact.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

CANDIDATE_PROBE_K = 100
OVERLAP_THRESHOLD = 0.5

_STOPWORDS = frozenset("""
a an and are as at be been but by did do does for from had has have he her
hers him his i if in into is it its me my not of on or our she so that the
their them they this to was we were what when where which who will with you
your about
""".split())
_DIA_ID_RE = re.compile(r"D(\d+)\s*:\s*(\d+)")
_SESSION_CITE_RE = re.compile(r"session (\d+) ")

STAGE_ORDER = (
    "ingest", "candidate", "rank", "assembly",
    "synthesis", "judge", "success",
)


def _norm(text: str) -> str:
    text = re.sub(r"[^\w\s]", " ", (text or "").lower())
    return re.sub(r"\s+", " ", text).strip()


def _content_words(text: str) -> set[str]:
    return {
        word for word in _norm(text).split()
        if word not in _STOPWORDS and len(word) > 2
    }


def evidence_sessions(qa: dict) -> set[int]:
    sessions: set[int] = set()
    for evidence in qa.get("evidence") or []:
        match = _DIA_ID_RE.search(str(evidence))
        if match:
            sessions.add(int(match.group(1)))
    return sessions


def evidence_turn_texts(conv: dict, qa: dict) -> list[tuple[int, str]]:
    wanted: set[str] = set()
    for evidence in qa.get("evidence") or []:
        match = _DIA_ID_RE.search(str(evidence))
        if match:
            wanted.add(f"D{match.group(1)}:{match.group(2)}")
    if not wanted:
        return []

    turns: list[tuple[int, str]] = []
    conversation = conv["conversation"]
    session_number = 1
    while f"session_{session_number}" in conversation:
        for turn in conversation[f"session_{session_number}"]:
            raw_id = str(turn.get("dia_id", ""))
            match = _DIA_ID_RE.search(raw_id)
            key = f"D{match.group(1)}:{match.group(2)}" if match else raw_id
            if key not in wanted:
                continue
            text = turn.get("text", "")
            if turn.get("blip_caption"):
                text = f"{text} {turn['blip_caption']}"
            turns.append((session_number, text))
        session_number += 1
    return turns


def _neuron_session(citation: str | None) -> int | None:
    match = _SESSION_CITE_RE.search(citation or "")
    return int(match.group(1)) if match else None


class OracleIndex:
    """Active graph rows reduced to the fields required by the gold matcher."""

    def __init__(self, rows: list[tuple[int, str, str, str]]):
        self.neurons = []
        for neuron_id, label, content, citation in rows:
            fact_text = (content or "").split("Evidence:")[0]
            combined = f"{label or ''} {fact_text}"
            self.neurons.append({
                "id": neuron_id,
                "text_norm": _norm(combined),
                "words": _content_words(combined),
                "session": _neuron_session(citation),
            })

    @classmethod
    async def load(cls, db) -> "OracleIndex":
        from sqlalchemy import text

        rows = (await db.execute(text(
            "SELECT id, label, content, citation FROM neurons "
            "WHERE is_active IS TRUE"
        ))).all()
        return cls([(row[0], row[1], row[2], row[3]) for row in rows])

    def encoding_neurons(self, qa: dict, conv: dict) -> list[int]:
        gold = _norm(str(qa.get("answer", "")))
        gold_usable = len(gold) >= 3
        turn_words = [
            (session, _content_words(text))
            for session, text in evidence_turn_texts(conv, qa)
        ]
        matches: list[int] = []
        for neuron in self.neurons:
            if gold_usable and gold in neuron["text_norm"]:
                matches.append(neuron["id"])
                continue
            for session, words in turn_words:
                if not words or not neuron["words"]:
                    continue
                if neuron["session"] is not None and neuron["session"] != session:
                    continue
                overlap = (
                    len(neuron["words"] & words)
                    / min(len(neuron["words"]), len(words))
                )
                if overlap >= OVERLAP_THRESHOLD:
                    matches.append(neuron["id"])
                    break
        return matches


def _score_ids(ctx) -> list[int]:
    return [
        score["neuron_id"]
        for score in getattr(ctx, "neuron_scores", [])
        if isinstance(score, dict) and "neuron_id" in score
    ]


def _activated_ids(ctx) -> set[int]:
    neuron_ids = set(_score_ids(ctx))
    for score in getattr(ctx, "activated_scores", []) or []:
        neuron_id = getattr(score, "neuron_id", None)
        if neuron_id is not None:
            neuron_ids.add(neuron_id)
    return neuron_ids


async def probe(db, qa: dict, conv: dict, prod_ctx, oracle: OracleIndex,
                top_k: int) -> dict:
    """Attribute deterministic stages 1-4 for one question."""
    if qa.get("category") == 5:
        return {"stage": "adversarial", "oracle_neurons": []}

    oracle_ids = oracle.encoding_neurons(qa, conv)
    row: dict = {
        "oracle_neurons": oracle_ids[:20],
        "n_oracle": len(oracle_ids),
        "evidence_sessions": sorted(evidence_sessions(qa)),
    }
    if not oracle_ids:
        row["stage"] = "ingest"
        return row

    oracle_set = set(oracle_ids)
    from app.services.executor import prepare_context

    wide_ctx = await prepare_context(
        db, qa["question"], top_k=CANDIDATE_PROBE_K, recall_mode="cheap",
    )
    wide_ids = set(_score_ids(wide_ctx)) | _activated_ids(wide_ctx)
    row["candidate_hit"] = bool(oracle_set & wide_ids)
    if not row["candidate_hit"]:
        row["stage"] = "candidate"
        return row

    ranked = _score_ids(prod_ctx)
    ranks = [index + 1 for index, neuron_id in enumerate(ranked)
             if neuron_id in oracle_set]
    row["best_rank"] = min(ranks) if ranks else None
    if not ranks or min(ranks) > top_k:
        row["stage"] = "rank"
        return row

    delivered = (
        set(ranked[:top_k])
        & set(getattr(prod_ctx, "neuron_map", {}))
    )
    row["assembly_hit"] = bool(oracle_set & delivered)
    if not row["assembly_hit"]:
        row["stage"] = "assembly"
        return row

    row["stage"] = "retrieved"
    return row


def attribute_verdicts(results: list[dict]) -> None:
    """Finalize synthesis/judge/success after the existing judge runs."""
    for result in results:
        funnel = result.get("funnel")
        if not funnel or funnel.get("stage") != "retrieved":
            continue
        if result.get("correct"):
            funnel["stage"] = "success"
            continue
        gold = _norm(str(result.get("gold", "")))
        prediction = _norm(str(result.get("pred", "")))
        funnel["stage"] = (
            "judge" if len(gold) >= 3 and gold in prediction else "synthesis"
        )


def ledger(results: list[dict]) -> dict:
    rows = [
        result for result in results
        if result.get("funnel")
        and result["funnel"].get("stage") != "adversarial"
    ]
    total = len(rows)
    stages = Counter(result["funnel"]["stage"] for result in rows)
    by_category: dict[str, Counter] = defaultdict(Counter)
    for result in rows:
        by_category[str(result.get("category"))][result["funnel"]["stage"]] += 1
    stage_payload = {
        stage: {
            "count": stages.get(stage, 0),
            "pct": round(100 * stages.get(stage, 0) / max(1, total), 1),
        }
        for stage in STAGE_ORDER
    }
    losses = {
        stage: payload["pct"]
        for stage, payload in stage_payload.items()
        if stage != "success" and payload["pct"] > 0
    }
    return {
        "n_funneled": total,
        "stages": stage_payload,
        "per_category": {
            category: dict(counts)
            for category, counts in sorted(by_category.items())
        },
        "headline": (
            f"{stage_payload['success']['pct']}% success; losses — "
            + ", ".join(f"{stage}: {pct}pp" for stage, pct in losses.items())
        ),
    }


def write_rows(artifact_dir: str, condition: str,
               results: list[dict]) -> str:
    path = Path(artifact_dir) / f"funnel-{condition}.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            if not result.get("funnel"):
                continue
            handle.write(json.dumps({
                "question": result.get("question"),
                "category": result.get("category"),
                "gold": result.get("gold"),
                "pred": result.get("pred"),
                "correct": result.get("correct"),
                "n_hits": result.get("n_hits"),
                **result["funnel"],
            }) + "\n")
    return str(path)
