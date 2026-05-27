from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.directories import DirectoryService
from agent_sandbox.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from agent_sandbox.models import SessionManifest, TeamsMessageEnvelope, ToolName
from agent_sandbox.messaging.teams_ingest import build_teams_ingest_worker
from agent_sandbox.orchestration.host_polling import HostWakeSleeper, wake_socket_path_for_lock


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


def patch_worker_httpx_client(
    monkeypatch: pytest.MonkeyPatch,
    transport: httpx.BaseTransport,
) -> None:
    real_client = httpx.Client
    monkeypatch.setattr(
        "agent_sandbox.messaging.teams_ingest.httpx.Client",
        lambda timeout: real_client(timeout=timeout, transport=transport),
    )


def write_root_communication_targets(worker, targets: list[dict[str, object]]) -> None:
    worker.runtime.directories.ensure_root()
    repository = SqliteCommunicationTargetsRepository(
        communication_targets_db_path(worker.config.runtime_dir)
    )
    repository.write_overlay("root", {"targets": targets})


def write_trigger_config(
    project_dir: Path,
    triggers: list[dict[str, object]],
    *,
    extra: dict[str, object] | None = None,
) -> None:
    trigger_config_path = project_dir / "config" / "trigger-orchestrator" / "config.json"
    trigger_config_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"triggers": triggers}
    if extra:
        payload.update(extra)
    trigger_config_path.write_text(json.dumps(payload), encoding="utf-8")


def test_build_teams_ingest_worker_uses_worker_config_and_normalizes_directories(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    worker_config_path = project_dir / "config" / "teams-ingest" / "config.json"
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
    worker = build_teams_ingest_worker(config, project_dir=project_dir)
    ensure_directory(worker, "root/team-a")
    ensure_directory(worker, "root/team-b")

    assert worker.worker_config.directories == ["root/team-a", "root/team-b"]
    assert str(worker.worker_config.lock_path) == str(tmp_path / "runtime" / "teams-ingest.lock")
    result = worker.run_once()
    assert result.directories_polled == 2
    assert result.sessions_seen == 0
    assert result.channel_targets_polled == 0


def test_teams_ingest_worker_persists_inbound_messages_and_ignores_status_store_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/teams/team-a/channels/channel-1/messages"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-1",
                            "createdDateTime": "2026-03-30T11:00:00Z",
                            "body": {"content": "<p>hello</p>", "contentType": "html"},
                            "from": {"user": {"id": "user-1", "displayName": "Ada"}},
                            "attachments": [
                                {
                                    "id": "att-1",
                                    "name": "report.txt",
                                    "contentType": "reference",
                                    "contentUrl": "https://graph.microsoft.com/download/att-1",
                                    "contentBytes": "must-not-store",
                                }
                            ],
                            "hostedContents": [
                                {
                                    "id": "hosted-1",
                                    "contentType": "image/png",
                                    "contentBytes": "must-not-store",
                                }
                            ],
                            "replies": [
                                {
                                    "id": "msg-2",
                                    "replyToId": "msg-1",
                                    "createdDateTime": "2026-03-30T11:01:00Z",
                                    "body": {"content": "reply", "contentType": "text"},
                                    "from": {"user": {"id": "user-2", "displayName": "Grace"}},
                                }
                            ],
                        }
                    ]
                },
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
        }
    )
    wake_calls: list[str] = []
    worker = build_teams_ingest_worker(
        config,
        project_dir=tmp_path,
        trigger_wake_notifier=lambda: wake_calls.append("wake") or True,
    )
    ensure_directory(worker, "root/team-a")
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["team-a/channel-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    worker.runtime.teams_messages.put(
        "team-a/channel-1/msg-status",
        TeamsMessageEnvelope(
            cursor="team-a/channel-1/msg-status",
            message_id="msg-status",
            author_id="bot-app",
            team_id="team-a",
            channel_id="channel-1",
            thread_id="msg-status",
            content="status",
            created_at=datetime(2026, 3, 30, 10, 59, tzinfo=UTC),
            metadata={"source": "teams_graph_api_status", "session_id": "session-1"},
        ),
    )

    result = worker.run_once()

    stored = worker.store.list_all_messages(channel_targets=["team-a/channel-1"])
    assert [message.cursor for message in stored] == [
        "team-a/channel-1/msg-1",
        "team-a/channel-1/msg-2",
    ]
    assert stored[0].author_name == "Ada"
    assert stored[1].thread_id == "msg-1"
    assert stored[0].metadata["source"] == "teams_graph_api"
    assert [attachment["id"] for attachment in stored[0].metadata["attachments"]] == [
        "att-1",
        "hosted-1",
    ]
    assert "contentBytes" not in stored[0].metadata["attachments"][0]
    assert "contentBytes" not in stored[0].metadata["message"]["attachments"][0]
    assert "contentBytes" not in stored[0].metadata["message"]["hostedContents"][0]
    assert worker.store.get_cursor("team-a/channel-1") == "team-a/channel-1/msg-2"
    assert requests[0].headers["Authorization"] == "Bearer graph-token"
    assert result.channel_targets_polled == 1
    assert result.new_messages_discovered == 2
    assert result.trigger_wake_notified is True
    assert wake_calls == ["wake"]

    second_result = worker.run_once()

    assert second_result.new_messages_discovered == 0
    assert second_result.trigger_wake_notified is False
    assert wake_calls == ["wake"]


