from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.directories import DirectoryService
from agent_sandbox.messaging.discord_ingest import build_discord_ingest_worker
from agent_sandbox.models import SessionManifest, ToolName


def ensure_directory(worker, directory_path: str) -> None:
    service = DirectoryService(worker.runtime.directories)
    current = "root"
    for segment in [part for part in directory_path.split("/")[1:] if part]:
        next_path = f"{current}/{segment}"
        try:
            service.show(next_path)
        except ValueError:
            service.create(parent=current, name=segment)
        current = next_path


def test_build_discord_ingest_worker_uses_worker_config_and_normalizes_directories(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    worker_config_path = project_dir / "config" / "discord-ingest" / "config.json"
    worker_config_path.parent.mkdir(parents=True, exist_ok=True)
    worker_config_path.write_text(
        json.dumps(
            {
                "directories": [" root/team-a ", "root/team-b", "  "],
                "poll_interval_seconds": 12.5,
                "jitter_seconds": 1.5,
                "backoff_seconds": 45.0,
            }
        ),
        encoding="utf-8",
    )

    config = GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")})
    worker = build_discord_ingest_worker(config, project_dir=project_dir)
    ensure_directory(worker, "root/team-a")
    ensure_directory(worker, "root/team-b")

    assert worker.worker_config.directories == ["root/team-a", "root/team-b"]
    assert str(worker.worker_config.lock_path) == str(tmp_path / "runtime" / "discord-ingest.lock")
    result = worker.run_once()
    assert result.directories_polled == 2
    assert result.sessions_seen == 0


def test_discord_ingest_worker_counts_active_sessions_in_configured_scope(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    worker_config_path = project_dir / "config" / "discord-ingest" / "config.json"
    worker_config_path.parent.mkdir(parents=True, exist_ok=True)
    worker_config_path.write_text(
        json.dumps(
            {
                "directories": ["root/team-a"],
                "poll_interval_seconds": 10.0,
                "jitter_seconds": 0.0,
                "backoff_seconds": 30.0,
            }
        ),
        encoding="utf-8",
    )

    config = GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")})
    worker = build_discord_ingest_worker(config, project_dir=project_dir)
    ensure_directory(worker, "root/team-a")
    ensure_directory(worker, "root/team-b")
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-a",
            vm_id="vm-a",
            directory_path="root/team-a",
            allowed_tools=[ToolName.APPROVAL_CHECK],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-b",
            vm_id="vm-b",
            directory_path="root/team-b",
            allowed_tools=[ToolName.APPROVAL_CHECK],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    result = worker.run_once()

    assert result.directories_polled == 1
    assert result.sessions_seen == 1


def test_discord_ingest_worker_run_once_with_lock_uses_configured_lock_path(
    tmp_path: Path,
) -> None:
    worker = build_discord_ingest_worker(
        GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")}),
        project_dir=tmp_path,
    )

    class _RecordingLock:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

    class _RecordingLockManager:
        def __init__(self) -> None:
            self.paths: list[object] = []

        def lock(self, path: object):
            self.paths.append(path)
            return _RecordingLock()

    lock_manager = _RecordingLockManager()
    worker._kernel._lock_manager = lock_manager

    result = worker.run_once_with_lock()

    assert result.directories_polled == 0
    assert result.sessions_seen == 0
    assert lock_manager.paths == [worker.worker_config.lock_path]


def test_discord_ingest_worker_reports_busy_lock_message(tmp_path: Path) -> None:
    worker = build_discord_ingest_worker(
        GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")}),
        project_dir=tmp_path,
    )

    with worker._kernel._lock_manager.lock(worker.worker_config.lock_path):
        with pytest.raises(RuntimeError, match="Discord ingest worker already running") as exc:
            worker.run_once_with_lock()

    assert str(worker.worker_config.lock_path) in str(exc.value)


def test_discord_ingest_normalize_message_preserves_attachment_metadata_without_bytes(
    tmp_path: Path,
) -> None:
    worker = build_discord_ingest_worker(
        GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")}),
        project_dir=tmp_path,
    )

    envelope = worker._normalize_message(
        channel_id="chan-a",
        created_at=datetime(2026, 3, 30, 11, 0, tzinfo=UTC),
        payload={
            "id": "msg-1",
            "content": "attached",
            "author": {"id": "user-a", "username": "Ada"},
            "attachments": [
                {
                    "id": "att-1",
                    "filename": "report.txt",
                    "content_type": "text/plain",
                    "size": 11,
                    "url": "https://cdn.discord.test/att-1",
                    "content": "must-not-store",
                    "content_base64": "bXVzdC1ub3Qtc3RvcmU=",
                }
            ],
        },
    )

    assert envelope.raw["attachments"] == [
        {
            "id": "att-1",
            "filename": "report.txt",
            "content_type": "text/plain",
            "size": 11,
            "url": "https://cdn.discord.test/att-1",
        }
    ]
    assert "content" not in envelope.raw["message"]["attachments"][0]
    assert "content_base64" not in envelope.raw["message"]["attachments"][0]
