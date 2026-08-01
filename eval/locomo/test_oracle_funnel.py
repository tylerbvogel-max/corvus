"""Pure-function coverage for the Carlos Lab Oracle Funnel."""

import asyncio
import sys
from pathlib import Path

import pytest


pytestmark = [pytest.mark.evaluation, pytest.mark.timeout(120)]

sys.path.insert(0, str(Path(__file__).parent))

import oracle_funnel


def _conversation():
    return {
        "conversation": {
            "session_1": [
                {"dia_id": "D1:1", "text": "Alice adopted a dog named Pepper."},
            ],
        },
    }


def _qa():
    return {
        "question": "What is Alice's dog called?",
        "answer": "Pepper",
        "category": 1,
        "evidence": ["D1:1"],
    }


def test_oracle_matches_gold_containment():
    index = oracle_funnel.OracleIndex([
        (7, "Alice dog", "Her dog is called Pepper.", "[session:locomo-0-1]"),
    ])
    assert index.encoding_neurons(_qa(), _conversation()) == [7]


def test_oracle_requires_matching_session_for_overlap():
    index = oracle_funnel.OracleIndex([
        (8, "Alice adopted dog", "Alice adopted a dog.",
         "Evidence: session 2 turn 1"),
    ])
    assert index.encoding_neurons(
        {**_qa(), "answer": "not-present"}, _conversation(),
    ) == []


def test_adversarial_probe_is_excluded():
    row = asyncio.run(oracle_funnel.probe(
        None,
        {"category": 5},
        _conversation(),
        None,
        oracle_funnel.OracleIndex([]),
        10,
    ))
    assert row == {"stage": "adversarial", "oracle_neurons": []}


def test_attribute_verdicts_separates_synthesis_judge_success():
    results = [
        {"correct": True, "gold": "Pepper", "pred": "Pepper",
         "funnel": {"stage": "retrieved"}},
        {"correct": False, "gold": "Pepper", "pred": "Pepper",
         "funnel": {"stage": "retrieved"}},
        {"correct": False, "gold": "Pepper", "pred": "Unknown",
         "funnel": {"stage": "retrieved"}},
    ]
    oracle_funnel.attribute_verdicts(results)
    assert [row["funnel"]["stage"] for row in results] == [
        "success", "judge", "synthesis",
    ]


def test_ledger_excludes_adversarial_rows():
    rows = [
        {"category": 1, "funnel": {"stage": "success"}},
        {"category": 1, "funnel": {"stage": "ingest"}},
        {"category": 5, "funnel": {"stage": "adversarial"}},
    ]
    result = oracle_funnel.ledger(rows)
    assert result["n_funneled"] == 2
    assert result["stages"]["success"]["pct"] == 50.0
    assert result["stages"]["ingest"]["count"] == 1


def test_write_rows_is_append_artifact_shape(tmp_path):
    path = oracle_funnel.write_rows(
        str(tmp_path),
        "memory",
        [{
            "question": "q", "category": 1, "gold": "g", "pred": "p",
            "correct": False, "n_hits": 2,
            "funnel": {"stage": "synthesis", "n_oracle": 1},
        }],
    )
    content = Path(path).read_text()
    assert '"stage": "synthesis"' in content
    assert Path(path).name == "funnel-memory.jsonl"