def test_teams_ingest_worker_notifies_configured_trigger_wake_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trigger_lock_path = tmp_path / "runtime" / "custom-trigger.lock"
    write_trigger_config(tmp_path, [], extra={"lock_path": str(trigger_lock_path)})
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/teams/team-a/channels/channel-1/messages"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-1",
                            "createdDateTime": "2026-03-30T11:00:00Z",
                            "body": {"content": "hello", "contentType": "text"},
                            "from": {"user": {"id": "user-1", "displayName": "Ada"}},
                        }
                    ]
                },
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)
    ensure_directory(worker, "root/team-a")
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["team-a/channel-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    sleeper = HostWakeSleeper(wake_socket_path_for_lock(trigger_lock_path))
    sleeper.start()
    try:
        result = worker.run_once()
    finally:
        sleeper.close()

    assert [request.url.path for request in requests] == [
        "/v1.0/teams/team-a/channels/channel-1/messages"
    ]
    assert result.new_messages_discovered == 1
    assert result.trigger_wake_notified is True


def test_teams_ingest_worker_polls_chat_targets_from_chat_allowlist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/chats/chat-1/messages"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-1",
                            "createdDateTime": "2026-03-30T11:00:00Z",
                            "body": {"content": "<p>hello</p>", "contentType": "html"},
                            "from": {"user": {"id": "user-1", "displayName": "Ada"}},
                        }
                    ]
                },
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)
    ensure_directory(worker, "root/team-a")
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_chat_allowlist=["chat:chat-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    result = worker.run_once()

    assert [request.url.path for request in requests] == ["/v1.0/chats/chat-1/messages"]
    assert worker.store.list_all_messages(channel_targets=["chat:chat-1"])
    assert worker.store.get_cursor("chat:chat-1") == "chat:chat-1/msg-1"
    assert result.channel_targets_polled == 1
    assert result.new_messages_discovered == 1
    assert result.trigger_wake_notified is False


def test_teams_ingest_worker_resolves_communication_target_allowlists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/teams/team-a/channels/channel-1/messages"):
            return httpx.Response(200, json={"value": []}, request=request)
        raise AssertionError(f"Unexpected path: {request.url.path}")

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
        }
    )
    wake_calls: list[str] = []
    worker = build_teams_ingest_worker(
        config,
        project_dir=tmp_path,
        trigger_wake_notifier=lambda: wake_calls.append("wake") or True,
    )
    ensure_directory(worker, "root/team-a")
    write_root_communication_targets(
        worker,
        [
            {
                "id": "teams-inbox",
                "provider": "teams",
                "kind": "teams",
                "address": "team-a/channel-1",
                "policy": {"capabilities": ["read"]},
            }
        ],
    )
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["teams-inbox"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    result = worker.run_once()

    assert [request.url.path for request in requests] == [
        "/v1.0/teams/team-a/channels/channel-1/messages"
    ]
    assert result.channel_targets_polled == 1
    assert result.new_messages_discovered == 0
    assert result.trigger_wake_notified is False
    assert wake_calls == []


def test_teams_ingest_worker_polls_allowlisted_direct_messages_from_configured_trigger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_trigger_config(
        tmp_path,
        [
            {
                "id": "teams-dm-trigger",
                "display_name": "Teams direct message",
                "type": "trigger:teams:direct-message",
                "sender_user_ids": ["user-allowed"],
            }
        ],
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/me/chats"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"id": "dm-chat", "chatType": "oneOnOne"},
                        {"id": "group-chat", "chatType": "group"},
                    ]
                },
                request=request,
            )
        if request.url.path.endswith("/chats/dm-chat/messages"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-1",
                            "createdDateTime": "2026-05-25T11:00:00Z",
                            "body": {"content": "hello", "contentType": "text"},
                            "from": {"user": {"id": "user-allowed", "displayName": "Ada"}},
                        },
                        {
                            "id": "msg-2",
                            "createdDateTime": "2026-05-25T11:01:00Z",
                            "body": {"content": "ignored", "contentType": "text"},
                            "from": {"user": {"id": "user-other", "displayName": "Grace"}},
                        },
                    ]
                },
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)
    ensure_directory(worker, "root/team-a")
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    result = worker.run_once()

    assert [request.url.path for request in requests] == [
        "/v1.0/me/chats",
        "/v1.0/chats/dm-chat/messages",
    ]
    stored = worker.store.list_all_messages(channel_targets=["chat:dm-chat"])
    assert [message.cursor for message in stored] == ["chat:dm-chat/msg-1"]
    assert stored[0].metadata["target_type"] == "direct_message"
    assert stored[0].metadata["chat_type"] == "oneOnOne"
    assert worker.store.get_cursor("chat:dm-chat") == "chat:dm-chat/msg-2"
    assert result.channel_targets_polled == 0
    assert result.direct_chats_polled == 1


