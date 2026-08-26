from __future__ import annotations

import base64
import json
import logging
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from evander.config import GatewayConfig
from evander.directories import DirectoryService
from evander.events.provider_models import (
    CommunicationNewMessageTriggerDefinition,
    TeamsDirectMessageTriggerDefinition,
    TeamsNewMessageTriggerDefinition,
)
from evander.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from evander.models import SessionManifest, TeamsMessageEnvelope, ToolName
from evander.messaging.teams_ingest import (
    _classify_graph_poll_error,
    _compute_target_backoff_seconds,
    _notification_message_locator,
    _notification_resource_target,
    _teams_graph_poll_scope,
    _teams_poll_health_key,
    build_teams_ingest_worker,
)
from evander.messaging.teams_inbox_store import (
    TeamsInboxMessage,
    TeamsNotificationQueueEntry,
    TeamsOwnedSubscription,
)
from evander.orchestration.host_polling import HostWakeSleeper, wake_socket_path_for_lock
from evander.services import TeamsAuthenticationError
from evander.triggers.providers.base import TriggerRuntimeContext
from evander.triggers.providers.communication_new_message import (
    CommunicationNewMessageTriggerProvider,
)
from evander.triggers.providers.teams_direct_message import TeamsDirectMessageTriggerProvider
from evander.triggers.providers.teams_new_message import TeamsNewMessageTriggerProvider
from evander.triggers.queue import SqliteTriggerEventQueue
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


def patch_worker_httpx_client(
    monkeypatch: pytest.MonkeyPatch,
    transport: httpx.BaseTransport,
) -> None:
    real_client = httpx.Client
    monkeypatch.setattr(
        "evander.messaging.teams_ingest.httpx.Client",
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
    trigger_config_path.write_text(
        json.dumps(canonical_workflow_payload(payload)),
        encoding="utf-8",
    )


def write_teams_ingest_worker_config(
    project_dir: Path, *, max_concurrent_target_polls: int
) -> None:
    config_path = project_dir / "config" / "teams-ingest" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps({"max_concurrent_target_polls": max_concurrent_target_polls}),
        encoding="utf-8",
    )


@pytest.mark.parametrize(
    ("resource", "expected"),
    [
        (
            "teams('team-a')/channels('channel-a')/messages('root-message')",
            ("root-message", None),
        ),
        (
            "teams('team-a')/channels('channel-a')/messages('root')/replies('reply')",
            ("reply", "root"),
        ),
        ("chats('chat-a')/messages('chat-message')", ("chat-message", None)),
    ],
)
def test_notification_message_locator_supports_channel_roots_replies_and_chats(
    resource: str,
    expected: tuple[str, str | None],
) -> None:
    assert _notification_message_locator(resource) == expected


@pytest.mark.parametrize(
    ("resource", "expected"),
    [
        (
            "teams('team-a')/channels('channel-a')/messages('message-a')",
            "team-a/channel-a",
        ),
        ("/teams/team-a/channels/channel-a/messages/message-a", "team-a/channel-a"),
        ("chats('chat-a')/messages('message-a')", "chat:chat-a"),
    ],
)
def test_notification_resource_target_supports_graph_resource_shapes(
    resource: str,
    expected: str,
) -> None:
    assert _notification_resource_target(resource) == expected


def test_healthy_notification_target_uses_reconciliation_poll_interval(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "teams-ingest" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "change_notifications_enabled": True,
                "change_notification_public_base_url": "https://notifications.example.test",
                "change_notification_reconciliation_poll_interval_seconds": 60,
            }
        ),
        encoding="utf-8",
    )
    worker = build_teams_ingest_worker(
        GatewayConfig.model_validate({"runtime_dir": tmp_path / "runtime"}),
        project_dir=tmp_path,
    )
    now = datetime.now(UTC)
    poll_scope = "root:test"
    target = "team-a/channel-a"

    assert worker._reconciliation_poll_due(
        poll_scope=poll_scope,
        target_kind="channel",
        target=target,
        now=now,
    )
    worker.store.record_poll_success(
        _teams_poll_health_key(poll_scope, "channel", target),
        succeeded_at=now,
    )
    assert not worker._reconciliation_poll_due(
        poll_scope=poll_scope,
        target_kind="channel",
        target=target,
        now=now + timedelta(seconds=59),
    )
    assert worker._reconciliation_poll_due(
        poll_scope=poll_scope,
        target_kind="channel",
        target=target,
        now=now + timedelta(seconds=60),
    )
    worker.worker_config = worker.worker_config.model_copy(
        update={"change_notifications_enabled": False}
    )
    assert worker._reconciliation_poll_due(
        poll_scope=poll_scope,
        target_kind="channel",
        target=target,
        now=now + timedelta(seconds=1),
    )


def test_delayed_unseen_message_is_ingested_without_regressing_target_cursor(
    tmp_path: Path,
) -> None:
    worker = build_teams_ingest_worker(
        GatewayConfig.model_validate({"runtime_dir": tmp_path / "runtime"}),
        project_dir=tmp_path,
    )
    target = "team-a/channel-a"
    newer_at = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    worker.store.set_cursor(
        target,
        f"{target}/newer",
        created_at=newer_at,
    )
    delayed = TeamsInboxMessage(
        cursor=f"{target}/delayed",
        message_id="delayed",
        team_id="team-a",
        channel_id="channel-a",
        thread_id="delayed",
        author_id="operator-a",
        content="arrived out of order",
        created_at=newer_at - timedelta(minutes=1),
        metadata={"source": "teams_graph_api", "target": target},
    )
    snapshot = worker._capture_trigger_config_snapshot(captured_at=datetime.now(UTC))

    discovered = worker._ingest_message_candidates(
        [delayed],
        channel_target=target,
        cursor_state=worker.store.get_cursor_state(target),
        trigger_snapshot=snapshot,
    )

    assert discovered == 1
    assert worker.store.get_message(delayed.cursor) is not None
    cursor_state = worker.store.get_cursor_state(target)
    assert cursor_state is not None
    assert cursor_state.cursor == f"{target}/newer"


def test_disabled_worker_finishes_already_queued_lifecycle_notifications(tmp_path: Path) -> None:
    worker = build_teams_ingest_worker(
        GatewayConfig.model_validate({"runtime_dir": tmp_path / "runtime"}),
        project_dir=tmp_path,
    )
    now = datetime.now(UTC)
    store = worker._notification_store
    assert store is not None
    store.upsert_owned_subscription(
        TeamsOwnedSubscription(
            subscription_id="owned-subscription",
            owner_id=store.notification_owner_id(),
            target="chat:chat-a",
            resource="/chats/chat-a/messages",
            directory_path="root",
            tenant_id="tenant-a",
            expires_at=now + timedelta(hours=48),
            client_state_sha256="0" * 64,
            created_at=now,
            updated_at=now,
        )
    )
    store.enqueue_notification(
        TeamsNotificationQueueEntry(
            idempotency_key="lifecycle-queued-before-disable",
            notification_kind="lifecycle",
            subscription_id="owned-subscription",
            tenant_id="tenant-a",
            resource="/chats/chat-a/messages",
            lifecycle_event="missed",
            received_at=now,
            next_attempt_at=now,
        )
    )

    result = worker.run_once()

    assert result.notifications_processed == 1
    assert store.lease_notifications(now=datetime.now(UTC)) == []


