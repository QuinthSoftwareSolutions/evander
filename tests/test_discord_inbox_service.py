from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agent_sandbox.messaging.discord_inbox_store import DiscordInboxStore
from agent_sandbox.models import DiscordMessageEnvelope
from agent_sandbox.services import DiscordInboxService


def _write_store(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "cursors": {},
                "messages": {
                    "discord:1": {
                        "message_id": "discord:1",
                        "channel_id": "chan-a",
                        "author_id": "user-a",
                        "author_name": "Alice",
                        "content": "First",
                        "created_at": "2026-01-01T00:00:00Z",
                        "in_reply_to": None,
                        "raw": {"id": "discord:1"},
                    },
                    "discord:2": {
                        "message_id": "discord:2",
                        "channel_id": "chan-a",
                        "author_id": "user-b",
                        "author_name": "Bob",
                        "content": "Second",
                        "created_at": "2026-01-02T00:00:00Z",
                        "in_reply_to": "discord:1",
                        "raw": {"id": "discord:2"},
                    },
                    "discord:3": {
                        "message_id": "discord:3",
                        "channel_id": "chan-b",
                        "author_id": "user-c",
                        "author_name": "Cara",
                        "content": "Other channel",
                        "created_at": "2026-01-03T00:00:00Z",
                        "in_reply_to": None,
                        "raw": {"id": "discord:3"},
                    },
                },
                "session_reads": {},
            }
        ),
        encoding="utf-8",
    )


def test_has_pending_discord_messages_returns_minimal_shape(tmp_path: Path) -> None:
    store_path = tmp_path / "discord-inbox-store.json"
    _write_store(store_path)
    service = DiscordInboxService(store_path)

    response = service.has_pending_discord_messages(
        streams=[{"channel_id": "chan-a"}],
        cursor="2025-12-31T00:00:00Z",
    )

    assert response.pending is True
    assert response.next_cursor == "2026-01-02T00:00:00Z"


def test_list_unread_discord_messages_persists_session_cursor(tmp_path: Path) -> None:
    store_path = tmp_path / "discord-inbox-store.json"
    _write_store(store_path)
    service = DiscordInboxService(store_path)
    streams = [{"channel_id": "chan-a"}]

    first = service.list_unread_discord_messages(streams=streams, session_id="session-1", limit=1)
    assert [message.message_id for message in first.messages] == ["discord:1"]

    second = service.list_unread_discord_messages(streams=streams, session_id="session-1", limit=1)
    assert [message.message_id for message in second.messages] == ["discord:2"]


def test_list_all_discord_messages_returns_all_allowed_messages(tmp_path: Path) -> None:
    store_path = tmp_path / "discord-inbox-store.json"
    _write_store(store_path)
    service = DiscordInboxService(store_path)

    response = service.list_all_discord_messages(
        streams=[{"channel_id": "chan-a"}, {"channel_id": "chan-b"}],
        limit=10,
    )

    assert [message.message_id for message in response.messages] == [
        "discord:1",
        "discord:2",
        "discord:3",
    ]
    assert response.next_cursor == "2026-01-03T00:00:00Z"


def test_list_unread_discord_messages_skips_messages_posted_by_same_session(tmp_path: Path) -> None:
    store_path = tmp_path / "discord-inbox-store.json"
    store_path.write_text(
        json.dumps(
            {
                "cursors": {},
                "messages": {
                    "discord:1": {
                        "message_id": "discord:1",
                        "channel_id": "chan-a",
                        "author_id": "bot-user",
                        "author_name": "Bot",
                        "content": "Self echo",
                        "created_at": "2026-01-01T00:00:00Z",
                        "in_reply_to": None,
                        "raw": {"source": "discord_bot_api_status", "session_id": "session-1"},
                    },
                    "discord:2": {
                        "message_id": "discord:2",
                        "channel_id": "chan-a",
                        "author_id": "user-a",
                        "author_name": "Alice",
                        "content": "Human reply",
                        "created_at": "2026-01-02T00:00:00Z",
                        "in_reply_to": None,
                        "raw": {"source": "discord_bot_api"},
                    },
                },
                "session_reads": {},
            }
        ),
        encoding="utf-8",
    )
    service = DiscordInboxService(store_path)

    response = service.list_unread_discord_messages(
        streams=[{"channel_id": "chan-a"}],
        session_id="session-1",
    )

    assert [message.message_id for message in response.messages] == ["discord:2"]


def test_discord_inbox_store_compacts_expired_messages_after_channel_cursor_advances(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "discord-inbox-store.json"
    store = DiscordInboxStore(store_path)
    store.set_cursor("chan-a", "2024-01-02T00:00:00Z")

    inserted = store.add_message_if_new(
        DiscordMessageEnvelope(
            message_id="discord:old",
            channel_id="chan-a",
            author_id="user-a",
            author_name="Alice",
            content="Old message",
            created_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            raw={"id": "discord:old"},
        )
    )

    assert inserted is True
    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert payload["messages"] == {}
