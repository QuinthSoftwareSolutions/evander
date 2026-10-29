from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evander.config import GatewayConfig
from evander.directories import DirectoryService
from evander.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from evander.messaging.discord_ingest import build_discord_ingest_worker
from evander.models import SessionManifest, ToolName
from config_helpers import canonical_workflow_payload


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


def write_root_communication_targets(worker, targets: list[dict[str, object]]) -> None:
    worker.runtime.directories.ensure_root()
    repository = SqliteCommunicationTargetsRepository(
        communication_targets_db_path(worker.config.runtime_dir)
    )
    repository.write_overlay("root", {"targets": targets})


def write_trigger_config(project_dir: Path, triggers: list[dict[str, object]]) -> None:
    trigger_config_path = project_dir / "config" / "trigger-orchestrator" / "config.json"
    trigger_config_path.parent.mkdir(parents=True, exist_ok=True)
    trigger_config_path.write_text(
        json.dumps(canonical_workflow_payload({"triggers": triggers})),
        encoding="utf-8",
    )


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


def test_discord_ingest_worker_polls_session_communication_targets(tmp_path: Path) -> None:
    config = GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")})
    worker = build_discord_ingest_worker(config, project_dir=tmp_path)
    write_root_communication_targets(
        worker,
        [
            {
                "id": "discord-inbox",
                "provider": "discord",
                "kind": "discord",
                "address": "123456789012345678/234567890123456789",
                "policy": {"capabilities": ["read"]},
            }
        ],
    )
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-a",
            directory_path="root",
            allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
            discord_channel_allowlist=["discord-inbox"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    calls: list[tuple[str, list[str]]] = []

    def record_poll(directory_path: str, *, channel_ids: list[str]) -> None:
        calls.append((directory_path, channel_ids))

    worker._poll_directory = record_poll  # type: ignore[method-assign]

    result = worker.run_once()

    assert calls == [("root", ["234567890123456789"])]
    assert result.directories_polled == 1
    assert result.sessions_seen == 1


def test_discord_ingest_worker_polls_communication_trigger_targets_without_active_sessions(
    tmp_path: Path,
) -> None:
    write_trigger_config(
        tmp_path,
        [
            {
                "id": "communication-message-trigger",
                "display_name": "Communication message",
                "type": "trigger:communication:new-message",
                "communication_target_ids": ["discord-inbox"],
                "directory_path": "root",
            }
        ],
    )
    config = GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")})
    worker = build_discord_ingest_worker(config, project_dir=tmp_path)
    write_root_communication_targets(
        worker,
        [
            {
                "id": "discord-inbox",
                "provider": "discord",
                "kind": "discord",
                "address": "123456789012345678/234567890123456789",
                "policy": {"capabilities": ["read"]},
            }
        ],
    )
    calls: list[tuple[str, list[str]]] = []

    def record_poll(directory_path: str, *, channel_ids: list[str]) -> None:
        calls.append((directory_path, channel_ids))

    worker._poll_directory = record_poll  # type: ignore[method-assign]

    result = worker.run_once()

    assert calls == [("root", ["234567890123456789"])]
    assert result.directories_polled == 1
    assert result.sessions_seen == 0


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


def test_discord_ingest_worker_batches_new_messages_before_advancing_cursor(
    tmp_path: Path,
) -> None:
    worker = build_discord_ingest_worker(
        GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")}),
        project_dir=tmp_path,
    )

    class _FakeResponse:
        def json(self):
            return [
                {
                    "id": "2",
                    "timestamp": "2026-03-30T11:00:02Z",
                    "content": "second",
                    "author": {"id": "user-b", "username": "B"},
                    "attachments": [],
                },
                {
                    "id": "1",
                    "timestamp": "2026-03-30T11:00:01Z",
                    "content": "first",
                    "author": {"id": "user-a", "username": "A"},
                    "attachments": [
                        {
                            "id": "att-1",
                            "filename": "report.txt",
                            "content_type": "text/plain",
                            "size": 11,
                            "url": "https://cdn.discord.test/att-1",
                            "content": "must-not-store",
                        }
                    ],
                },
            ]

    class _FakeAdapter:
        api_base_url = "https://discord.test"
        poll_limit = 50

        def _request_with_retries(self, client, method, url, params=None):
            return _FakeResponse()

    class _RecordingStore:
        def __init__(self) -> None:
            self.add_messages_calls: list[list[str]] = []
            self.set_cursor_calls: list[tuple[str, str | None]] = []

        def get_cursor(self, channel_id: str) -> str | None:
            return None

        def add_messages_if_new(self, envelopes) -> int:
            message_ids = [envelope.message_id for envelope in envelopes]
            self.add_messages_calls.append(message_ids)
            return len(message_ids)

        def set_cursor(self, channel_id: str, cursor: str | None) -> None:
            self.set_cursor_calls.append((channel_id, cursor))

    store = _RecordingStore()
    worker.runtime.discord_inbox_service.store = store

    worker._poll_channel(object(), adapter=_FakeAdapter(), channel_id="chan-a")

    assert store.add_messages_calls == [["1", "2"]]
    assert store.set_cursor_calls == [("chan-a", "2026-03-30T11:00:02Z")]
    mirrored = worker.runtime.discord_commands.get("1")
    assert mirrored is not None
    assert mirrored.channel_id == "chan-a"
    assert mirrored.metadata["attachments"][0] == {
        "id": "att-1",
        "filename": "report.txt",
        "content_type": "text/plain",
        "size": 11,
        "url": "https://cdn.discord.test/att-1",
    }


def test_discord_ingest_discovers_only_public_threads_under_configured_parent(
    tmp_path: Path,
) -> None:
    worker = build_discord_ingest_worker(
        GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")}),
        project_dir=tmp_path,
    )
    calls: list[str] = []

    class _FakeResponse:
        def __init__(self, payload: object) -> None:
            self.payload = payload

        def json(self):
            return self.payload

    class _FakeAdapter:
        api_base_url = "https://discord.test/api/v10"

        def _request_with_retries(self, _client, _method, url, params=None):
            calls.append(url)
            if url.endswith("/channels/234567890123456789"):
                return _FakeResponse({"guild_id": "123456789012345678"})
            if url.endswith("/guilds/123456789012345678/threads/active"):
                return _FakeResponse(
                    {
                        "threads": [
                            {
                                "id": "345678901234567890",
                                "parent_id": "234567890123456789",
                                "type": 11,
                                "thread_metadata": {"archived": False, "locked": False},
                            },
                            {
                                "id": "456789012345678901",
                                "parent_id": "234567890123456789",
                                "type": 12,
                                "thread_metadata": {"archived": False, "locked": False},
                            },
                            {
                                "id": "567890123456789012",
                                "parent_id": "999999999999999999",
                                "type": 11,
                                "thread_metadata": {"archived": False, "locked": False},
                            },
                        ]
                    }
                )
            raise AssertionError(url)

    discovered = worker._discover_public_threads(
        object(),
        adapter=_FakeAdapter(),
        channel_scope_by_parent={"234567890123456789": "public_threads"},
    )

    assert discovered == {"234567890123456789": ["345678901234567890"]}
    binding = worker.runtime.discord_inbox_service.store.get_thread_binding("345678901234567890")
    assert binding is not None
    assert binding.parent_channel_id == "234567890123456789"
    assert calls.count("https://discord.test/api/v10/guilds/123456789012345678/threads/active") == 1


def test_discord_ingest_polls_bound_threads_only_with_parent_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = build_discord_ingest_worker(
        GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")}),
        project_dir=tmp_path,
    )
    parent_channel_id = "234567890123456789"
    thread_id = "345678901234567890"
    write_root_communication_targets(
        worker,
        [
            {
                "id": "discord-forum",
                "provider": "discord",
                "kind": "discord",
                "address": f"123456789012345678/{parent_channel_id}",
                "policy": {
                    "capabilities": ["read"],
                    "discord_conversation_scope": "public_threads",
                },
            }
        ],
    )
    provider_config = worker.config.model_copy(update={"discord_backend": "bot_api"})
    monkeypatch.setattr(
        worker.runtime.provider_config,
        "resolve_for_directory",
        lambda _directory_path: provider_config,
    )
    monkeypatch.setenv(provider_config.discord_bot_token_env, "test-token")
    observed_at = datetime(2026, 8, 17, tzinfo=UTC)
    store = worker.runtime.discord_inbox_service.store
    store.upsert_thread_binding(
        thread_id=thread_id,
        parent_channel_id=parent_channel_id,
        guild_id="123456789012345678",
        thread_type=11,
        archived=False,
        locked=False,
        observed_at=observed_at,
    )
    store.register_thread_parent(parent_channel_id, observed_at=observed_at)
    monkeypatch.setattr(
        worker,
        "_discover_public_threads",
        lambda *_args, **_kwargs: {parent_channel_id: [thread_id]},
    )
    calls: list[tuple[str, str | None]] = []

    def record_poll(
        _client: object,
        *,
        adapter: object,
        channel_id: str,
        parent_channel_id: str | None = None,
    ) -> None:
        calls.append((channel_id, parent_channel_id))

    monkeypatch.setattr(worker, "_poll_channel", record_poll)

    worker._poll_directory("root", channel_ids=[parent_channel_id, thread_id])

    assert calls == [(thread_id, parent_channel_id)]


def test_discord_ingest_merges_shared_parent_scopes_as_an_order_independent_union(
    tmp_path: Path,
) -> None:
    worker = build_discord_ingest_worker(
        GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")}),
        project_dir=tmp_path,
    )
    parent_channel_id = "234567890123456789"
    write_root_communication_targets(
        worker,
        [
            {
                "id": "a-combined",
                "provider": "discord",
                "kind": "discord",
                "address": f"123456789012345678/{parent_channel_id}",
                "policy": {
                    "capabilities": ["read"],
                    "discord_conversation_scope": "channel_and_public_threads",
                },
            },
            {
                "id": "z-threads",
                "provider": "discord",
                "kind": "discord",
                "address": f"123456789012345678/{parent_channel_id}",
                "policy": {
                    "capabilities": ["read"],
                    "discord_conversation_scope": "public_threads",
                },
            },
        ],
    )

    assert worker._discord_conversation_scopes(
        "root",
        channel_ids=[parent_channel_id],
    ) == {parent_channel_id: "channel_and_public_threads"}


def test_discord_ingest_worker_does_not_advance_cursor_when_batch_write_fails(
    tmp_path: Path,
) -> None:
    worker = build_discord_ingest_worker(
        GatewayConfig.model_validate({"runtime_dir": str(tmp_path / "runtime")}),
        project_dir=tmp_path,
    )

    class _FakeResponse:
        def json(self):
            return [
                {
                    "id": "1",
                    "timestamp": "2026-03-30T11:00:01Z",
                    "content": "first",
                    "author": {"id": "user-a", "username": "A"},
                    "attachments": [],
                }
            ]

    class _FakeAdapter:
        api_base_url = "https://discord.test"
        poll_limit = 50

        def _request_with_retries(self, client, method, url, params=None):
            return _FakeResponse()

    class _FailingStore:
        def __init__(self) -> None:
            self.set_cursor_calls: list[tuple[str, str | None]] = []

        def get_cursor(self, channel_id: str) -> str | None:
            return None

        def add_messages_if_new(self, envelopes) -> int:
            raise RuntimeError("boom")

        def set_cursor(self, channel_id: str, cursor: str | None) -> None:
            self.set_cursor_calls.append((channel_id, cursor))

    store = _FailingStore()
    worker.runtime.discord_inbox_service.store = store

    with pytest.raises(RuntimeError, match="boom"):
        worker._poll_channel(object(), adapter=_FakeAdapter(), channel_id="chan-a")

    assert store.set_cursor_calls == []
