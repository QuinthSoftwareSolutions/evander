from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evander.config import GatewayConfig, MailboxConfig
from evander.email.providers import EmailMessageSummary
from evander.events.provider_models import CommunicationNewMessageTriggerDefinition
from evander.messaging.discord_inbox_store import DiscordInboxStore
from evander.messaging.teams_inbox_store import TeamsInboxMessage, TeamsInboxStore
from evander.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from evander.models import DiscordMessageEnvelope, EmailMessage
from evander.provider_config import ProviderConfigResolver
from evander.provider_runtime.adapters import trigger_provider_config_blob
from evander.triggers.providers.communication_new_message import (
    CommunicationNewMessageTriggerProvider,
)
from evander.triggers.providers.base import TriggerRuntimeContext


def _runtime_context(tmp_path: Path) -> TriggerRuntimeContext:
    runtime_dir = tmp_path / "runtime"
    return TriggerRuntimeContext(
        resolved_config=GatewayConfig(
            runtime_dir=runtime_dir,
            directories_store_path=runtime_dir / "directories.json",
        ),
        env={},
    )


def _write_targets(runtime_dir: Path, *, include_discord: bool = True) -> None:
    targets = [
        {
            "id": "teams-primary",
            "provider": "teams",
            "kind": "teams",
            "address": "team-a/channel-1",
            "policy": {"capabilities": ["read"]},
        }
    ]
    if include_discord:
        targets.append(
            {
                "id": "discord-ops",
                "provider": "discord",
                "kind": "discord",
                "address": "123456789012345678/234567890123456789",
                "policy": {"capabilities": ["read"]},
            }
        )
    repository = SqliteCommunicationTargetsRepository(communication_targets_db_path(runtime_dir))
    repository.write_overlay("root", {"targets": targets})


def _trigger_definition(
    *, target_ids: list[str], **extra: object
) -> CommunicationNewMessageTriggerDefinition:
    return CommunicationNewMessageTriggerDefinition.model_validate(
        {
            "id": "comm-trigger",
            "display_name": "Communication message",
            "type": "trigger:communication:new-message",
            "communication_target_ids": target_ids,
            **extra,
        }
    )


def _read_cursor_state(runtime_dir: Path) -> dict[str, object]:
    cursor_path = runtime_dir / "trigger-cursors-communication.json"
    return json.loads(cursor_path.read_text(encoding="utf-8"))