@pytest.mark.parametrize(
    ("target", "resource", "expected_path", "expected_thread_id"),
    [
        (
            "team-a/channel-a",
            "teams('team-a')/channels('channel-a')/messages('root-message')",
            "/v1.0/teams/team-a/channels/channel-a/messages/root-message",
            "root-message",
        ),
        (
            "team-a/channel-a",
            "teams('team-a')/channels('channel-a')/messages('root-message')/replies('reply')",
            "/v1.0/teams/team-a/channels/channel-a/messages/root-message/replies/reply",
            "root-message",
        ),
        (
            "chat:chat-a",
            "chats('chat-a')/messages('chat-message')",
            "/v1.0/chats/chat-a/messages/chat-message",
            None,
        ),
    ],
)
def test_message_notification_fetches_exact_authorized_message_through_shared_ingestor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    resource: str,
    expected_path: str,
    expected_thread_id: str | None,
) -> None:
    tenant_payload = base64.urlsafe_b64encode(json.dumps({"tid": "tenant-a"}).encode()).decode()
    monkeypatch.setenv(
        "GRAPH_TEAMS_ACCESS_TOKEN",
        f"header.{tenant_payload.rstrip('=')}.signature",
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == expected_path
        message_id = expected_path.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            request=request,
            json={
                "id": message_id,
                "createdDateTime": "2026-08-26T12:00:00Z",
                "body": {"content": "notification message", "contentType": "text"},
                "from": {"user": {"id": "operator-a", "displayName": "Ada"}},
            },
        )

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": tmp_path / "runtime",
            "teams_backend": "graph_api",
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)
    worker.runtime.directories.ensure_root()
    session = SessionManifest(
        session_id="session-1",
        directory_path="root",
        allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
        teams_channel_allowlist=[target],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    worker.session_repository.upsert_session(session)
    store = worker._notification_store
    assert store is not None
    now = datetime.now(UTC)
    store.upsert_owned_subscription(
        TeamsOwnedSubscription(
            subscription_id="owned-subscription",
            owner_id=store.notification_owner_id(),
            target=target,
            resource="unused",
            directory_path="root",
            tenant_id="tenant-a",
            expires_at=now + timedelta(hours=48),
            client_state_sha256="0" * 64,
            created_at=now,
            updated_at=now,
        )
    )
    entry = TeamsNotificationQueueEntry(
        idempotency_key="notification-1",
        notification_kind="message",
        subscription_id="owned-subscription",
        tenant_id="tenant-a",
        resource=resource,
        change_type="created",
        received_at=now,
        next_attempt_at=now,
    )
    snapshot = worker._capture_trigger_config_snapshot(captured_at=now)

    discovered = worker._process_message_notification(
        entry,
        active_sessions=[session],
        trigger_snapshot=snapshot,
        directories=["root"],
    )

    assert discovered == 1
    assert len(requests) == 1
    messages = worker.store.list_all_messages(channel_targets=[target])
    assert len(messages) == 1
    assert messages[0].thread_id == expected_thread_id


def test_message_notification_does_not_fetch_removed_or_unauthorized_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_payload = base64.urlsafe_b64encode(json.dumps({"tid": "tenant-a"}).encode()).decode()
    monkeypatch.setenv(
        "GRAPH_TEAMS_ACCESS_TOKEN",
        f"header.{tenant_payload.rstrip('=')}.signature",
    )
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": tmp_path / "runtime",
            "teams_backend": "graph_api",
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)
    worker.runtime.directories.ensure_root()
    store = worker._notification_store
    assert store is not None
    now = datetime.now(UTC)
    store.upsert_owned_subscription(
        TeamsOwnedSubscription(
            subscription_id="removed-target-subscription",
            owner_id=store.notification_owner_id(),
            target="team-a/removed-channel",
            resource="/teams/team-a/channels/removed-channel/messages",
            directory_path="root",
            tenant_id="tenant-a",
            expires_at=now + timedelta(hours=48),
            client_state_sha256="0" * 64,
            created_at=now,
            updated_at=now,
        )
    )
    entry = TeamsNotificationQueueEntry(
        idempotency_key="unauthorized-notification",
        notification_kind="message",
        subscription_id="removed-target-subscription",
        tenant_id="tenant-a",
        resource="teams('team-a')/channels('removed-channel')/messages('message-a')",
        change_type="created",
        received_at=now,
        next_attempt_at=now,
    )
    snapshot = worker._capture_trigger_config_snapshot(captured_at=now)

    assert (
        worker._process_message_notification(
            entry,
            active_sessions=[],
            trigger_snapshot=snapshot,
            directories=["root"],
        )
        == 0
    )


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


def test_teams_ingest_worker_resolves_relative_trigger_wake_socket_from_project_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_dir = tmp_path / "project"
    relative_trigger_lock_path = Path("runtime") / "custom-trigger.lock"
    write_trigger_config(
        project_dir,
        [],
        extra={"lock_path": str(relative_trigger_lock_path)},
    )
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    monkeypatch.chdir(outside_dir)
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
            "runtime_dir": str(project_dir / "runtime"),
            "teams_backend": "graph_api",
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=project_dir)
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
    sleeper = HostWakeSleeper(wake_socket_path_for_lock(project_dir / relative_trigger_lock_path))
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


