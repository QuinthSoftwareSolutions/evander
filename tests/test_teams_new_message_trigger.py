from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agent_sandbox.config import GatewayConfig
from agent_sandbox.events.provider_models import TeamsNewMessageTriggerDefinition
from agent_sandbox.messaging.teams_inbox_store import TeamsInboxMessage, TeamsInboxStore
from agent_sandbox.triggers.providers.base import TriggerRuntimeContext
from agent_sandbox.triggers.providers.teams_new_message import TeamsNewMessageTriggerProvider


def _runtime_context(tmp_path: Path) -> TriggerRuntimeContext:
    runtime_dir = tmp_path / "runtime"
    return TriggerRuntimeContext(
        resolved_config=GatewayConfig(
            runtime_dir=runtime_dir,
        ),
        env={},
    )


def _trigger_definition() -> TeamsNewMessageTriggerDefinition:
    return TeamsNewMessageTriggerDefinition.model_validate(
        {
            "id": "teams-trigger",
            "display_name": "Teams new message",
            "type": "trigger:teams:new-message",
            "targets": [{"target": "team-a/channel-1"}],
            "user_ids": ["user-a"],
        }
    )


def _assert_payload(
    payload: dict[str, object],
    *,
    channel_target: str,
    cursor: str,
    message_id: str,
    target_type: str,
) -> None:
    assert payload["channel_target"] == channel_target
    assert payload["target"] == channel_target
    assert payload["target_type"] == target_type
    assert payload["cursor"] == cursor
    assert payload["message_id"] == message_id
    assert isinstance(payload["created_at"], str)
    message = payload["message"]
    assert isinstance(message, dict)
    assert message["channel_target"] == channel_target
    assert message["cursor"] == cursor
    assert message["message_id"] == message_id
    assert message["target_type"] == target_type


def test_teams_trigger_emits_once_per_poll_when_any_matching_messages_arrive_and_persists_cursor(
    tmp_path: Path,
) -> None:
    context = _runtime_context(tmp_path)
    store = TeamsInboxStore(context.resolved_config.runtime_dir / "teams-inbox-store.json")
    created_at = datetime(2026, 4, 9, 9, 30, tzinfo=UTC)
    for message in (
        TeamsInboxMessage(
            cursor="team-a/channel-1/msg-1",
            message_id="msg-1",
            author_id="user-a",
            author_name="User A",
            team_id="team-a",
            channel_id="channel-1",
            thread_id="msg-1",
            content="hello",
            created_at=created_at,
            metadata={"source": "teams_graph_api"},
        ),
        TeamsInboxMessage(
            cursor="team-a/channel-1/msg-2",
            message_id="msg-2",
            author_id="user-a",
            author_name="User A",
            team_id="team-a",
            channel_id="channel-1",
            thread_id="msg-1",
            content="same timestamp reply",
            created_at=created_at,
            metadata={"source": "teams_graph_api"},
        ),
    ):
        store.add_message_if_new(message)

    store.set_cursor(
        "team-a/channel-1",
        "team-a/channel-1/msg-2",
        created_at=created_at,
    )

    provider = TeamsNewMessageTriggerProvider()
    provider.initialize(_trigger_definition(), context)

    first = provider.poll()
    second = provider.poll()

    store.add_message_if_new(
        TeamsInboxMessage(
            cursor="team-a/channel-1/msg-3",
            message_id="msg-3",
            author_id="user-a",
            author_name="User A",
            team_id="team-a",
            channel_id="channel-1",
            thread_id="msg-1",
            content="late arrival with same timestamp",
            created_at=created_at,
            metadata={"source": "teams_graph_api"},
        )
    )

    third = provider.poll()
    fourth = provider.poll()

    assert [event.event_id for event in first] == ["teams-trigger:team-a/channel-1/msg-2"]
    assert second == []
    assert [event.event_id for event in third] == ["teams-trigger:team-a/channel-1/msg-3"]
    assert fourth == []
    _assert_payload(
        first[0].payload,
        channel_target="team-a/channel-1",
        cursor="team-a/channel-1/msg-2",
        message_id="msg-2",
        target_type="channel",
    )
    _assert_payload(
        third[0].payload,
        channel_target="team-a/channel-1",
        cursor="team-a/channel-1/msg-3",
        message_id="msg-3",
        target_type="channel",
    )

    cursor_payload = json.loads(
        (context.resolved_config.runtime_dir / "trigger-cursors-teams.json").read_text(
            encoding="utf-8"
        )
    )
    assert cursor_payload["teams-trigger"]["last_created_at"] == "2026-04-09T09:30:00Z"
    assert cursor_payload["teams-trigger"]["recent_cursors"] == [
        "team-a/channel-1/msg-1",
        "team-a/channel-1/msg-2",
        "team-a/channel-1/msg-3",
    ]


