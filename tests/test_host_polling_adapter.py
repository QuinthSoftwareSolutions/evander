from __future__ import annotations

from pathlib import Path

import pytest

from evander.orchestration.host_polling import HostFileLockManager


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