def test_teams_ingest_worker_backs_off_only_the_failing_graph_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/channels/bad/messages"):
            raise httpx.ReadTimeout("unhealthy endpoint", request=request)
        if request.url.path.endswith("/channels/good/messages"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "healthy-message",
                            "createdDateTime": "2026-08-13T10:00:00Z",
                            "body": {"content": "healthy", "contentType": "text"},
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
            "teams_retry_count": 0,
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
            teams_channel_allowlist=["team-a/bad", "team-a/good"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    first = worker.run_once()

    assert first.targets_failed == 1
    assert first.new_messages_discovered == 1
    assert first.trigger_wake_notified is True
    assert wake_calls == ["wake"]
    assert worker.store.list_all_messages(channel_targets=["team-a/good"])
    failed_target_health = next(
        state
        for key, state in worker.store.read_raw()["poll_health"].items()
        if key.endswith("|channel|team-a/bad")
    )
    assert failed_target_health["consecutive_failures"] == 1
    assert (
        datetime.fromisoformat(failed_target_health["next_attempt_at"])
        - datetime.fromisoformat(failed_target_health["last_failed_at"])
    ).total_seconds() == 5.0

    requests.clear()
    second_worker = build_teams_ingest_worker(
        config,
        project_dir=tmp_path,
        trigger_wake_notifier=lambda: wake_calls.append("wake") or True,
    )
    second = second_worker.run_once()

    assert second.targets_skipped_backoff == 1
    assert requests == ["/v1.0/teams/team-a/channels/good/messages"]


def test_teams_ingest_worker_honors_retry_after_for_only_the_throttled_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    throttled_attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal throttled_attempts
        if request.url.path.endswith("/channels/throttled/messages"):
            throttled_attempts += 1
            return httpx.Response(
                429,
                headers={"Retry-After": "120"},
                request=request,
            )
        if request.url.path.endswith("/channels/healthy/messages"):
            return httpx.Response(200, json={"value": []}, request=request)
        raise AssertionError(f"Unexpected path: {request.url.path}")

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setattr("evander.services.time.sleep", lambda _seconds: None)
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
            "teams_retry_count": 2,
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)
    ensure_directory(worker, "root/team-a")
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["team-a/throttled", "team-a/healthy"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )

    before = datetime.now(UTC)
    result = worker.run_once()

    unhealthy = next(
        state
        for key, state in worker.store.read_raw()["poll_health"].items()
        if key.endswith("|channel|team-a/throttled")
    )
    assert result.targets_failed == 1
    assert result.channel_targets_polled == 2
    assert throttled_attempts == 1
    assert datetime.fromisoformat(unhealthy["next_attempt_at"]) >= before + timedelta(seconds=119)


def test_teams_ingest_worker_resets_target_backoff_after_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = tmp_path / "config" / "teams-ingest" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "target_backoff_initial_seconds": 0,
                "target_backoff_max_seconds": 0,
                "target_transient_backoff_initial_seconds": 0,
                "target_transient_backoff_max_seconds": 0,
            }
        ),
        encoding="utf-8",
    )
    healthy = False

    def handler(request: httpx.Request) -> httpx.Response:
        if not healthy:
            raise httpx.ReadTimeout("temporary failure", request=request)
        return httpx.Response(200, json={"value": []}, request=request)

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
            "teams_retry_count": 0,
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

    first = worker.run_once()
    healthy = True
    second = worker.run_once()

    target_health = next(
        state
        for key, state in worker.store.read_raw()["poll_health"].items()
        if key.endswith("|channel|team-a/channel-1")
    )
    assert first.targets_failed == 1
    assert second.targets_recovered == 1
    assert target_health["consecutive_failures"] == 0
    assert target_health["next_attempt_at"] is None


def test_teams_ingest_worker_uses_cached_direct_chats_when_discovery_fails(
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
                "targets": [{"target": "chat:dm-chat"}],
                "sender_user_ids": ["user-allowed"],
            }
        ],
    )
    discovery_healthy = True
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/me/chats"):
            if not discovery_healthy:
                raise httpx.ReadTimeout("discovery unavailable", request=request)
            return httpx.Response(
                200,
                json={"value": [{"id": "dm-chat", "chatType": "oneOnOne"}]},
                request=request,
            )
        if request.url.path.endswith("/chats/dm-chat/messages"):
            return httpx.Response(
                200,
                json={
                    "value": []
                    if discovery_healthy
                    else [
                        {
                            "id": "msg-after-discovery-failure",
                            "createdDateTime": "2026-08-13T10:01:00Z",
                            "body": {"content": "still healthy", "contentType": "text"},
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
            "teams_retry_count": 0,
        }
    )
    wake_calls: list[str] = []
    worker = build_teams_ingest_worker(
        config,
        project_dir=tmp_path,
        trigger_wake_notifier=lambda: wake_calls.append("wake") or True,
    )
    worker.run_once()

    discovery_healthy = False
    requests.clear()
    result = worker.run_once()

    assert result.targets_failed == 1
    assert result.new_messages_discovered == 1
    assert result.trigger_wake_notified is True
    assert requests == ["/v1.0/me/chats", "/v1.0/chats/dm-chat/messages"]
    assert wake_calls == ["wake"]


def test_teams_ingest_worker_polls_communication_trigger_targets_without_active_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_trigger_config(
        tmp_path,
        [
            {
                "id": "communication-message-trigger",
                "display_name": "Communication message",
                "type": "trigger:communication:new-message",
                "communication_target_ids": ["teams-inbox"],
                "directory_path": "root",
            }
        ],
    )
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
                            "body": {"content": "trigger target", "contentType": "text"},
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
    wake_calls: list[str] = []
    worker = build_teams_ingest_worker(
        config,
        project_dir=tmp_path,
        trigger_wake_notifier=lambda: wake_calls.append("wake") or True,
    )
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

    result = worker.run_once()

    assert [request.url.path for request in requests] == [
        "/v1.0/teams/team-a/channels/channel-1/messages"
    ]
    stored = worker.store.list_all_messages(channel_targets=["team-a/channel-1"])
    assert [message.cursor for message in stored] == ["team-a/channel-1/msg-1"]
    assert result.directories_polled == 1
    assert result.sessions_seen == 0
    assert result.channel_targets_polled == 1
    assert result.new_messages_discovered == 1
    assert result.trigger_wake_notified is True
    assert wake_calls == ["wake"]


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
                "targets": [{"target": "chat:dm-chat"}],
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
                "targets": [{"target": "chat:dm-chat"}],
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
                "targets": [{"target": "chat:dm-chat"}],
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

    caplog.set_level(logging.WARNING, logger="evander.messaging.teams_ingest")
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
                "targets": [{"target": "chat:dm-chat"}],
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
                "targets": [{"target": "chat:dm-chat"}],
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
                "targets": [{"target": "chat:dm-chat"}],
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


@pytest.mark.parametrize("configured_limit", [None, 1])
def test_teams_ingest_worker_default_and_explicit_one_poll_targets_serially(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured_limit: int | None,
) -> None:
    if configured_limit is not None:
        write_teams_ingest_worker_config(
            tmp_path,
            max_concurrent_target_polls=configured_limit,
        )

    active_requests = 0
    maximum_active_requests = 0
    request_paths: list[str] = []
    request_lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active_requests, maximum_active_requests
        with request_lock:
            active_requests += 1
            maximum_active_requests = max(maximum_active_requests, active_requests)
            request_paths.append(request.url.path)
        with request_lock:
            active_requests -= 1
        return httpx.Response(200, json={"value": []}, request=request)

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
            "teams_retry_count": 0,
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
        )
    )

    result = worker.run_once()

    assert worker.worker_config.max_concurrent_target_polls == 1
    assert maximum_active_requests == 1
    assert request_paths == [
        "/v1.0/teams/team-a/channels/channel-1/messages",
        "/v1.0/teams/team-a/channels/channel-2/messages",
    ]
    assert result.channel_targets_polled == 2