def test_communication_trigger_polls_teams_and_discord_and_acknowledges_same_timestamp_messages(
    tmp_path: Path,
) -> None:
    context = _runtime_context(tmp_path)
    runtime_dir = context.resolved_config.runtime_dir
    _write_targets(runtime_dir)

    teams_store = TeamsInboxStore(runtime_dir / "teams-inbox-store.json")
    discord_store = DiscordInboxStore(runtime_dir / "discord-inbox-store.json")
    created_at = datetime(2026, 4, 9, 9, 30, tzinfo=UTC)
    teams_store.add_message_if_new(
        TeamsInboxMessage(
            cursor="teams-msg-1",
            message_id="teams-msg-1",
            author_id="user-a",
            author_name="User A",
            team_id="team-a",
            channel_id="channel-1",
            thread_id="thread-1",
            content="hello from teams",
            created_at=created_at,
            metadata={"source": "teams_graph_api", "target": "team-a/channel-1"},
        )
    )
    discord_store.add_message_if_new(
        DiscordMessageEnvelope(
            message_id="discord-msg-1",
            channel_id="234567890123456789",
            author_id="user-a",
            author_name="User A",
            content="HELLO from discord",
            created_at=created_at,
            raw={
                "source": "discord_bot_api",
                "message": {"id": "discord-msg-1", "channel_id": "234567890123456789"},
            },
        )
    )

    provider = CommunicationNewMessageTriggerProvider()
    provider.initialize(
        _trigger_definition(
            target_ids=["teams-primary", "discord-ops"],
            author_ids=["USER-A"],
            content_contains="hello",
        ),
        context,
    )

    first = provider.poll()
    assert [event.event_id for event in first] == [
        "comm-trigger:discord:discord-ops:discord-msg-1:2026-04-09T09:30:00Z",
        "comm-trigger:teams:teams-primary:teams-msg-1:2026-04-09T09:30:00Z",
    ]
    assert _read_cursor_state(runtime_dir) == {}

    discord_event, teams_event = first
    assert discord_event.payload["provider"] == "discord"
    assert discord_event.payload["communication_target_id"] == "discord-ops"
    assert discord_event.payload["message_id"] == "discord-msg-1"
    assert discord_event.payload["thread_id"] is None
    assert discord_event.payload["content"] == "HELLO from discord"
    assert discord_event.payload["author_id"] == "user-a"
    assert discord_event.payload["author_name"] == "User A"
    assert discord_event.payload["created_at"] == "2026-04-09T09:30:00Z"
    assert discord_event.payload["received_at"] == "2026-04-09T09:30:00Z"
    assert discord_event.payload["target"] == "123456789012345678/234567890123456789"
    assert discord_event.payload["message"]["metadata"]["source"] == "discord_bot_api"
    assert "auth_profile_ref" not in discord_event.payload

    assert teams_event.payload["provider"] == "teams"
    assert teams_event.payload["communication_target_id"] == "teams-primary"
    assert teams_event.payload["message_id"] == "teams-msg-1"
    assert teams_event.payload["thread_id"] == "thread-1"
    assert teams_event.payload["content"] == "hello from teams"
    assert teams_event.payload["author_id"] == "user-a"
    assert teams_event.payload["author_name"] == "User A"
    assert teams_event.payload["created_at"] == "2026-04-09T09:30:00Z"
    assert teams_event.payload["received_at"] == "2026-04-09T09:30:00Z"
    assert teams_event.payload["target"] == "team-a/channel-1"
    assert teams_event.payload["message"]["metadata"]["source"] == "teams_graph_api"
    assert "auth_profile_ref" not in teams_event.payload

    provider.acknowledge_published_event(discord_event)
    provider.acknowledge_published_event(teams_event)

    cursor_state = _read_cursor_state(runtime_dir)
    assert cursor_state["comm-trigger"]["last_created_at"] == "2026-04-09T09:30:00Z"
    assert cursor_state["comm-trigger"]["recent_message_ids"] == [
        "discord:discord-ops:discord-msg-1",
        "teams:teams-primary:teams-msg-1",
    ]

    teams_store.add_message_if_new(
        TeamsInboxMessage(
            cursor="teams-msg-2",
            message_id="teams-msg-2",
            author_id="user-a",
            author_name="User A",
            team_id="team-a",
            channel_id="channel-1",
            thread_id="thread-1",
            content="late hello arrival with the same timestamp",
            created_at=created_at,
            metadata={"source": "teams_graph_api", "target": "team-a/channel-1"},
        )
    )

    second = provider.poll()
    assert [event.event_id for event in second] == [
        "comm-trigger:teams:teams-primary:teams-msg-2:2026-04-09T09:30:00Z",
    ]

    provider.acknowledge_published_event(second[0])
    assert provider.poll() == []


def test_communication_trigger_requires_message_prefix_at_the_start(tmp_path: Path) -> None:
    context = _runtime_context(tmp_path)
    runtime_dir = context.resolved_config.runtime_dir
    _write_targets(runtime_dir)
    store = DiscordInboxStore(runtime_dir / "discord-inbox-store.json")
    created_at = datetime(2026, 8, 10, 9, 30, tzinfo=UTC)
    for message_id, content in (
        ("discord-msg-1", "@Trudy: review the retry behavior"),
        ("discord-msg-2", "Could @Trudy: review the retry behavior?"),
        ("discord-msg-3", " @Trudy: review the retry behavior"),
        ("discord-msg-4", "@Trudell: review the retry behavior"),
    ):
        store.add_message_if_new(
            DiscordMessageEnvelope(
                message_id=message_id,
                channel_id="234567890123456789",
                author_id="user-a",
                author_name="User A",
                content=content,
                created_at=created_at,
                raw={"source": "discord_bot_api"},
            )
        )

    provider = CommunicationNewMessageTriggerProvider()
    provider.initialize(
        _trigger_definition(
            target_ids=["discord-ops"],
            content_starts_with="@Trudy:",
        ),
        context,
    )

    assert [event.payload["message_id"] for event in provider.poll()] == ["discord-msg-1"]