def test_teams_ingest_worker_resolves_direct_message_sender_email(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_trigger_config(
        tmp_path,
        [
            {
                "id": "teams-dm-trigger",
                "display_name": "Teams direct message",
                "type": "trigger:teams:direct-message",
                "sender_user_ids": ["operator@example.com"],
            }
        ],
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1.0/users/operator@example.com":
            return httpx.Response(
                200,
                json={"id": "user-allowed", "userPrincipalName": "operator@example.com"},
                request=request,
            )
        if request.url.path.endswith("/me/chats"):
            return httpx.Response(
                200,
                json={"value": [{"id": "dm-chat", "chatType": "oneOnOne"}]},
                request=request,
            )
        if request.url.path.endswith("/chats/dm-chat/messages"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-1",
                            "createdDateTime": "2026-05-25T11:00:00Z",
                            "body": {"content": "email allowlist", "contentType": "text"},
                            "from": {"user": {"id": "user-allowed", "displayName": "Ada"}},
                        }
                    ]
                },
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)

    result = worker.run_once()

    assert [request.url.path for request in requests] == [
        "/v1.0/users/operator@example.com",
        "/v1.0/me/chats",
        "/v1.0/chats/dm-chat/messages",
    ]
    stored = worker.store.list_all_messages(channel_targets=["chat:dm-chat"])
    assert [message.cursor for message in stored] == ["chat:dm-chat/msg-1"]
    assert stored[0].metadata["target_type"] == "direct_message"
    assert stored[0].metadata["direct_message_sender_refs"] == [
        "operator@example.com",
        "user-allowed",
    ]
    assert result.direct_chats_polled == 1