def test_teams_ingest_worker_bounds_concurrent_target_polls_globally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_teams_ingest_worker_config(tmp_path, max_concurrent_target_polls=2)
    active_requests = 0
    maximum_active_requests = 0
    request_paths: list[str] = []
    request_lock = threading.Lock()
    two_requests_started = threading.Event()
    release_requests = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active_requests, maximum_active_requests
        with request_lock:
            active_requests += 1
            maximum_active_requests = max(maximum_active_requests, active_requests)
            request_paths.append(request.url.path)
            if active_requests == 2:
                two_requests_started.set()
        assert release_requests.wait(timeout=5)
        with request_lock:
            active_requests -= 1
        return httpx.Response(200, json={"value": []}, request=request)

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
            "teams_retry_count": 0,
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)
    ensure_directory(worker, "root/team-a")
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=[
                "team-a/channel-1",
                "team-a/channel-2",
                "team-a/channel-3",
            ],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    outcome: dict[str, object] = {}

    def run_worker() -> None:
        try:
            outcome["result"] = worker.run_once()
        except BaseException as exc:  # pragma: no cover - asserted after joining the thread.
            outcome["exception"] = exc

    worker_thread = threading.Thread(target=run_worker)
    worker_thread.start()
    try:
        assert two_requests_started.wait(timeout=5)
        with request_lock:
            assert len(request_paths) == 2
            assert maximum_active_requests == 2
        release_requests.set()
    finally:
        release_requests.set()
        worker_thread.join(timeout=5)

    assert not worker_thread.is_alive()
    assert "exception" not in outcome
    assert worker.worker_config.max_concurrent_target_polls == 2
    assert maximum_active_requests == 2
    assert len(request_paths) == 3
    assert outcome["result"].channel_targets_polled == 3


def test_teams_ingest_worker_concurrent_polls_persist_cursors_and_wake_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_teams_ingest_worker_config(tmp_path, max_concurrent_target_polls=2)
    active_requests = 0
    request_lock = threading.Lock()
    two_requests_started = threading.Event()
    release_requests = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active_requests
        channel_id = request.url.path.rsplit("/", 2)[-2]
        with request_lock:
            active_requests += 1
            if active_requests == 2:
                two_requests_started.set()
        assert release_requests.wait(timeout=5)
        with request_lock:
            active_requests -= 1
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": f"message-{channel_id}",
                        "createdDateTime": "2026-08-14T10:00:00Z",
                        "body": {"content": channel_id, "contentType": "text"},
                        "from": {"user": {"id": "user-1"}},
                    }
                ]
            },
            request=request,
        )

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
            "teams_retry_count": 0,
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
            teams_channel_allowlist=["team-a/channel-1", "team-a/channel-2"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    outcome: dict[str, object] = {}

    def run_worker() -> None:
        try:
            outcome["result"] = worker.run_once()
        except BaseException as exc:  # pragma: no cover - asserted after joining the thread.
            outcome["exception"] = exc

    worker_thread = threading.Thread(target=run_worker)
    worker_thread.start()
    try:
        assert two_requests_started.wait(timeout=5)
        release_requests.set()
    finally:
        release_requests.set()
        worker_thread.join(timeout=5)

    assert not worker_thread.is_alive()
    assert "exception" not in outcome
    result = outcome["result"]
    assert result.new_messages_discovered == 2
    assert result.trigger_wake_notified is True
    assert wake_calls == ["wake"]
    assert [message.cursor for message in worker.store.list_all_messages()] == [
        "team-a/channel-1/message-channel-1",
        "team-a/channel-2/message-channel-2",
    ]
    assert worker.store.get_cursor("team-a/channel-1") == "team-a/channel-1/message-channel-1"
    assert worker.store.get_cursor("team-a/channel-2") == "team-a/channel-2/message-channel-2"

    second_result = worker.run_once()

    assert second_result.new_messages_discovered == 0
    assert len(worker.store.list_all_messages()) == 2
    assert wake_calls == ["wake"]


def test_teams_ingest_worker_concurrent_throttle_does_not_block_healthy_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_teams_ingest_worker_config(tmp_path, max_concurrent_target_polls=2)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/channels/throttled/messages"):
            return httpx.Response(429, headers={"Retry-After": "120"}, request=request)
        if request.url.path.endswith("/channels/healthy/messages"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "healthy-message",
                            "createdDateTime": "2026-08-14T10:01:00Z",
                            "body": {"content": "healthy", "contentType": "text"},
                            "from": {"user": {"id": "user-1"}},
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
            "teams_retry_count": 0,
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)
    ensure_directory(worker, "root/team-a")
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["team-a/throttled", "team-a/healthy"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    result = worker.run_once()

    assert result.targets_failed == 1
    assert result.new_messages_discovered == 1
    assert worker.store.get_cursor("team-a/healthy") == "team-a/healthy/healthy-message"
    throttled_health = next(
        state
        for key, state in worker.store.read_raw()["poll_health"].items()
        if key.endswith("|channel|team-a/throttled")
    )
    assert throttled_health["consecutive_failures"] == 1


def test_teams_ingest_worker_coalesces_concurrent_credential_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_teams_ingest_worker_config(tmp_path, max_concurrent_target_polls=3)
    requests_started = threading.Barrier(3)

    def handler(request: httpx.Request) -> httpx.Response:
        requests_started.wait(timeout=5)
        return httpx.Response(401, request=request)

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
            "teams_retry_count": 0,
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)
    ensure_directory(worker, "root/team-a")
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=[
                "team-a/channel-1",
                "team-a/channel-2",
                "team-a/channel-3",
            ],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    result = worker.run_once()

    auth_health = [
        state
        for key, state in worker.store.read_raw()["poll_health"].items()
        if key.endswith("|auth|credential-set")
    ]
    assert result.targets_failed == 3
    assert len(auth_health) == 1
    assert auth_health[0]["consecutive_failures"] == 1


def test_teams_ingest_worker_drains_successful_futures_before_propagating_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_teams_ingest_worker_config(tmp_path, max_concurrent_target_polls=2)
    monkeypatch.setattr(
        "evander.messaging.teams_ingest.as_completed",
        lambda futures: iter(futures),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/channels/broken/messages"):
            return httpx.Response(200, content=b"{", request=request)
        if request.url.path.endswith("/channels/healthy/messages"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "healthy-message",
                            "createdDateTime": "2026-08-14T10:02:00Z",
                            "body": {"content": "healthy", "contentType": "text"},
                            "from": {"user": {"id": "user-1"}},
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
            "teams_retry_count": 0,
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
            teams_channel_allowlist=["team-a/broken", "team-a/healthy"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    with pytest.raises(json.JSONDecodeError):
        worker.run_once()

    assert [message.cursor for message in worker.store.list_all_messages()] == [
        "team-a/healthy/healthy-message"
    ]
    assert worker.store.get_cursor("team-a/healthy") == "team-a/healthy/healthy-message"
    assert wake_calls == ["wake"]


def test_teams_ingest_worker_finishes_discovery_before_concurrent_unique_chat_polls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_teams_ingest_worker_config(tmp_path, max_concurrent_target_polls=2)
    write_trigger_config(
        tmp_path,
        [
            {
                "id": "teams-dm-trigger",
                "display_name": "Teams direct message",
                "type": "trigger:teams:direct-message",
                "targets": [{"target": "chat:chat-one"}],
                "sender_user_ids": ["user-allowed"],
            }
        ],
    )
    request_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        request_paths.append(request.url.path)
        if request.url.path.endswith("/me/chats"):
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"id": "chat-one", "chatType": "oneOnOne"},
                        {"id": "chat-one", "chatType": "oneOnOne"},
                        {"id": "chat-two", "chatType": "oneOnOne"},
                    ]
                },
                request=request,
            )
        if request.url.path.endswith("/chats/chat-one/messages"):
            return httpx.Response(200, json={"value": []}, request=request)
        if request.url.path.endswith("/chats/chat-two/messages"):
            return httpx.Response(200, json={"value": []}, request=request)
        raise AssertionError(f"Unexpected path: {request.url.path}")

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
            "teams_retry_count": 0,
        }
    )
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)
    ensure_directory(worker, "root/team-a")
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_chat_allowlist=["chat:chat-one"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    result = worker.run_once()

    assert request_paths[0] == "/v1.0/me/chats"
    assert request_paths.count("/v1.0/chats/chat-one/messages") == 1
    assert set(request_paths[1:]) == {
        "/v1.0/chats/chat-one/messages",
        "/v1.0/chats/chat-two/messages",
    }
    assert result.direct_chats_polled == 2


def test_teams_graph_poll_errors_keep_authentication_and_resource_scopes_distinct() -> None:
    request = httpx.Request("GET", "https://graph.microsoft.test/resource")
    unauthorized = httpx.HTTPStatusError(
        "unauthorized",
        request=request,
        response=httpx.Response(401, request=request),
    )
    forbidden = httpx.HTTPStatusError(
        "forbidden",
        request=request,
        response=httpx.Response(403, request=request),
    )

    assert _classify_graph_poll_error(TeamsAuthenticationError("revoked")) == (
        "authentication",
        None,
        True,
    )
    assert _classify_graph_poll_error(unauthorized) == ("http_401", None, True)
    assert _classify_graph_poll_error(forbidden) == ("http_403", None, False)


def test_teams_target_backoff_caps_before_large_exponentiation() -> None:
    assert (
        _compute_target_backoff_seconds(
            failure_count=1,
            initial_seconds=60,
            max_seconds=900,
        )
        == 60
    )
    assert (
        _compute_target_backoff_seconds(
            failure_count=1025,
            initial_seconds=60,
            max_seconds=900,
        )
        == 900
    )


def test_teams_transient_target_backoff_uses_short_capped_progression() -> None:
    assert [
        _compute_target_backoff_seconds(
            failure_count=failure_count,
            initial_seconds=5,
            max_seconds=30,
        )
        for failure_count in range(1, 6)
    ] == [5, 10, 20, 30, 30]


def test_teams_delegated_poll_scope_changes_with_identity(tmp_path: Path) -> None:
    base = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "delegated_graph_api",
            "teams_delegated_identity_id": "identity-1",
            "teams_delegated_client_id": "client-1",
        }
    )

    first = _teams_graph_poll_scope(base, directory_path="root/team-a")
    second = _teams_graph_poll_scope(
        base.model_copy(update={"teams_delegated_identity_id": "identity-2"}),
        directory_path="root/team-a",
    )

    assert first != second