def test_communication_trigger_polls_email_target(tmp_path: Path) -> None:
    context = _runtime_context(tmp_path)
    runtime_dir = context.resolved_config.runtime_dir
    runtime_dir.mkdir(parents=True, exist_ok=True)
    repository = SqliteCommunicationTargetsRepository(communication_targets_db_path(runtime_dir))
    repository.write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "email-ops",
                    "provider": "email",
                    "kind": "email",
                    "address": "operator@example.com",
                    "policy": {
                        "capabilities": ["read"],
                        "auth_profile_ref": "email.default",
                    },
                }
            ]
        },
    )
    received_at = datetime(2026, 4, 9, 10, 0, tzinfo=UTC)
    context.resolved_config.email_store_path.write_text(
        json.dumps(
            {
                "email-msg-1": EmailMessage(
                    message_id="email-msg-1",
                    mailbox_id="default",
                    provider="json_store",
                    folder="inbox",
                    sender="sender@example.com",
                    recipients=["operator@example.com"],
                    subject="Status",
                    body_text="hello from email",
                    unread=True,
                    received_at=received_at,
                ).model_dump(mode="json")
            }
        ),
        encoding="utf-8",
    )

    provider = CommunicationNewMessageTriggerProvider()
    provider.initialize(_trigger_definition(target_ids=["email-ops"]), context)

    events = provider.poll()

    assert [event.event_id for event in events] == [
        "comm-trigger:email:email-ops:email-msg-1:2026-04-09T10:00:00Z"
    ]
    event = events[0]
    assert event.payload["provider"] == "email"
    assert event.payload["communication_target_id"] == "email-ops"
    assert event.payload["message_id"] == "email-msg-1"
    assert event.payload["content"] == "hello from email"
    assert event.payload["author_id"] == "sender@example.com"
    assert event.payload["target"] == "operator@example.com"

    provider.acknowledge_published_event(event)
    cursor_state = _read_cursor_state(runtime_dir)
    assert cursor_state["comm-trigger"]["recent_message_ids"] == ["email:email-ops:email-msg-1"]


def test_communication_trigger_resolves_full_email_config_without_provider_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(
        runtime_dir=runtime_dir,
        email_mailboxes=[
            MailboxConfig(
                mailbox_id="mailbox-1",
                provider="gmail",
                account_identifier="me",
                auth_secret_refs={"access_token": "GMAIL_ACCESS_TOKEN"},
            )
        ],
    )
    resolver = ProviderConfigResolver(config)
    SqliteCommunicationTargetsRepository(communication_targets_db_path(runtime_dir)).write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "email-ops",
                    "provider": "email",
                    "kind": "email",
                    "address": "operator@example.com",
                    "policy": {
                        "capabilities": ["read"],
                        "auth_profile_ref": "email.mailbox-1",
                    },
                }
            ]
        },
    )
    captured: dict[str, object] = {}

    class FakeEmailProvider:
        def list_unread(
            self, mailbox_id: str, folder: str, limit: int
        ) -> list[EmailMessageSummary]:
            captured["list_unread"] = {
                "mailbox_id": mailbox_id,
                "folder": folder,
                "limit": limit,
            }
            return [
                EmailMessageSummary(
                    mailbox_id=mailbox_id,
                    provider="gmail",
                    folder=folder,
                    message_id="gmail-msg-1",
                    thread_id="thread-1",
                    sender="sender@example.com",
                    recipients=["operator@example.com"],
                    received_at=datetime(2026, 4, 9, 10, 0, tzinfo=UTC),
                    unread=True,
                    body_snippet="hello from gmail",
                )
            ]

    def fake_build_email_provider(config_arg, emails, drafts, email_cursors):
        captured["mailbox_ids"] = [mailbox.mailbox_id for mailbox in config_arg.email_mailboxes]
        captured["mailbox_providers"] = [mailbox.provider for mailbox in config_arg.email_mailboxes]
        captured["email_cursors"] = email_cursors
        return FakeEmailProvider()

    monkeypatch.setattr(
        "evander.triggers.providers.communication_new_message._build_email_provider",
        fake_build_email_provider,
    )
    provider = CommunicationNewMessageTriggerProvider()
    provider.initialize(
        _trigger_definition(target_ids=["email-ops"]),
        TriggerRuntimeContext(
            provider_config=trigger_provider_config_blob(
                resolver.resolve_trigger_provider_config("root")
            ),
            provider_config_resolver=resolver,
            env={"GMAIL_ACCESS_TOKEN": "secret"},
            metadata={"directory_path": "root"},
        ),
    )

    events = provider.poll()

    assert captured["mailbox_ids"] == ["mailbox-1"]
    assert captured["mailbox_providers"] == ["gmail"]
    assert captured["email_cursors"] is None
    assert captured["list_unread"] == {
        "mailbox_id": "mailbox-1",
        "folder": "inbox",
        "limit": 200,
    }
    assert events[0].payload["communication_target_id"] == "email-ops"
    assert events[0].payload["message_id"] == "gmail-msg-1"


