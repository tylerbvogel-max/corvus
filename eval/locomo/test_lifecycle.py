import importlib.util
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