def test_teams_ingest_worker_keeps_delegated_auth_failure_to_its_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"value": []}, request=request)

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))

    def delegated_token_provider(provider_config: GatewayConfig):
        def resolve() -> str:
            if provider_config.teams_delegated_identity_id == "identity-broken":
                raise TeamsAuthenticationError("delegated credential was revoked")
            return "delegated-token"

        return resolve

    monkeypatch.setattr(
        "evander.messaging.teams_ingest._build_delegated_teams_token_provider",
        delegated_token_provider,
    )
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "delegated_graph_api",
            "teams_delegated_identity_id": "identity-broken",
            "teams_delegated_client_id": "client-1",
            "teams_retry_count": 0,
        }
    )
    healthy_config = config.model_copy(update={"teams_delegated_identity_id": "identity-healthy"})
    worker = build_teams_ingest_worker(config, project_dir=tmp_path)
    ensure_directory(worker, "root/team-a")
    ensure_directory(worker, "root/team-b")
    monkeypatch.setattr(
        worker.runtime.provider_config,
        "resolve_for_directory",
        lambda directory_path: config if directory_path == "root/team-a" else healthy_config,
    )
    for session_id, directory_path, channel_target in (
        ("session-broken", "root/team-a", "team-a/channel-1"),
        ("session-healthy", "root/team-b", "team-b/channel-1"),
    ):
        worker.session_repository.upsert_session(
            SessionManifest(
                session_id=session_id,
                directory_path=directory_path,
                allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
                teams_channel_allowlist=[channel_target],
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            ),
        )

    result = worker.run_once()

    assert result.targets_failed == 1
    assert [request.url.path for request in requests] == [
        "/v1.0/teams/team-b/channels/channel-1/messages"
    ]


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
        "evander.messaging.teams_ingest._build_delegated_teams_token_provider",
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
                            "from": {"application": {"id": "bot-app", "displayName": "Evander"}},
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


