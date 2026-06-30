from __future__ import annotations

import time
from pathlib import Path

import pytest

from evander.orchestration.host_polling import HostFileLockManager, HostSleeper


def test_host_file_lock_manager_releases_and_reacquires(tmp_path: Path) -> None:
    lock_path = tmp_path / "worker.lock"
    manager = HostFileLockManager(busy_error_text="Discord ingest worker already running")

    with manager.lock(lock_path):
        assert lock_path.exists()

    with manager.lock(lock_path):
        assert lock_path.exists()


def test_host_file_lock_manager_uses_custom_busy_text(tmp_path: Path) -> None:
    lock_path = tmp_path / "worker.lock"
    manager = HostFileLockManager(busy_error_text="Teams ingest worker already running")

    with manager.lock(lock_path):
        with pytest.raises(RuntimeError, match=r"Teams ingest worker already running"):
            manager.lock(lock_path)


def test_host_sleeper_delegates_to_time_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[float] = []

    def _sleep(seconds: float) -> None:
        calls.append(seconds)

    monkeypatch.setattr(time, "sleep", _sleep)

    HostSleeper().sleep(1.25)

    assert calls == [1.25]
