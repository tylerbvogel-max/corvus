"""The checkpoint diagnostic must never fall back to a personal database."""

import pytest

from scripts.reproduce_distillation_checkpoints import disposable_url


@pytest.mark.parametrize("url", [
    "",
    "postgresql+asyncpg://localhost/corvus_mind",
    "postgresql+asyncpg://localhost/corvus_locomo",
    "postgresql+asyncpg://localhost/not_corvus_test_example",
    "postgresql://localhost/corvus_test_example",
    "sqlite:///corvus_test_example",
])
def test_reject_non_disposable_target(url):
    with pytest.raises(ValueError):
        disposable_url(url)


@pytest.mark.parametrize("database", ["corvus_test_checkpoint", "corvus_migration_checkpoint"])
def test_accept_explicit_disposable_asyncpg_target(database):
    assert disposable_url(f"postgresql+asyncpg://localhost/{database}").database == database