def test_teams_ingest_worker_directly_publishes_all_matching_teams_trigger_types(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_at = datetime.now(UTC) + timedelta(minutes=5)
    created_at_text = created_at.isoformat().replace("+00:00", "Z")
    trigger_payloads: list[dict[str, object]] = [
        {
            "id": "teams-new",
            "display_name": "Teams message",
            "type": "trigger:teams:new-message",
            "targets": [
                {
                    "target": "chat:dm-chat",
                    "message_filter": {"content": {"contains": "deploy"}},
                }
            ],
            "user_ids": ["user-a"],
        },
        {
            "id": "teams-dm",
            "display_name": "Teams direct message",
            "type": "trigger:teams:direct-message",
            "targets": [{"target": "chat:dm-chat"}],
            "sender_user_ids": ["user-a"],
            "message_filter": {"content": {"contains": "deploy"}},
        },
        {
            "id": "communication-teams",
            "display_name": "Communication message",
            "type": "trigger:communication:new-message",
            "communication_target_ids": ["teams-chat"],
            "directory_path": "root",
            "author_ids": ["user-a"],
            "content_starts_with": "Deploy",
        },
        {
            "id": "disabled-teams",
            "display_name": "Disabled Teams message",
            "type": "trigger:teams:new-message",
            "enabled": False,
            "targets": [{"target": "chat:dm-chat"}],
        },
        {
            "id": "unmatched-teams",
            "display_name": "Unmatched Teams message",
            "type": "trigger:teams:new-message",
            "targets": [
                {
                    "target": "chat:dm-chat",
                    "message_filter": {"content": {"contains": "never-match"}},
                }
            ],
        },
    ]
    write_trigger_config(tmp_path, trigger_payloads)

    def handler(request: httpx.Request) -> httpx.Response:
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
                            "createdDateTime": created_at_text,
                            "body": {"content": "Deploy the release", "contentType": "text"},
                            "from": {"user": {"id": "user-a", "displayName": "Ada"}},
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
    queue = SqliteTriggerEventQueue(tmp_path / "runtime" / "direct-events.json")
    effect_wakes: list[str] = []
    trigger_wakes: list[str] = []
    worker = build_teams_ingest_worker(
        config,
        project_dir=tmp_path,
        event_queue=queue,
        effect_wake_notifier=lambda: effect_wakes.append("wake") or True,
        trigger_wake_notifier=lambda: trigger_wakes.append("wake") or True,
    )
    write_root_communication_targets(
        worker,
        [
            {
                "id": "teams-chat",
                "provider": "teams",
                "kind": "teams",
                "address": "chat:dm-chat",
                "policy": {"capabilities": ["read"]},
            }
        ],
    )

    first = worker.run_once()

    assert first.new_messages_discovered == 1
    assert first.messages_matched == 1
    assert first.events_created == 3
    assert first.events_deduplicated == 0
    assert first.publication_failures == 0
    assert first.effect_wake_notified is True
    assert first.trigger_wake_notified is True
    assert effect_wakes == ["wake"]
    assert trigger_wakes == ["wake"]
    assert not (tmp_path / "runtime" / "trigger-cursors-teams.json").exists()
    assert not (tmp_path / "runtime" / "trigger-cursors-communication.json").exists()

    queued = {record.event_id: record for record in queue.list_pending()}
    assert set(queued) == {
        "teams-new:chat:dm-chat/msg-1",
        "teams-dm:chat:dm-chat/msg-1",
        (f"communication-teams:teams:teams-chat:msg-1:{created_at_text}"),
    }
    assert {record.directory_path for record in queued.values()} == {"root"}
    assert all(record.config_revision for record in queued.values())

    second = worker.run_once()

    assert second.new_messages_discovered == 0
    assert second.events_created == 0
    assert second.events_deduplicated == 0
    assert effect_wakes == ["wake"]
    assert trigger_wakes == ["wake"]

    context = TriggerRuntimeContext(resolved_config=worker.config, env={})
    provider_events = []
    providers = [
        (
            TeamsNewMessageTriggerProvider(),
            TeamsNewMessageTriggerDefinition.model_validate(trigger_payloads[0]),
        ),
        (
            TeamsDirectMessageTriggerProvider(),
            TeamsDirectMessageTriggerDefinition.model_validate(trigger_payloads[1]),
        ),
        (
            CommunicationNewMessageTriggerProvider(),
            CommunicationNewMessageTriggerDefinition.model_validate(trigger_payloads[2]),
        ),
    ]
    for provider, definition in providers:
        provider.initialize(definition, context)
        events = provider.poll()
        assert len(events) == 1
        event = events[0]
        provider_events.append(event)
        assert queue.publish_result(event).created is False
        provider.acknowledge_published_event(event)

    assert {event.event_id: event.payload for event in provider_events} == {
        event_id: record.payload for event_id, record in queued.items()
    }
    assert len(queue.list_pending()) == 3


def test_teams_ingest_worker_polls_teams_trigger_in_workflow_graph_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_trigger_config(
        tmp_path,
        [
            {
                "id": "teams-new",
                "display_name": "Teams message",
                "type": "trigger:teams:new-message",
                "targets": [{"target": "team-a/channel-1"}],
            }
        ],
        extra={
            "workflow_graphs": [
                {
                    "id": "graph-teams-new",
                    "name": "Scoped Teams trigger",
                    "directory_path": "root/team-a",
                    "node_ids": ["teams-new"],
                }
            ]
        },
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "msg-1",
                        "createdDateTime": "2026-08-14T10:00:00Z",
                        "body": {"content": "hello", "contentType": "text"},
                        "from": {"user": {"id": "user-a", "displayName": "Ada"}},
                    }
                ]
            },
            request=request,
        )

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("TEAM_GRAPH_TEAMS_ACCESS_TOKEN", "team-graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "stub",
        }
    )
    team_config = config.model_copy(
        update={
            "teams_backend": "graph_api",
            "teams_graph_access_token_env": "TEAM_GRAPH_TEAMS_ACCESS_TOKEN",
        }
    )
    queue = SqliteTriggerEventQueue(tmp_path / "runtime" / "direct-events.json")
    worker = build_teams_ingest_worker(
        config,
        project_dir=tmp_path,
        event_queue=queue,
        effect_wake_notifier=lambda: True,
        trigger_wake_notifier=lambda: True,
    )
    ensure_directory(worker, "root/team-a")
    resolved_directories: list[str] = []

    def resolve_for_directory(directory_path: str) -> GatewayConfig:
        resolved_directories.append(directory_path)
        return team_config if directory_path == "root/team-a" else config

    monkeypatch.setattr(
        worker.runtime.provider_config,
        "resolve_for_directory",
        resolve_for_directory,
    )

    result = worker.run_once()

    assert resolved_directories == ["root/team-a"]
    assert requests[0].headers["Authorization"] == "Bearer team-graph-token"
    assert result.directories_polled == 1
    assert result.new_messages_discovered == 1
    assert result.events_created == 1
    assert [message.message_id for message in worker.store.list_all_messages()] == ["msg-1"]