def test_communication_trigger_matches_teams_chat_and_bare_discord_channel_targets(
    tmp_path: Path,
) -> None:
    context = _runtime_context(tmp_path)
    runtime_dir = context.resolved_config.runtime_dir

    repository = SqliteCommunicationTargetsRepository(communication_targets_db_path(runtime_dir))
    repository.write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "teams-chat",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "chat:19:abc@thread.v2",
                    "policy": {
                        "capabilities": ["read"],
                        "auth_profile_ref": "teams.secret",
                    },
                },
                {
                    "id": "discord-bare",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "345678901234567890",
                    "policy": {
                        "capabilities": ["read"],
                        "auth_profile_ref": "discord.secret",
                    },
                },
            ]
        },
    )

    teams_store = TeamsInboxStore(runtime_dir / "teams-inbox-store.json")
    discord_store = DiscordInboxStore(runtime_dir / "discord-inbox-store.json")
    created_at = datetime(2026, 4, 9, 11, 0, tzinfo=UTC)
    teams_store.add_message_if_new(
        TeamsInboxMessage(
            cursor="teams-chat-msg-1",
            message_id="teams-chat-msg-1",
            author_id="user-c",
            author_name="User C",
            chat_id="19:abc@thread.v2",
            content="hello from chat",
            created_at=created_at,
            metadata={"source": "teams_graph_api", "target": "chat:19:abc@thread.v2"},
        )
    )
    discord_store.add_message_if_new(
        DiscordMessageEnvelope(
            message_id="discord-bare-msg-1",
            channel_id="345678901234567890",
            author_id="user-c",
            author_name="User C",
            content="hello from discord bare channel",
            created_at=created_at,
            raw={"source": "discord_bot_api"},
        )
    )

    provider = CommunicationNewMessageTriggerProvider()
    provider.initialize(
        _trigger_definition(
            target_ids=["teams-chat", "discord-bare"],
            author_ids=["user-c"],
            content_contains="hello",
        ),
        context,
    )

    events = provider.poll()
    assert [event.event_id for event in events] == [
        "comm-trigger:discord:discord-bare:discord-bare-msg-1:2026-04-09T11:00:00Z",
        "comm-trigger:teams:teams-chat:teams-chat-msg-1:2026-04-09T11:00:00Z",
    ]

    discord_event = next(event for event in events if event.payload["provider"] == "discord")
    teams_event = next(event for event in events if event.payload["provider"] == "teams")

    assert discord_event.payload["target"] == "345678901234567890"
    assert discord_event.payload["message"]["metadata"]["source"] == "discord_bot_api"
    assert "auth_profile_ref" not in discord_event.payload

    assert teams_event.payload["target"] == "chat:19:abc@thread.v2"
    assert teams_event.payload["message"]["metadata"]["source"] == "teams_graph_api"
    assert "auth_profile_ref" not in teams_event.payload


def test_communication_trigger_provider_rejects_teams_user_targets(
    tmp_path: Path,
) -> None:
    context = _runtime_context(tmp_path)
    runtime_dir = context.resolved_config.runtime_dir
    repository = SqliteCommunicationTargetsRepository(communication_targets_db_path(runtime_dir))
    repository.write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "teams-user",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "user:operator@example.com",
                    "policy": {"capabilities": ["read"]},
                }
            ]
        },
    )

    provider = CommunicationNewMessageTriggerProvider()

    with pytest.raises(ValueError, match="Teams user target"):
        provider.initialize(_trigger_definition(target_ids=["teams-user"]), context)


