from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_worker_daemon_console_script_is_registered() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'evander-worker-daemon = "evander.worker_daemon:main"' in pyproject