def test_teams_ingest_worker_falls_back_when_direct_queue_setup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    write_trigger_config(
        tmp_path,
        [
            {
                "id": "teams-new",
                "display_name": "Teams message",
                "type": "trigger:teams:new-message",
                "targets": [{"target": "team-a/channel-1"}],
            }
        ],
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "msg-1",
                        "createdDateTime": "2026-08-14T10:00:00Z",
                        "body": {"content": "hello", "contentType": "text"},
                        "from": {"user": {"id": "user-a", "displayName": "Ada"}},
                    }
                ]
            },
            request=request,
        )

    def fail_queue_setup(_trigger_config) -> None:
        raise PermissionError("simulated orchestration store failure")

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
        }
    )
    trigger_wakes: list[str] = []
    worker = build_teams_ingest_worker(
        config,
        project_dir=tmp_path,
        trigger_wake_notifier=lambda: trigger_wakes.append("wake") or True,
    )
    monkeypatch.setattr(worker, "_event_queue_for", fail_queue_setup)

    with caplog.at_level(logging.WARNING):
        result = worker.run_once()

    assert len(requests) == 1
    assert result.new_messages_discovered == 1
    assert result.events_created == 0
    assert result.trigger_wake_notified is True
    assert trigger_wakes == ["wake"]
    assert [message.message_id for message in worker.store.list_all_messages()] == ["msg-1"]
    assert "continuing with inbox persistence and trigger wake recovery" in caplog.text


def test_teams_ingest_worker_wakes_effect_after_all_cycle_publications(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_trigger_config(
        tmp_path,
        [
            {
                "id": "teams-new",
                "display_name": "Teams message",
                "type": "trigger:teams:new-message",
                "targets": [
                    {"target": "team-a/channel-1"},
                    {"target": "team-a/channel-2"},
                ],
            }
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        channel_id = request.url.path.split("/")[-2]
        message_number = channel_id.rsplit("-", 1)[-1]
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": f"msg-{message_number}",
                        "createdDateTime": f"2026-08-14T10:0{message_number}:00Z",
                        "body": {"content": "hello", "contentType": "text"},
                        "from": {"user": {"id": "user-a", "displayName": "Ada"}},
                    }
                ]
            },
            request=request,
        )

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
        }
    )
    queue = SqliteTriggerEventQueue(tmp_path / "runtime" / "direct-events.json")
    pending_counts_at_wake: list[int] = []
    worker = build_teams_ingest_worker(
        config,
        project_dir=tmp_path,
        event_queue=queue,
        effect_wake_notifier=lambda: (
            pending_counts_at_wake.append(len(queue.list_pending())) or True
        ),
        trigger_wake_notifier=lambda: True,
    )

    result = worker.run_once()

    assert result.events_created == 2
    assert result.effect_wake_notified is True
    assert pending_counts_at_wake == [2]
    assert [record.event_id for record in queue.list_pending()] == [
        "teams-new:team-a/channel-1/msg-1",
        "teams-new:team-a/channel-2/msg-2",
    ]


def test_teams_ingest_worker_direct_publication_can_be_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_trigger_config(
        tmp_path,
        [
            {
                "id": "teams-new",
                "display_name": "Teams message",
                "type": "trigger:teams:new-message",
                "targets": [{"target": "team-a/channel-1"}],
            }
        ],
    )
    teams_config_path = tmp_path / "config" / "teams-ingest" / "config.json"
    teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    teams_config_path.write_text(
        json.dumps({"publish_trigger_events_directly": False}),
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "msg-1",
                        "createdDateTime": "2026-08-14T10:00:00Z",
                        "body": {"content": "hello", "contentType": "text"},
                        "from": {"user": {"id": "user-a", "displayName": "Ada"}},
                    }
                ]
            },
            request=request,
        )

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
        }
    )
    queue = SqliteTriggerEventQueue(tmp_path / "runtime" / "direct-events.json")
    effect_wakes: list[str] = []
    trigger_wakes: list[str] = []
    worker = build_teams_ingest_worker(
        config,
        project_dir=tmp_path,
        event_queue=queue,
        effect_wake_notifier=lambda: effect_wakes.append("wake") or True,
        trigger_wake_notifier=lambda: trigger_wakes.append("wake") or True,
    )
    ensure_directory(worker, "root/team-a")
    worker.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            directory_path="root/team-a",
            allowed_tools=[ToolName.TEAMS_GET_MESSAGES],
            teams_channel_allowlist=["team-a/channel-1"],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )

    result = worker.run_once()

    assert result.new_messages_discovered == 1
    assert result.messages_matched == 0
    assert result.events_created == 0
    assert result.effect_wake_notified is False
    assert result.trigger_wake_notified is True
    assert queue.list_pending() == []
    assert effect_wakes == []
    assert trigger_wakes == ["wake"]


def test_teams_ingest_worker_keeps_message_durable_when_direct_queue_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_trigger_config(
        tmp_path,
        [
            {
                "id": "teams-new",
                "display_name": "Teams message",
                "type": "trigger:teams:new-message",
                "targets": [{"target": "team-a/channel-1"}],
            }
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "msg-1",
                        "createdDateTime": "2026-08-14T10:00:00Z",
                        "body": {"content": "hello", "contentType": "text"},
                        "from": {"user": {"id": "user-a", "displayName": "Ada"}},
                    }
                ]
            },
            request=request,
        )

    class FailingQueue:
        def publish_result(self, _event, **_kwargs):
            raise RuntimeError("simulated queue failure")

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
        }
    )
    trigger_wakes: list[str] = []
    worker = build_teams_ingest_worker(
        config,
        project_dir=tmp_path,
        event_queue=FailingQueue(),
        trigger_wake_notifier=lambda: trigger_wakes.append("wake") or True,
    )

    result = worker.run_once()

    assert result.new_messages_discovered == 1
    assert result.messages_matched == 1
    assert result.events_created == 0
    assert result.publication_failures == 1
    assert result.effect_wake_notified is False
    assert result.trigger_wake_notified is True
    assert trigger_wakes == ["wake"]
    stored = worker.store.list_all_messages(channel_targets=["team-a/channel-1"])
    assert [message.message_id for message in stored] == ["msg-1"]
    assert not (tmp_path / "runtime" / "trigger-cursors-teams.json").exists()

    provider = TeamsNewMessageTriggerProvider()
    provider.initialize(
        TeamsNewMessageTriggerDefinition.model_validate(
            {
                "id": "teams-new",
                "display_name": "Teams message",
                "type": "trigger:teams:new-message",
                "targets": [{"target": "team-a/channel-1"}],
            }
        ),
        TriggerRuntimeContext(resolved_config=worker.config, env={}),
    )
    assert [event.event_id for event in provider.poll()] == ["teams-new:team-a/channel-1/msg-1"]


