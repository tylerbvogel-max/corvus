"""Backend test-lane policy and timeout diagnostics."""

from __future__ import annotations

import faulthandler
import signal

import pytest


PRIMARY_LANES = {
    "hermetic",
    "database",
    "integration",
    "evaluation",
    "live_provider",
}


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live-provider",
        action="store_true",
        default=False,
        help="authorize tests that make real model-provider calls",
    )


def pytest_configure() -> None:
    # The lane runner sends SIGUSR1 before terminating a job-level timeout.
    # Registering it here preserves every Python thread's stack in stdout.
    if hasattr(signal, "SIGUSR1"):
        faulthandler.register(signal.SIGUSR1, all_threads=True)


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item],
) -> None:
    """Default unmarked backend tests to hermetic and reject ambiguity."""
    for item in items:
        declared = {
            name for name in PRIMARY_LANES
            if item.get_closest_marker(name) is not None
        }
        if not declared:
            item.add_marker(pytest.mark.hermetic)
            declared = {"hermetic"}
        if len(declared) != 1:
            raise pytest.UsageError(
                f"{item.nodeid} declares multiple primary test lanes: "
                f"{sorted(declared)}"
            )
        if "live_provider" in declared and not config.getoption("--run-live-provider"):
            item.add_marker(pytest.mark.skip(
                reason="live-provider lane requires --run-live-provider",
            ))
