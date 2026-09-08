"""Nonblocking same-host exclusion, not a distributed or durable work lease."""

import fcntl
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class JobBusy(RuntimeError):
    """Another process owns this job's local lock."""


@contextmanager
def job_run_lock(job: str, directory: Path) -> Iterator[None]:
    """Hold a persistent inode's flock until exit, cancellation or process death.

    All workers must share the same local directory. Never unlink lock files:
    replacing their inode would let another caller acquire an independent lock.
    No TTL, PID inference, blocking wait, or fail-open fallback is involved.
    """
    if not isinstance(job, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", job):
        raise ValueError("invalid maintenance lock identity")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(directory / f"{job}.lock",
                 os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise JobBusy("maintenance-job-busy") from None
        yield
    finally:
        os.close(fd)
