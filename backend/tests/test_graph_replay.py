from datetime import datetime, timedelta

from app.routers.neurons import (
    _apply_graph_activity,
    _is_conversational_recall,
    _select_replay_segments,
)


def test_replay_segments_use_historical_members_and_avoid_cycles():
    firings = [
        {"neuron_id": 1, "rank": 1, "spread_boost": 0.0},
        {"neuron_id": 2, "rank": 2, "spread_boost": 0.2},
        {"neuron_id": 3, "rank": 3, "spread_boost": 0.0},
    ]
    edges = [
        {"source": 1, "target": 2, "weight": 0.9},
        {"source": 2, "target": 3, "weight": 0.8},
        {"source": 1, "target": 3, "weight": 0.7},
        {"source": 3, "target": 99, "weight": 1.0},
    ]

    segments = _select_replay_segments(firings, edges)

    assert [(segment["source"], segment["target"]) for segment in segments] == [
        (1, 2),
        (2, 3),
    ]
    assert all(segment["kind"] == "spread" for segment in segments)
    assert all(99 not in (segment["source"], segment["target"]) for segment in segments)


def test_replay_segments_are_bounded():
    firings = [
        {"neuron_id": neuron_id, "rank": neuron_id, "spread_boost": 0.0}
        for neuron_id in range(1, 8)
    ]
    edges = [
        {"source": neuron_id, "target": neuron_id + 1, "weight": 0.8}
        for neuron_id in range(1, 7)
    ]

    segments = _select_replay_segments(firings, edges, max_segments=3)

    assert len(segments) == 3
    assert all(segment["kind"] == "coactivation" for segment in segments)


def test_graph_activity_counts_only_real_retained_pairs_by_window():
    now = datetime(2026, 7, 28, 12, 0, 0)
    edges = [
        {"source": 1, "target": 2, "co_fire_count": 9},
        {"source": 2, "target": 3, "co_fire_count": 4},
    ]
    rows = [
        {"query_id": 10, "neuron_id": 1, "created_at": now - timedelta(hours=4)},
        {"query_id": 10, "neuron_id": 2, "created_at": now - timedelta(hours=4)},
        {"query_id": 10, "neuron_id": 99, "created_at": now - timedelta(hours=4)},
        {"query_id": 11, "neuron_id": 2, "created_at": now - timedelta(days=3)},
        {"query_id": 11, "neuron_id": 3, "created_at": now - timedelta(days=3)},
        {"query_id": 12, "neuron_id": 1, "created_at": now - timedelta(days=12)},
        {"query_id": 12, "neuron_id": 2, "created_at": now - timedelta(days=12)},
    ]

    _apply_graph_activity(edges, rows, now)

    assert edges[0] == {
        "source": 1,
        "target": 2,
        "co_fire_count": 9,
        "activity_1d": 1,
        "activity_7d": 1,
        "activity_30d": 2,
        "activity_all": 9,
    }
    assert edges[1]["activity_1d"] == 0
    assert edges[1]["activity_7d"] == 1
    assert edges[1]["activity_30d"] == 1


def test_recall_lens_excludes_maintenance_traffic_but_keeps_chat():
    assert _is_conversational_recall("I like it, lets do them.")
    assert _is_conversational_recall("Can we make the graph show prior sessions?")
    assert not _is_conversational_recall(
        "SYSTEM INSTRUCTIONS (follow these for this task): classify this pair"
    )
    assert not _is_conversational_recall(
        "working knowledge, gotchas, tool profiles, and user preferences for this machine"
    )
    assert not _is_conversational_recall("git diff --check")
    assert not _is_conversational_recall("command -v jq || true")
    assert not _is_conversational_recall("wc -l /tmp/results.jsonl")
    assert not _is_conversational_recall("/usr/bin/python3 /tmp/probe.py")
