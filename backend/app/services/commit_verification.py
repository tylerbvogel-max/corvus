"""Resolve reconciliation evidence against the ledger's own repository.

A roadmap record is reconciled `done` by an API call, and the call is trusted
to describe work that landed.  On 2026-08-02 that trust broke in the only way
it can break silently: `mind-synaptic-downscaling` was marked done while its
implementation sat uncommitted in a working tree, so the ledger's forward
state and the repository's history disagreed with nothing to detect it.

This module is the detector.  It is deliberately the impure half of the gate —
`roadmap_ledger.reconcile_node` stays a pure validator that can be unit-tested
without a checkout, and everything that touches a filesystem or a subprocess
lives here.

Fail-closed and fail-honest are different failures and are treated
differently.  A commit that does NOT resolve in a repository we CAN read is
the exact condition this gate exists to catch, so it is refused.  A repository
we cannot read at all proves nothing either way, so the receipt records that
no check ran rather than implying one passed.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path


logger = logging.getLogger(__name__)

GIT_TIMEOUT_SECONDS = 10.0


async def _git(project_path: str, *args: str) -> tuple[int, str]:
    """Run one git command in ``project_path``; never raise, never hang.

    Arguments reach git through exec, not a shell, and callers only ever pass
    regex-restricted hex tokens, so evidence text cannot become a command.
    """
    git = shutil.which("git")
    if git is None:
        return 127, "git executable not found"
    try:
        proc = await asyncio.create_subprocess_exec(
            git, "-C", project_path, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return 127, str(exc)
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(),
                                           timeout=GIT_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "git timed out"
    return proc.returncode or 0, stdout.decode("utf-8", "replace").strip()


async def is_git_repository(project_path: str | None) -> bool:
    if not project_path or not Path(project_path).is_dir():
        return False
    # rev-parse rather than a .git existence test: worktrees carry .git as a
    # FILE, and this gate has to work inside the per-leg worktrees that
    # unattended relays run in.
    code, _ = await _git(project_path, "rev-parse", "--git-dir")
    return code == 0


async def commit_exists(project_path: str, sha: str) -> bool:
    """True when ``sha`` names a commit object reachable in this repository.

    ``^{commit}`` is load-bearing: without it a blob or tree whose hash shares
    the prefix would satisfy the check, and a receipt could name a file rather
    than a change.
    """
    code, _ = await _git(project_path, "cat-file", "-e", f"{sha}^{{commit}}")
    return code == 0


async def resolve_commits(
    project_path: str | None, candidates: list[str],
) -> tuple[list[str], list[str], str]:
    """Return ``(resolved, unresolved, status)`` for nominated commit tokens.

    ``status`` is ``verified`` when a repository was readable and the claims
    were actually checked, or ``unverifiable-no-repo`` when there was nothing
    to check against.  The caller decides what to refuse; this only reports.
    """
    if not await is_git_repository(project_path):
        logger.info("commit verification skipped: %r is not a git repository",
                    project_path)
        return [], list(candidates), "unverifiable-no-repo"

    assert project_path is not None
    results = await asyncio.gather(
        *(commit_exists(project_path, sha) for sha in candidates)
    )
    resolved = [sha for sha, ok in zip(candidates, results) if ok]
    unresolved = [sha for sha, ok in zip(candidates, results) if not ok]
    return resolved, unresolved, "verified"
