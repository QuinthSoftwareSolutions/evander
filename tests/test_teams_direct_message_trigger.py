from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from evander.config import GatewayConfig
from evander.events.provider_models import TeamsDirectMessageTriggerDefinition
from evander.messaging.teams_inbox_store import TeamsInboxMessage, TeamsInboxStore
from evander.triggers.providers.base import TriggerRuntimeContext
from evander.triggers.providers.teams_direct_message import (
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


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _seed_trigger_cursor(context: TriggerRuntimeContext, last_created_at: datetime) -> None:
    cursor_path = context.resolved_config.runtime_dir / "trigger-cursors-teams.json"
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    cursor_path.write_text(
        json.dumps(
            {
                "teams-dm-trigger": {
                    "last_created_at": _utc_text(last_created_at),
                    "recent_cursors": [],
                }
            }
        ),
        encoding="utf-8",
    )


def test_teams_direct_message_trigger_filters_to_allowlisted_direct_messages(
    tmp_path: Path,
) -> None:
    context = _runtime_context(tmp_path)
    store = TeamsInboxStore(context.resolved_config.runtime_dir / "teams-inbox-store.json")
    created_at = datetime(2026, 5, 25, 9, 30, tzinfo=UTC)
    _seed_trigger_cursor(context, created_at - timedelta(minutes=1))
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


def test_teams_direct_message_trigger_matches_resolved_sender_refs(
    tmp_path: Path,
) -> None:
    context = _runtime_context(tmp_path)
    store = TeamsInboxStore(context.resolved_config.runtime_dir / "teams-inbox-store.json")
    created_at = datetime(2026, 5, 25, 9, 30, tzinfo=UTC)
    _seed_trigger_cursor(context, created_at - timedelta(minutes=1))
    store.add_message_if_new(
        TeamsInboxMessage(
            cursor="chat:dm-chat/msg-1",
            message_id="msg-1",
            author_id="user-allowed",
            author_name="User Allowed",
            chat_id="dm-chat",
            content="allowed direct message",
            created_at=created_at,
            metadata={
                "source": "teams_graph_api",
                "target": "chat:dm-chat",
                "target_type": "direct_message",
                "chat_type": "oneOnOne",
                "direct_message_sender_refs": ["operator@example.com", "user-allowed"],
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
                "sender_user_ids": ["operator@example.com"],
            }
        ),
        context,
    )

    events = provider.poll()

    assert [event.event_id for event in events] == ["teams-dm-trigger:chat:dm-chat/msg-1"]
    assert events[0].payload["message"]["author_id"] == "user-allowed"


def test_teams_direct_message_trigger_applies_filter_and_acknowledges_cursor(
    tmp_path: Path,
) -> None:
    context = _runtime_context(tmp_path)
    store = TeamsInboxStore(context.resolved_config.runtime_dir / "teams-inbox-store.json")
    _seed_trigger_cursor(context, datetime(2026, 5, 25, 9, 30, tzinfo=UTC))
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


def test_teams_direct_message_trigger_initializes_without_backfilling_existing_messages(
    tmp_path: Path,
) -> None:
    context = _runtime_context(tmp_path)
    store = TeamsInboxStore(context.resolved_config.runtime_dir / "teams-inbox-store.json")
    old_created_at = datetime.now(UTC) - timedelta(days=1)
    store.add_message_if_new(
        TeamsInboxMessage(
            cursor="chat:dm-chat/old",
            message_id="old",
            author_id="user-a",
            author_name="User A",
            chat_id="dm-chat",
            content="old direct message",
            created_at=old_created_at,
            metadata={
                "source": "teams_graph_api",
                "target": "chat:dm-chat",
                "target_type": "direct_message",
                "chat_type": "oneOnOne",
            },
        )
    )

    provider = TeamsDirectMessageTriggerProvider()
    provider.initialize(_trigger_definition(), context)

    first_poll_started_at = datetime.now(UTC)
    assert provider.poll() == []
    first_poll_finished_at = datetime.now(UTC)

    cursor_payload = json.loads(
        (context.resolved_config.runtime_dir / "trigger-cursors-teams.json").read_text(
            encoding="utf-8"
        )
    )
    initialized_at = datetime.fromisoformat(
        cursor_payload["teams-dm-trigger"]["initialized_at"].replace("Z", "+00:00")
    )
    assert first_poll_started_at <= initialized_at <= first_poll_finished_at

    new_created_at = first_poll_finished_at + timedelta(seconds=1)
    store.add_message_if_new(
        TeamsInboxMessage(
            cursor="chat:dm-chat/new",
            message_id="new",
            author_id="user-a",
            author_name="User A",
            chat_id="dm-chat",
            content="new direct message",
            created_at=new_created_at,
            metadata={
                "source": "teams_graph_api",
                "target": "chat:dm-chat",
                "target_type": "direct_message",
                "chat_type": "oneOnOne",
            },
        )
    )

    events = provider.poll()

    assert [event.event_id for event in events] == ["teams-dm-trigger:chat:dm-chat/new"]
