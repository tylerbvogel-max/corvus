import json

from app.services import model_usage_ledger as ledger


def test_usage_ledger_separates_equivalent_from_actual_cash(tmp_path, monkeypatch):
    path = tmp_path / "usage.jsonl"
    monkeypatch.setattr(ledger, "LEDGER_PATH", path)
    monkeypatch.setattr(ledger, "EPISODE_DIR", tmp_path / "episodes")
    ledger.record_model_usage(
        provider="anthropic", model="opus", harness="corvus_backend",
        workload="skill_compilation",
        result={"model_version": "claude-opus", "input_tokens": 100,
                "output_tokens": 10, "cost_usd": 0.25},
    )
    row = json.loads(path.read_text().strip())
    assert row["billing_basis"] == "subscription_included"
    assert row["actual_cash_usd"] is None
    report = ledger.usage_report(3.0)
    assert report["equivalent_cost_usd"] == 3.25
    assert report["actual_cash_complete"] is False
    assert report["by_model"][0]["calls"] == 1


def test_distillation_marker_and_ledger_are_not_double_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "LEDGER_PATH", tmp_path / "usage.jsonl")
    monkeypatch.setattr(ledger, "EPISODE_DIR", tmp_path / "episodes")
    ledger.record_model_usage(
        provider="anthropic", model="opus", harness="corvus_backend",
        workload="distillation",
        result={"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.5},
    )
    report = ledger.usage_report(3.5)
    assert report["ledger_equivalent_usd"] == 0.5
    assert report["equivalent_cost_usd"] == 3.5


def test_harness_stop_usage_is_aggregated_when_exposed(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "LEDGER_PATH", tmp_path / "usage.jsonl")
    episodes = tmp_path / "episodes"
    episodes.mkdir()
    monkeypatch.setattr(ledger, "EPISODE_DIR", episodes)
    (episodes / "codex-session.jsonl").write_text(json.dumps({
        "ts": "2026-07-15T00:00:00Z", "event": "Stop", "harness": "codex",
        "model": "gpt-5", "usage": {"provider": "openai", "input_tokens": 90,
        "output_tokens": 10, "billing_basis": "subscription_included"},
    }) + "\n")
    report = ledger.usage_report()
    assert report["tracked_calls"] == 1
    assert report["by_harness"][0]["label"] == "codex"
    assert report["by_model"][0]["tokens"] == 100
