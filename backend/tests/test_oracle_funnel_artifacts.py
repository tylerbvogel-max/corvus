import json
import os

from app.services.oracle_funnel_artifacts import latest_artifact


def test_latest_artifact_empty_state(tmp_path):
    result = latest_artifact(tmp_path)
    assert result["available"] is False
    assert result["artifact_root"] == str(tmp_path)


def test_latest_artifact_builds_loss_ledger(tmp_path):
    older = tmp_path / "run-old" / "funnel-memory.jsonl"
    older.parent.mkdir()
    older.write_text(json.dumps({"stage": "success", "category": 1}) + "\n")

    latest = tmp_path / "run-new" / "funnel-memory.jsonl"
    latest.parent.mkdir()
    latest.write_text("\n".join([
        json.dumps({
            "question": "q1", "stage": "ingest", "category": 1,
            "correct": False,
        }),
        json.dumps({
            "question": "q2", "stage": "success", "category": 2,
            "correct": True,
        }),
        json.dumps({
            "question": "q3", "stage": "adversarial", "category": 5,
            "correct": True,
        }),
    ]) + "\n")
    os.utime(older, (1, 1))
    os.utime(latest, (2, 2))

    result = latest_artifact(tmp_path)
    assert result["available"] is True
    assert result["run"] == "run-new"
    assert result["row_count"] == 3
    assert result["ledger"]["n_funneled"] == 2
    assert result["ledger"]["stages"]["ingest"]["pct"] == 50.0
    assert result["ledger"]["stages"]["success"]["count"] == 1


def test_nexus_cluster_runtime_dependencies_are_importable():
    """The lab's Leiden overlay must not ship with undeclared imports."""
    from app.services.clustering import _build_igraph

    graph, nodes = _build_igraph([(1, 2, 0.7)], {1, 2})
    assert nodes == [1, 2]
    assert graph.vcount() == 2
