from __future__ import annotations

import fcntl
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class _LockHandle:
    path: Path
    fd: int

    def __enter__(self) -> _LockHandle:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def single_instance_lock(
    path: Path, *, busy_error_text: str = "worker already running"
) -> _LockHandle:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise RuntimeError(f"{busy_error_text} (lock held at {path})") from exc
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
    return _LockHandle(path, fd)


class HostFileLockManager:
    def __init__(self, *, busy_error_text: str = "worker already running") -> None:
        self._busy_error_text = busy_error_text

    def lock(self, path: Any) -> _LockHandle:
        return single_instance_lock(Path(path), busy_error_text=self._busy_error_text)


class HostSleeper:
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)