def test_teams_trigger_filters_by_channel_target_and_user(tmp_path: Path) -> None:
    context = _runtime_context(tmp_path)
    store = TeamsInboxStore(context.resolved_config.runtime_dir / "teams-inbox-store.json")
    for message in (
        TeamsInboxMessage(
            cursor="team-a/channel-1/msg-1",
            message_id="msg-1",
            author_id="user-a",
            author_name="User A",
            team_id="team-a",
            channel_id="channel-1",
            thread_id="msg-1",
            content="allowed",
            created_at=datetime(2026, 4, 9, 10, 0, tzinfo=UTC),
            metadata={"source": "teams_graph_api", "team_id": "team-a", "channel_id": "channel-1"},
        ),
        TeamsInboxMessage(
            cursor="team-a/channel-1/msg-2",
            message_id="msg-2",
            author_id="user-b",
            author_name="User B",
            team_id="team-a",
            channel_id="channel-1",
            thread_id="msg-1",
            content="wrong author",
            created_at=datetime(2026, 4, 9, 10, 1, tzinfo=UTC),
            metadata={"source": "teams_graph_api", "team_id": "team-a", "channel_id": "channel-1"},
        ),
        TeamsInboxMessage(
            cursor="team-b/channel-9/msg-9",
            message_id="msg-9",
            author_id="user-a",
            author_name="User A",
            team_id="team-b",
            channel_id="channel-9",
            thread_id="msg-9",
            content="wrong channel",
            created_at=datetime(2026, 4, 9, 10, 2, tzinfo=UTC),
            metadata={"source": "teams_graph_api", "team_id": "team-b", "channel_id": "channel-9"},
        ),
    ):
        store.add_message_if_new(message)

    provider = TeamsNewMessageTriggerProvider()
    provider.initialize(_trigger_definition(), context)

    events = provider.poll()

    assert len(events) == 1
    assert events[0].event_id == "teams-trigger:team-a/channel-1/msg-1"
    _assert_payload(
        events[0].payload,
        channel_target="team-a/channel-1",
        cursor="team-a/channel-1/msg-1",
        message_id="msg-1",
        target_type="channel",
    )


def test_teams_trigger_message_filter_matches_content_case_insensitively(tmp_path: Path) -> None:
    context = _runtime_context(tmp_path)
    store = TeamsInboxStore(context.resolved_config.runtime_dir / "teams-inbox-store.json")
    for message in (
        TeamsInboxMessage(
            cursor="chat:19:abc@thread.v2/msg-1",
            message_id="msg-1",
            author_id="user-a",
            author_name="User A",
            chat_id="19:abc@thread.v2",
            content="hello @Truman Bot",
            created_at=datetime(2026, 4, 9, 10, 0, tzinfo=UTC),
            metadata={"source": "teams_graph_api", "chat_id": "19:abc@thread.v2"},
        ),
        TeamsInboxMessage(
            cursor="chat:19:abc@thread.v2/msg-2",
            message_id="msg-2",
            author_id="user-a",
            author_name="User A",
            chat_id="19:abc@thread.v2",
            content="hello someone else",
            created_at=datetime(2026, 4, 9, 10, 1, tzinfo=UTC),
            metadata={"source": "teams_graph_api", "chat_id": "19:abc@thread.v2"},
        ),
    ):
        store.add_message_if_new(message)

    provider = TeamsNewMessageTriggerProvider()
    provider.initialize(
        TeamsNewMessageTriggerDefinition.model_validate(
            {
                "id": "teams-trigger",
                "display_name": "Teams new message",
                "type": "trigger:teams:new-message",
                "targets": [
                    {
                        "target": "chat:19:abc@thread.v2",
                        "message_filter": {
                            "content": {
                                "contains": "@truman bot",
                                "case_sensitive": False,
                            }
                        },
                    }
                ],
            }
        ),
        context,
    )

    events = provider.poll()

    assert [event.event_id for event in events] == ["teams-trigger:chat:19:abc@thread.v2/msg-1"]
    _assert_payload(
        events[0].payload,
        channel_target="chat:19:abc@thread.v2",
        cursor="chat:19:abc@thread.v2/msg-1",
        message_id="msg-1",
        target_type="chat",
    )


