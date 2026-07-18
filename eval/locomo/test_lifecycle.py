import importlib.util
import os
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("run_locomo.py")
SPEC = importlib.util.spec_from_file_location("run_locomo", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
locomo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(locomo)


def test_raw_has_no_maintenance_events():
    assert all(not locomo.lifecycle_events("raw", n) for n in range(1, 201))


def test_consolidation_runs_after_every_session():
    events = [locomo.lifecycle_events("consolidation", n)
              for n in range(1, 201)]
    assert events == [["consolidation"]] * 200


def test_full_lifecycle_matches_200_session_week():
    scheduled = {n: locomo.lifecycle_events("full-lifecycle", n)
                 for n in range(1, 201)}
    janitors = [n for n, events in scheduled.items() if "janitor" in events]
    compilers = [n for n, events in scheduled.items() if "compiler" in events]

    assert len(janitors) == 28
    assert len(compilers) == 7
    assert janitors[-1] == 200
    assert compilers[-1] == 200
    assert set(b - a for a, b in zip(janitors, janitors[1:])) <= {7, 8}
    assert set(b - a for a, b in zip(compilers, compilers[1:])) <= {28, 29}


def test_global_offset_preserves_events_across_process_sized_segments():
    whole = [
        (n, locomo.lifecycle_events("full-lifecycle", n))
        for n in range(1, 201)
    ]
    segment_sizes = [19, 21, 18, 22, 20, 17, 23, 19, 20, 21]
    segmented = []
    offset = 0
    for size in segment_sizes:
        segmented.extend(
            (offset + local,
             locomo.lifecycle_events("full-lifecycle", offset + local))
            for local in range(1, size + 1)
        )
        offset += size

    assert offset == 200
    assert segmented == whole


def test_eval_artifacts_are_isolated(tmp_path):
    requested = tmp_path / "lifecycle-run"
    artifact_dir = Path(locomo.create_eval_artifact_dir(
        0, "full-lifecycle", str(requested)))

    assert artifact_dir == requested
    assert (artifact_dir / "episodes").is_dir()
    assert (artifact_dir / "skills").is_dir()


def test_live_skill_directory_is_rejected():
    with pytest.raises(AssertionError, match="live Claude skill directory"):
        locomo.create_eval_artifact_dir(0, "full-lifecycle", "~/.claude")


def test_only_throwaway_locomo_database_is_accepted():
    locomo.assert_eval_database(
        "postgresql+asyncpg://user:pass@localhost:5432/corvus_locomo")
    with pytest.raises(AssertionError, match="requires corvus_locomo"):
        locomo.assert_eval_database(
            "postgresql+asyncpg://user:pass@localhost:5432/corvus_mind")


# ── Gate 1: arms are single-variable ablations of the shipped config ──

def _diff_keys(a: dict, b: dict) -> set:
    return {k for k in a if a[k] != b[k]}


def test_memory_arm_is_shipped_hybrid_spread_config():
    assert locomo.ARM_CONFIG["memory"] == {
        "spread_enabled": True,
        "keyword_lane_enabled": True,
        "entity_lane_enabled": True,
    }


def test_nospread_differs_from_memory_by_spread_only():
    assert _diff_keys(locomo.ARM_CONFIG["memory"],
                      locomo.ARM_CONFIG["nospread"]) == {"spread_enabled"}


def test_embed_only_differs_from_memory_by_lanes_only():
    assert _diff_keys(locomo.ARM_CONFIG["memory"],
                      locomo.ARM_CONFIG["embed-only"]) == {
        "keyword_lane_enabled", "entity_lane_enabled"}


def test_strict_refusal_is_the_default_prompt():
    assert locomo.ANSWER_PROMPT.endswith(locomo._ANSWER_RULES_STRICT)


# ── Gate 2: provider drift aborts, clean receipts pass ──

def _result(**over):
    base = {"served_by": "sonnet", "provider": "anthropic",
            "model_version": "claude-sonnet-5", "input_tokens": 10,
            "output_tokens": 5, "cost_usd": 0.0}
    base.update(over)
    return base


def test_clean_receipt_passes(monkeypatch):
    monkeypatch.setattr(locomo, "_MODEL_VERSION_SEEN", {})
    locomo.verify_and_record_receipt("judge", "sonnet", _result())
    locomo.verify_and_record_receipt("judge", "sonnet", _result())


def test_fallback_served_call_aborts(monkeypatch):
    monkeypatch.setattr(locomo, "_MODEL_VERSION_SEEN", {})
    with pytest.raises(locomo.ProviderDrift, match="fallback"):
        locomo.verify_and_record_receipt(
            "answer", "sonnet",
            _result(served_by="codex-terra", provider="openai_codex",
                    fallback_from="sonnet"))


def test_wrong_provider_aborts(monkeypatch):
    monkeypatch.setattr(locomo, "_MODEL_VERSION_SEEN", {})
    with pytest.raises(locomo.ProviderDrift, match="provider"):
        locomo.verify_and_record_receipt(
            "answer", "sonnet",
            _result(provider="openai_codex", served_by="codex-terra"))


def test_mid_run_model_version_change_aborts(monkeypatch):
    monkeypatch.setattr(locomo, "_MODEL_VERSION_SEEN", {})
    locomo.verify_and_record_receipt("distill", "opus",
                                     _result(model_version="claude-opus-4-8"))
    with pytest.raises(locomo.ProviderDrift, match="changed mid-run"):
        locomo.verify_and_record_receipt(
            "distill", "opus", _result(model_version="claude-opus-4-9"))


def test_codex_fallback_is_disabled_at_module_import():
    assert os.environ["CODEX_PATH"].startswith("/nonexistent/")


# ── Gate 3: dataset is hash-pinned and fails closed ──

def test_tampered_dataset_fails_closed(monkeypatch, tmp_path):
    bad = tmp_path / "locomo10.json"
    bad.write_text("[]")
    monkeypatch.setattr(locomo, "DATA_PATH", str(bad))
    with pytest.raises(AssertionError, match="SHA-256 mismatch"):
        locomo.load_dataset()


def test_pinned_dataset_loads():
    data = locomo.load_dataset()
    assert len(data) == 10
    assert sum(locomo.session_count(c) for c in data) == 272


# ── Gate 4: compiled eval skills must never touch live skill directories ──

def test_skill_projection_is_captured_inside_artifact_dir(tmp_path):
    locomo.configure_isolated_runtime(str(tmp_path))
    from app.services import skill_projection, skill_compiler

    outputs = skill_projection.project_skill("mind-eval-test", "# body\n")
    for path in outputs.values():
        assert path.startswith(str(tmp_path)), \
            f"projection escaped the artifact dir: {path}"
    assert (tmp_path / "skills" / "mind-eval-test" / "SKILL.md").exists()

    removed = skill_projection.remove_projected_skill("mind-eval-test")
    assert removed and all(p.startswith(str(tmp_path)) for p in removed)
    assert not (tmp_path / "skills" / "mind-eval-test").exists()
    assert skill_compiler.RETIRED_DIR.startswith(str(tmp_path))
    assert skill_compiler.SKILLS_DIR.startswith(str(tmp_path))


def test_write_skill_end_to_end_stays_in_artifact_dir(tmp_path):
    locomo.configure_isolated_runtime(str(tmp_path))
    from app.services import skill_compiler

    path = skill_compiler._write_skill(
        "mind-eval-e2e", "test description", "body", [1, 2])
    assert path.startswith(str(tmp_path))
    assert Path(path).exists()