def test_teams_ingest_worker_skips_unresolved_direct_message_sender_email(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    write_trigger_config(
        tmp_path,
        [
            {
                "id": "teams-dm-trigger",
                "display_name": "Teams direct message",
                "type": "trigger:teams:direct-message",
                "sender_user_ids": ["missing@example.com"],
            }
        ],
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1.0/users/missing@example.com":
            return httpx.Response(
                404,
                json={"error": {"code": "Request_ResourceNotFound"}},
                request=request,
            )
        if request.url.path == "/v1.0/users":
            return httpx.Response(200, json={"value": []}, request=request)
        raise AssertionError(f"Unexpected path: {request.url.path}")

    caplog.set_level(logging.WARNING, logger="agent_sandbox.messaging.teams_ingest")
    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)

    result = worker.run_once()

    assert [request.url.path for request in requests] == [
        "/v1.0/users/missing@example.com",
        "/v1.0/users",
    ]
    assert worker.store.list_all_messages() == []
    assert result.direct_chats_polled == 0
    assert "Could not resolve Teams direct-message sender user ref" in caplog.text


def test_teams_ingest_worker_polls_direct_messages_without_active_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_trigger_config(
        tmp_path,
        [
            {
                "id": "teams-dm-trigger",
                "display_name": "Teams direct message",
                "type": "trigger:teams:direct-message",
                "sender_user_ids": ["user-allowed"],
            }
        ],
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/me/chats"):
            return httpx.Response(
                200,
                json={"value": [{"id": "dm-chat", "chatType": "oneOnOne"}]},
                request=request,
            )
        if request.url.path.endswith("/chats/dm-chat/messages"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-1",
                            "createdDateTime": "2026-05-25T11:00:00Z",
                            "body": {"content": "cold start", "contentType": "text"},
                            "from": {"user": {"id": "user-allowed", "displayName": "Ada"}},
                        }
                    ]
                },
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)

    result = worker.run_once()

    assert [request.url.path for request in requests] == [
        "/v1.0/me/chats",
        "/v1.0/chats/dm-chat/messages",
    ]
    stored = worker.store.list_all_messages(channel_targets=["chat:dm-chat"])
    assert [message.cursor for message in stored] == ["chat:dm-chat/msg-1"]
    assert stored[0].metadata["target_type"] == "direct_message"
    assert result.directories_polled == 1
    assert result.sessions_seen == 0
    assert result.direct_chats_polled == 1


def test_teams_ingest_worker_skips_disabled_direct_message_triggers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_trigger_config(
        tmp_path,
        [
            {
                "id": "teams-dm-trigger",
                "display_name": "Teams direct message",
                "type": "trigger:teams:direct-message",
                "enabled": False,
                "sender_user_ids": ["user-allowed"],
            }
        ],
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"value": []}, request=request)

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)

    result = worker.run_once()

    assert requests == []
    assert result.directories_polled == 0
    assert result.direct_chats_polled == 0