def test_teams_trigger_message_filter_matches_mentions_by_user_id(tmp_path: Path) -> None:
    context = _runtime_context(tmp_path)
    store = TeamsInboxStore(context.resolved_config.runtime_dir / "teams-inbox-store.json")
    for message in (
        TeamsInboxMessage(
            cursor="chat:19:abc@thread.v2/msg-1",
            message_id="msg-1",
            author_id="user-a",
            author_name="User A",
            chat_id="19:abc@thread.v2",
            content='<p><at id="0">Truman</at>&nbsp;<at id="1">Bot</at>&nbsp;Welcome</p>',
            created_at=datetime(2026, 4, 9, 10, 0, tzinfo=UTC),
            metadata={
                "source": "teams_graph_api",
                "chat_id": "19:abc@thread.v2",
                "message": {
                    "mentions": [
                        {
                            "id": 0,
                            "mentionText": "Truman",
                            "mentioned": {
                                "user": {
                                    "id": "143e5b02-868f-4f0c-a262-cf509b6c09e5",
                                }
                            },
                        },
                        {
                            "id": 1,
                            "mentionText": "Bot",
                            "mentioned": {
                                "user": {
                                    "id": "143e5b02-868f-4f0c-a262-cf509b6c09e5",
                                }
                            },
                        },
                    ]
                },
            },
        ),
        TeamsInboxMessage(
            cursor="chat:19:abc@thread.v2/msg-2",
            message_id="msg-2",
            author_id="user-a",
            author_name="User A",
            chat_id="19:abc@thread.v2",
            content="hello someone else",
            created_at=datetime(2026, 4, 9, 10, 1, tzinfo=UTC),
            metadata={
                "source": "teams_graph_api",
                "chat_id": "19:abc@thread.v2",
                "message": {"mentions": []},
            },
        ),
    ):
        store.add_message_if_new(message)

    provider = TeamsNewMessageTriggerProvider()
    provider.initialize(
        TeamsNewMessageTriggerDefinition.model_validate(
            {
                "id": "teams-trigger",
                "display_name": "Teams new message",
                "type": "trigger:teams:new-message",
                "targets": [
                    {
                        "target": "chat:19:abc@thread.v2",
                        "message_filter": {
                            "mentions": {
                                "user_ids": ["143e5b02-868f-4f0c-a262-cf509b6c09e5"],
                            }
                        },
                    }
                ],
            }
        ),
        context,
    )

    events = provider.poll()

    assert [event.event_id for event in events] == ["teams-trigger:chat:19:abc@thread.v2/msg-1"]
    _assert_payload(
        events[0].payload,
        channel_target="chat:19:abc@thread.v2",
        cursor="chat:19:abc@thread.v2/msg-1",
        message_id="msg-1",
        target_type="chat",
    )


def test_teams_trigger_message_filter_can_exclude_all_messages(tmp_path: Path) -> None:
    context = _runtime_context(tmp_path)
    store = TeamsInboxStore(context.resolved_config.runtime_dir / "teams-inbox-store.json")
    store.add_message_if_new(
        TeamsInboxMessage(
            cursor="chat:19:abc@thread.v2/msg-1",
            message_id="msg-1",
            author_id="user-a",
            author_name="User A",
            chat_id="19:abc@thread.v2",
            content="hello someone else",
            created_at=datetime(2026, 4, 9, 10, 0, tzinfo=UTC),
            metadata={"source": "teams_graph_api", "chat_id": "19:abc@thread.v2"},
        )
    )

    provider = TeamsNewMessageTriggerProvider()
    provider.initialize(
        TeamsNewMessageTriggerDefinition.model_validate(
            {
                "id": "teams-trigger",
                "display_name": "Teams new message",
                "type": "trigger:teams:new-message",
                "targets": [
                    {
                        "target": "chat:19:abc@thread.v2",
                        "message_filter": {
                            "mentions": {
                                "user_ids": ["143e5b02-868f-4f0c-a262-cf509b6c09e5"],
                            }
                        },
                    }
                ],
            }
        ),
        context,
    )

    assert provider.poll() == []


def test_teams_trigger_targets_can_apply_different_filters_per_target(tmp_path: Path) -> None:
    context = _runtime_context(tmp_path)
    store = TeamsInboxStore(context.resolved_config.runtime_dir / "teams-inbox-store.json")
    for message in (
        TeamsInboxMessage(
            cursor="team-a/channel-1/msg-1",
            message_id="msg-1",
            author_id="user-a",
            author_name="User A",
            team_id="team-a",
            channel_id="channel-1",
            thread_id="msg-1",
            content="plain channel message",
            created_at=datetime(2026, 4, 9, 10, 0, tzinfo=UTC),
            metadata={"source": "teams_graph_api", "team_id": "team-a", "channel_id": "channel-1"},
        ),
        TeamsInboxMessage(
            cursor="chat:19:abc@thread.v2/msg-2",
            message_id="msg-2",
            author_id="user-a",
            author_name="User A",
            chat_id="19:abc@thread.v2",
            content="hello someone else",
            created_at=datetime(2026, 4, 9, 10, 1, tzinfo=UTC),
            metadata={"source": "teams_graph_api", "chat_id": "19:abc@thread.v2"},
        ),
    ):
        store.add_message_if_new(message)

    provider = TeamsNewMessageTriggerProvider()
    provider.initialize(
        TeamsNewMessageTriggerDefinition.model_validate(
            {
                "id": "teams-trigger",
                "display_name": "Teams new message",
                "type": "trigger:teams:new-message",
                "targets": [
                    {"target": "team-a/channel-1"},
                    {
                        "target": "chat:19:abc@thread.v2",
                        "message_filter": {
                            "mentions": {"user_ids": ["143e5b02-868f-4f0c-a262-cf509b6c09e5"]}
                        },
                    },
                ],
            }
        ),
        context,
    )

    events = provider.poll()

    assert [event.event_id for event in events] == ["teams-trigger:team-a/channel-1/msg-1"]