def test_teams_ingest_worker_direct_publication_honors_saved_trigger_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_trigger_config(
        tmp_path,
        [
            {
                "id": "teams-new",
                "display_name": "Teams message",
                "type": "trigger:teams:new-message",
                "targets": [{"target": "team-a/channel-1"}],
            }
        ],
    )
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True)
    cursor_path = runtime_dir / "trigger-cursors-teams.json"
    cursor_payload = {
        "teams-new": {
            "last_created_at": "2026-08-14T11:00:00Z",
            "recent_cursors": [],
        }
    }
    cursor_path.write_text(json.dumps(cursor_payload), encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "msg-before-baseline",
                        "createdDateTime": "2026-08-14T10:00:00Z",
                        "body": {"content": "hello", "contentType": "text"},
                        "from": {"user": {"id": "user-a", "displayName": "Ada"}},
                    }
                ]
            },
            request=request,
        )

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {"runtime_dir": str(runtime_dir), "teams_backend": "graph_api"}
    )
    queue = SqliteTriggerEventQueue(runtime_dir / "direct-events.json")
    worker = build_teams_ingest_worker(
        config,
        project_dir=tmp_path,
        event_queue=queue,
        trigger_wake_notifier=lambda: True,
    )

    result = worker.run_once()

    assert result.new_messages_discovered == 1
    assert result.messages_matched == 0
    assert result.events_created == 0
    assert queue.list_pending() == []
    assert json.loads(cursor_path.read_text(encoding="utf-8")) == cursor_payload


def test_teams_ingest_worker_applies_trigger_config_change_on_next_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_trigger = {
        "id": "teams-old",
        "display_name": "Old Teams message",
        "type": "trigger:teams:new-message",
        "targets": [{"target": "team-a/channel-1"}],
        "user_ids": ["user-old"],
    }
    new_trigger = {
        "id": "teams-new",
        "display_name": "New Teams message",
        "type": "trigger:teams:new-message",
        "targets": [{"target": "team-a/channel-1"}],
        "user_ids": ["user-new"],
    }
    write_trigger_config(tmp_path, [old_trigger])
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            write_trigger_config(tmp_path, [new_trigger])
            message_id = "msg-old"
            author_id = "user-old"
            created_at = "2026-08-14T10:00:00Z"
        else:
            message_id = "msg-new"
            author_id = "user-new"
            created_at = "2026-08-14T10:01:00Z"
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": message_id,
                        "createdDateTime": created_at,
                        "body": {"content": "hello", "contentType": "text"},
                        "from": {"user": {"id": author_id, "displayName": author_id}},
                    }
                ]
            },
            request=request,
        )

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
        }
    )
    queue = SqliteTriggerEventQueue(tmp_path / "runtime" / "direct-events.json")
    worker = build_teams_ingest_worker(
        config,
        project_dir=tmp_path,
        event_queue=queue,
        effect_wake_notifier=lambda: True,
        trigger_wake_notifier=lambda: True,
    )

    first = worker.run_once()
    second = worker.run_once()

    assert first.workflow_config_revision != second.workflow_config_revision
    assert first.events_created == 1
    assert second.events_created == 1
    assert [record.event_id for record in queue.list_pending()] == [
        "teams-old:team-a/channel-1/msg-old",
        "teams-new:team-a/channel-1/msg-new",
    ]


def test_teams_ingest_worker_recovers_from_materialization_and_trigger_wake_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trigger_payload = {
        "id": "teams-new",
        "display_name": "Teams message",
        "type": "trigger:teams:new-message",
        "targets": [{"target": "team-a/channel-1"}],
    }
    write_trigger_config(tmp_path, [trigger_payload])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "msg-1",
                        "createdDateTime": "2026-08-14T10:00:00Z",
                        "body": {"content": "hello", "contentType": "text"},
                        "from": {"user": {"id": "user-a", "displayName": "Ada"}},
                    }
                ]
            },
            request=request,
        )

    def fail_materialization(_matcher, _message):
        raise RuntimeError("simulated materialization failure")

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setattr(
        "evander.messaging.teams_ingest.TeamsNewMessageEventMatcher.event_for",
        fail_materialization,
    )
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
        }
    )
    queue = SqliteTriggerEventQueue(tmp_path / "runtime" / "direct-events.json")
    worker = build_teams_ingest_worker(
        config,
        project_dir=tmp_path,
        event_queue=queue,
        effect_wake_notifier=lambda: (_ for _ in ()).throw(RuntimeError("effect wake failed")),
        trigger_wake_notifier=lambda: (_ for _ in ()).throw(RuntimeError("trigger wake failed")),
    )

    result = worker.run_once()

    assert result.new_messages_discovered == 1
    assert result.messages_matched == 0
    assert result.publication_failures == 1
    assert result.effect_wake_notified is False
    assert result.trigger_wake_notified is False
    assert queue.list_pending() == []
    assert [message.message_id for message in worker.store.list_all_messages()] == ["msg-1"]
    assert not (tmp_path / "runtime" / "trigger-cursors-teams.json").exists()

    provider = TeamsNewMessageTriggerProvider()
    provider.initialize(
        TeamsNewMessageTriggerDefinition.model_validate(trigger_payload),
        TriggerRuntimeContext(resolved_config=worker.config, env={}),
    )
    monkeypatch.undo()
    assert [event.event_id for event in provider.poll()] == ["teams-new:team-a/channel-1/msg-1"]


def test_teams_ingest_worker_keeps_durable_event_when_wake_notifications_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trigger_payload = {
        "id": "teams-new",
        "display_name": "Teams message",
        "type": "trigger:teams:new-message",
        "targets": [{"target": "team-a/channel-1"}],
    }
    write_trigger_config(tmp_path, [trigger_payload])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "msg-1",
                        "createdDateTime": "2026-08-14T10:00:00Z",
                        "body": {"content": "hello", "contentType": "text"},
                        "from": {"user": {"id": "user-a", "displayName": "Ada"}},
                    }
                ]
            },
            request=request,
        )

    def fail_wake() -> bool:
        raise RuntimeError("simulated wake failure")

    patch_worker_httpx_client(monkeypatch, httpx.MockTransport(handler))
    monkeypatch.setenv("GRAPH_TEAMS_ACCESS_TOKEN", "graph-token")
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": str(tmp_path / "runtime"),
            "teams_backend": "graph_api",
        }
    )
    queue = SqliteTriggerEventQueue(tmp_path / "runtime" / "direct-events.json")
    worker = build_teams_ingest_worker(
        config,
        project_dir=tmp_path,
        event_queue=queue,
        effect_wake_notifier=fail_wake,
        trigger_wake_notifier=fail_wake,
    )

    result = worker.run_once()

    assert result.events_created == 1
    assert result.publication_failures == 0
    assert result.effect_wake_notified is False
    assert result.trigger_wake_notified is False
    assert [record.event_id for record in queue.list_pending()] == [
        "teams-new:team-a/channel-1/msg-1"
    ]
    assert not (tmp_path / "runtime" / "trigger-cursors-teams.json").exists()
