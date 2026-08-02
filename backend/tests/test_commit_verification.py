"""Resolution of reconciliation evidence against a real repository.

LANE NOTE: these run real `git` against a throwaway repository under tmp_path.
That is genuine subprocess I/O, so the hermetic lane — which forbids
subprocesses by contract — cannot hold them.  They are classified
`integration` because everything they touch is local, deterministic, and
in-process-adjacent: no database, no network, no provider, no shared service.
Mocking git here would test the mock; the whole point of this gate is that a
sha either is or is not in somebody's actual history.
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from app.services.commit_verification import (
    commit_exists, is_git_repository, resolve_commits,
)


pytestmark = pytest.mark.integration


def _git(cwd, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True,
        capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A throwaway repository with exactly one commit."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    (root / "landed.txt").write_text("work that actually landed\n")
    _git(root, "add", "landed.txt")
    _git(root, "commit", "-q", "-m", "the commit that exists")
    return root


@pytest.fixture
def head_sha(repo) -> str:
    return _git(repo, "rev-parse", "HEAD")


def test_a_commit_that_exists_resolves_in_full_and_abbreviated_form(repo, head_sha):
    assert asyncio.run(commit_exists(str(repo), head_sha)) is True
    assert asyncio.run(commit_exists(str(repo), head_sha[:7])) is True


def test_a_commit_that_does_not_exist_is_refused(repo):
    """The whole point: a plausible sha for work that never landed."""
    assert asyncio.run(commit_exists(str(repo), "0" * 40)) is False
    assert asyncio.run(commit_exists(str(repo), "deadbee")) is False


def test_a_blob_hash_is_not_accepted_as_a_commit(repo):
    """`^{commit}` is load-bearing — a receipt must name a change, not a file."""
    blob = _git(repo, "hash-object", "-w", str(repo / "landed.txt"))
    assert asyncio.run(commit_exists(str(repo), blob)) is False


def test_resolution_splits_real_claims_from_invented_ones(repo, head_sha):
    resolved, unresolved, status = asyncio.run(resolve_commits(
        str(repo), [head_sha[:7], "0" * 12, "09481a623e8e"],
    ))
    assert status == "verified"
    assert resolved == [head_sha[:7]]
    assert unresolved == ["0" * 12, "09481a623e8e"]


def test_a_directory_that_is_not_a_repository_is_unverifiable_not_false(tmp_path):
    """Absence of a repo proves nothing; it must not read as a failed check."""
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    resolved, unresolved, status = asyncio.run(resolve_commits(str(plain), ["abc1234"]))
    assert status == "unverifiable-no-repo"
    assert resolved == []
    assert unresolved == ["abc1234"]


@pytest.mark.parametrize("path", [None, "", "/nonexistent/path/for/corvus/test"])
def test_missing_project_paths_are_unverifiable(path):
    assert asyncio.run(is_git_repository(path)) is False
    _, _, status = asyncio.run(resolve_commits(path, ["abc1234"]))
    assert status == "unverifiable-no-repo"


def test_verification_works_inside_a_worktree(repo, head_sha, tmp_path):
    """Unattended relays run each leg in a worktree, where .git is a FILE."""
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "-b", "leg", str(wt))
    assert (wt / ".git").is_file()
    assert asyncio.run(is_git_repository(str(wt))) is True
    resolved, _, status = asyncio.run(resolve_commits(str(wt), [head_sha[:7]]))
    assert status == "verified"
    assert resolved == [head_sha[:7]]


def test_a_commit_made_only_in_the_worktree_resolves_there(repo, tmp_path):
    """A leg's own commit must satisfy the gate from inside its worktree."""
    wt = tmp_path / "wt2"
    _git(repo, "worktree", "add", "-q", "-b", "leg2", str(wt))
    (wt / "leg.txt").write_text("work done by one relay leg\n")
    _git(wt, "add", "leg.txt")
    _git(wt, "commit", "-q", "-m", "leg work")
    leg_sha = _git(wt, "rev-parse", "HEAD")
    resolved, _, status = asyncio.run(resolve_commits(str(wt), [leg_sha[:7]]))
    assert status == "verified"
    assert resolved == [leg_sha[:7]]