def test_communication_trigger_maps_public_discord_child_to_parent_target(
    tmp_path: Path,
) -> None:
    context = _runtime_context(tmp_path)
    runtime_dir = context.resolved_config.runtime_dir
    repository = SqliteCommunicationTargetsRepository(communication_targets_db_path(runtime_dir))
    repository.write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "discord-forum",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345678/234567890123456789",
                    "policy": {
                        "capabilities": ["read", "reply"],
                        "discord_conversation_scope": "public_threads",
                    },
                }
            ]
        },
    )
    store = DiscordInboxStore(runtime_dir / "discord-inbox-store.json")
    created_at = datetime(2026, 8, 17, 9, 0, tzinfo=UTC)
    store.upsert_thread_binding(
        thread_id="345678901234567890",
        parent_channel_id="234567890123456789",
        guild_id="123456789012345678",
        thread_type=11,
        archived=False,
        locked=False,
        observed_at=created_at,
    )
    store.add_message_if_new(
        DiscordMessageEnvelope(
            message_id="456789012345678901",
            channel_id="345678901234567890",
            parent_channel_id="234567890123456789",
            thread_id="345678901234567890",
            in_reply_to="starter-message-id",
            author_id="worker-1",
            author_name="Worker One",
            content="The line is blocked",
            created_at=created_at,
            raw={
                "source": "discord_bot_api",
                "channel_id": "345678901234567890",
                "parent_channel_id": "234567890123456789",
                "thread_id": "345678901234567890",
                "message_reference_id": "starter-message-id",
            },
        )
    )
    store.add_message_if_new(
        DiscordMessageEnvelope(
            message_id="456789012345678902",
            channel_id="345678901234567890",
            parent_channel_id="234567890123456789",
            thread_id="345678901234567890",
            author_id="bot-1",
            author_name="Support Agent",
            content="I am looking into it.",
            created_at=created_at,
            raw={"source": "discord_webhook_status"},
        )
    )

    provider = CommunicationNewMessageTriggerProvider()
    provider.initialize(_trigger_definition(target_ids=["discord-forum"]), context)

    events = provider.poll()

    assert len(events) == 1
    assert events[0].payload["communication_target_id"] == "discord-forum"
    assert events[0].payload["thread_id"] == "345678901234567890"
    assert events[0].payload["message"]["metadata"]["message_reference_id"] == (
        "starter-message-id"
    )


def test_communication_trigger_applies_author_and_content_filters_case_insensitively_and_case_sensitively(
    tmp_path: Path,
) -> None:
    context = _runtime_context(tmp_path)
    runtime_dir = context.resolved_config.runtime_dir
    _write_targets(runtime_dir)

    discord_store = DiscordInboxStore(runtime_dir / "discord-inbox-store.json")
    created_at = datetime(2026, 4, 9, 10, 0, tzinfo=UTC)
    discord_store.add_message_if_new(
        DiscordMessageEnvelope(
            message_id="discord-msg-1",
            channel_id="234567890123456789",
            author_id="user-b",
            author_name="User B",
            content="hello world",
            created_at=created_at,
            raw={"source": "discord_bot_api"},
        )
    )
    discord_store.add_message_if_new(
        DiscordMessageEnvelope(
            message_id="discord-msg-2",
            channel_id="234567890123456789",
            author_id="user-a",
            author_name="User A",
            content="Hello world",
            created_at=created_at,
            raw={"source": "discord_bot_api"},
        )
    )

    provider = CommunicationNewMessageTriggerProvider()
    provider.initialize(
        _trigger_definition(
            target_ids=["discord-ops"],
            author_ids=["user-a"],
            content_contains="Hello",
            case_sensitive=True,
        ),
        context,
    )

    events = provider.poll()
    assert [event.event_id for event in events] == [
        "comm-trigger:discord:discord-ops:discord-msg-2:2026-04-09T10:00:00Z",
    ]
    message = events[0].payload["message"]
    assert isinstance(message, dict)
    assert message["metadata"]["source"] == "discord_bot_api"
