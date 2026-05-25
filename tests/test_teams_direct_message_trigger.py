from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from agent_sandbox.config import GatewayConfig
from agent_sandbox.events.provider_models import TeamsDirectMessageTriggerDefinition
from agent_sandbox.messaging.teams_inbox_store import TeamsInboxMessage, TeamsInboxStore
from agent_sandbox.triggers.providers.base import TriggerRuntimeContext
from agent_sandbox.triggers.providers.teams_direct_message import (
    TeamsDirectMessageTriggerProvider,
)


def _runtime_context(tmp_path: Path) -> TriggerRuntimeContext:
    runtime_dir = tmp_path / "runtime"
    return TriggerRuntimeContext(
        resolved_config=GatewayConfig(
            runtime_dir=runtime_dir,
        ),
        env={},
    )


def _trigger_definition() -> TeamsDirectMessageTriggerDefinition:
    return TeamsDirectMessageTriggerDefinition.model_validate(
        {
            "id": "teams-dm-trigger",
            "display_name": "Teams direct message",
            "type": "trigger:teams:direct-message",
            "sender_user_ids": ["user-a"],
        }
    )


def test_teams_direct_message_trigger_filters_to_allowlisted_direct_messages(
    tmp_path: Path,
) -> None:
    context = _runtime_context(tmp_path)
    store = TeamsInboxStore(context.resolved_config.runtime_dir / "teams-inbox-store.json")
    created_at = datetime(2026, 5, 25, 9, 30, tzinfo=UTC)
    for message in (
        TeamsInboxMessage(
            cursor="chat:dm-chat/msg-1",
            message_id="msg-1",
            author_id="user-a",
            author_name="User A",
            chat_id="dm-chat",
            content="allowed direct message",
            created_at=created_at,
            metadata={
                "source": "teams_graph_api",
                "target": "chat:dm-chat",
                "target_type": "direct_message",
                "chat_type": "oneOnOne",
            },
        ),
        TeamsInboxMessage(
            cursor="chat:dm-chat/msg-2",
            message_id="msg-2",
            author_id="user-b",
            author_name="User B",
            chat_id="dm-chat",
            content="wrong sender",
            created_at=created_at,
            metadata={
                "source": "teams_graph_api",
                "target": "chat:dm-chat",
                "target_type": "direct_message",
                "chat_type": "oneOnOne",
            },
        ),
        TeamsInboxMessage(
            cursor="chat:group-chat/msg-3",
            message_id="msg-3",
            author_id="user-a",
            author_name="User A",
            chat_id="group-chat",
            content="group chat message",
            created_at=created_at,
            metadata={
                "source": "teams_graph_api",
                "target": "chat:group-chat",
                "target_type": "chat",
            },
        ),
    ):
        store.add_message_if_new(message)

    provider = TeamsDirectMessageTriggerProvider()
    provider.initialize(_trigger_definition(), context)

    events = provider.poll()

    assert [event.event_id for event in events] == ["teams-dm-trigger:chat:dm-chat/msg-1"]
    payload = events[0].payload
    assert payload["channel_target"] == "chat:dm-chat"
    assert payload["target_type"] == "direct_message"
    assert payload["message_id"] == "msg-1"
    assert isinstance(payload["message"], dict)
    assert payload["message"]["target_type"] == "direct_message"


def test_teams_direct_message_trigger_applies_filter_and_acknowledges_cursor(
    tmp_path: Path,
) -> None:
    context = _runtime_context(tmp_path)
    store = TeamsInboxStore(context.resolved_config.runtime_dir / "teams-inbox-store.json")
    for index, content in enumerate(("ignore this", "please help"), start=1):
        store.add_message_if_new(
            TeamsInboxMessage(
                cursor=f"chat:dm-chat/msg-{index}",
                message_id=f"msg-{index}",
                author_id="user-a",
                author_name="User A",
                chat_id="dm-chat",
                content=content,
                created_at=datetime(2026, 5, 25, 9, 30 + index, tzinfo=UTC),
                metadata={
                    "source": "teams_graph_api",
                    "target": "chat:dm-chat",
                    "target_type": "direct_message",
                    "chat_type": "oneOnOne",
                },
            )
        )

    provider = TeamsDirectMessageTriggerProvider()
    provider.initialize(
        TeamsDirectMessageTriggerDefinition.model_validate(
            {
                "id": "teams-dm-trigger",
                "display_name": "Teams direct message",
                "type": "trigger:teams:direct-message",
                "sender_user_ids": ["user-a"],
                "message_filter": {"content": {"contains": "help"}},
            }
        ),
        context,
    )

    events = provider.poll()
    assert [event.event_id for event in events] == ["teams-dm-trigger:chat:dm-chat/msg-2"]

    provider.acknowledge_published_event(events[0])
    assert provider.poll() == []

    cursor_payload = json.loads(
        (context.resolved_config.runtime_dir / "trigger-cursors-teams.json").read_text(
            encoding="utf-8"
        )
    )
    assert cursor_payload["teams-dm-trigger"]["recent_cursors"] == ["chat:dm-chat/msg-2"]