def test_teams_ingest_worker_marks_allowlisted_one_on_one_chat_as_direct_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_trigger_config(
        tmp_path,
        [
            {
                "id": "teams-dm-trigger",
                "display_name": "Teams direct message",
                "type": "trigger:teams:direct-message",
                "sender_user_ids": ["user-allowed"],
            }
        ],
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/me/chats"):
            return httpx.Response(
                200,
                json={"value": [{"id": "dm-chat", "chatType": "oneOnOne"}]},
                request=request,
            )
        if request.url.path.endswith("/chats/dm-chat/messages"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-1",
                            "createdDateTime": "2026-05-25T11:00:00Z",
                            "body": {"content": "wake me", "contentType": "text"},
                            "from": {"user": {"id": "user-allowed", "displayName": "Ada"}},
                        },
                        {
                            "id": "msg-2",
                            "createdDateTime": "2026-05-25T11:01:00Z",
                            "body": {"content": "normal chat read", "contentType": "text"},
                            "from": {"user": {"id": "user-other", "displayName": "Grace"}},
                        },
                    ]
                },
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)
    ensure_directory(worker, "root/team-a")
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_chat_allowlist=["chat:dm-chat"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    result = worker.run_once()

    assert [request.url.path for request in requests] == [
        "/v1.0/me/chats",
        "/v1.0/chats/dm-chat/messages",
    ]
    stored = worker.store.list_all_messages(channel_targets=["chat:dm-chat"])
    assert [message.cursor for message in stored] == [
        "chat:dm-chat/msg-1",
        "chat:dm-chat/msg-2",
    ]
    assert {message.metadata["target_type"] for message in stored} == {"direct_message"}
    assert {message.metadata["chat_type"] for message in stored} == {"oneOnOne"}
    assert worker.store.get_cursor("chat:dm-chat") == "chat:dm-chat/msg-2"
    assert result.channel_targets_polled == 1
    assert result.direct_chats_polled == 1


def test_teams_ingest_worker_uses_history_page_budget_per_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/teams/team-a/channels/channel-1/messages"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-1",
                            "createdDateTime": "2026-03-30T11:00:00Z",
                            "body": {"content": "first", "contentType": "text"},
                            "from": {"user": {"id": "user-1"}},
                        }
                    ]
                },
                request=request,
            )
        if request.url.path.endswith("/teams/team-a/channels/channel-2/messages"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-2",
                            "createdDateTime": "2026-03-30T11:01:00Z",
                            "body": {"content": "second", "contentType": "text"},
                            "from": {"user": {"id": "user-2"}},
                        }
                    ]
                },
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
            "teams_history_page_cap": 1,
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)
    ensure_directory(worker, "root/team-a")
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["team-a/channel-1", "team-a/channel-2"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    result = worker.run_once()

    assert [request.url.path for request in requests] == [
        "/v1.0/teams/team-a/channels/channel-1/messages",
        "/v1.0/teams/team-a/channels/channel-2/messages",
    ]
    assert worker.store.get_cursor("team-a/channel-1") == "team-a/channel-1/msg-1"
    assert worker.store.get_cursor("team-a/channel-2") == "team-a/channel-2/msg-2"
    assert result.channel_targets_polled == 2


def test_teams_ingest_worker_uses_delegated_token_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"value": []}, request=request)

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setattr(
        "agent_sandbox.messaging.teams_ingest._build_delegated_teams_token_provider",
        lambda config: lambda: "delegated-token",
    )
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "delegated_graph_api",
            "teams_delegated_identity_id": "identity-1",
            "teams_delegated_client_id": "client-1",
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)
    ensure_directory(worker, "root/team-a")
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["team-a/channel-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    worker.run_once()

    assert requests[0].headers["Authorization"] == "Bearer delegated-token"


def test_teams_ingest_worker_ignores_graph_echo_of_agent_status_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/teams/team-a/channels/channel-1/messages"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "msg-status",
                            "createdDateTime": "2026-03-30T11:02:00Z",
                            "body": {"content": "status", "contentType": "text"},
                            "from": {
                                "application": {"id": "bot-app", "displayName": "Agent Sandbox"}
                            },
                        }
                    ]
                },
                request=request,
            )
        raise AssertionError(f"Unexpected path: {request.url.path}")

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
        }
    )
    wake_calls: list[str] = []
    worker = build_teams_ingest_worker(
        config,
        project_dir=tmp_path,
        trigger_wake_notifier=lambda: wake_calls.append("wake") or True,
    )
    ensure_directory(worker, "root/team-a")
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["team-a/channel-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    worker.runtime.teams_messages.put(
        "team-a/channel-1/msg-status",
        TeamsMessageEnvelope(
            cursor="team-a/channel-1/msg-status",
            message_id="msg-status",
            author_id="bot-app",
            team_id="team-a",
            channel_id="channel-1",
            thread_id="msg-status",
            content="status",
            created_at=datetime(2026, 3, 30, 11, 2, tzinfo=UTC),
            metadata={"source": "teams_graph_api_status", "session_id": "session-1"},
        ),
    )

    result = worker.run_once()

    assert worker.store.list_all_messages(channel_targets=["team-a/channel-1"]) == []
    assert worker.store.get_cursor("team-a/channel-1") == "team-a/channel-1/msg-status"
    assert result.new_messages_discovered == 0
    assert result.trigger_wake_notified is False
    assert wake_calls == []
