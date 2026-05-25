from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agent_sandbox.config import GatewayConfig
from agent_sandbox.events.provider_models import CommunicationNewMessageTriggerDefinition
from agent_sandbox.messaging.discord_inbox_store import DiscordInboxStore
from agent_sandbox.messaging.teams_inbox_store import TeamsInboxMessage, TeamsInboxStore
from agent_sandbox.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from agent_sandbox.models import DiscordMessageEnvelope
from agent_sandbox.triggers.providers.communication_new_message import (
    CommunicationNewMessageTriggerProvider,
)
from agent_sandbox.triggers.providers.base import TriggerRuntimeContext


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


def _trigger_definition(*, target_ids: list[str], **extra: object) -> CommunicationNewMessageTriggerDefinition:
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
