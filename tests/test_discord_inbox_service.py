from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from evander.messaging.discord_inbox_store import DiscordInboxStore
from evander.models import DiscordMessageEnvelope
from evander.services import DiscordInboxService


def _message(
    message_id: str,
    *,
    channel_id: str,
    created_at: datetime,
    content: str,
    author_id: str,
    author_name: str,
    raw: dict[str, object] | None = None,
    in_reply_to: str | None = None,
) -> DiscordMessageEnvelope:
    return DiscordMessageEnvelope(
        message_id=message_id,
        channel_id=channel_id,
        author_id=author_id,
        author_name=author_name,
        content=content,
        created_at=created_at,
        in_reply_to=in_reply_to,
        raw=raw or {"id": message_id},
    )


def _seed_store(path: Path) -> DiscordInboxStore:
    store = DiscordInboxStore(path)
    assert (
        store.add_messages_if_new(
            [
                _message(
                    "discord:1",
                    channel_id="chan-a",
                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    content="First",
                    author_id="user-a",
                    author_name="Alice",
                ),
                _message(
                    "discord:2",
                    channel_id="chan-a",
                    created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                    content="Second",
                    author_id="user-b",
                    author_name="Bob",
                    in_reply_to="discord:1",
                ),
                _message(
                    "discord:3",
                    channel_id="chan-b",
                    created_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
                    content="Other channel",
                    author_id="user-c",
                    author_name="Cara",
                ),
            ]
        )
        == 3
    )
    return store


def test_has_pending_discord_messages_returns_minimal_shape(tmp_path: Path) -> None:
    store_path = tmp_path / "discord-inbox-store.json"
    store = _seed_store(store_path)
    service = DiscordInboxService(store_path)

    response = service.has_pending_discord_messages(
        streams=[{"channel_id": "chan-a"}],
        cursor="2025-12-31T00:00:00Z",
    )

    assert service.store.path == store.path
    assert response.pending is True
    assert response.next_cursor == "2026-01-02T00:00:00Z"


def test_list_unread_discord_messages_persists_session_cursor(tmp_path: Path) -> None:
    store_path = tmp_path / "discord-inbox-store.json"
    _seed_store(store_path)
    service = DiscordInboxService(store_path)
    streams = [{"channel_id": "chan-a"}]

    first = service.list_unread_discord_messages(streams=streams, session_id="session-1", limit=1)
    assert [message.message_id for message in first.messages] == ["discord:1"]
    assert service.store.get_session_cursor("session-1", "chan-a") == "2026-01-01T00:00:00Z"

    second = service.list_unread_discord_messages(streams=streams, session_id="session-1", limit=1)
    assert [message.message_id for message in second.messages] == ["discord:2"]
    assert service.store.get_session_cursor("session-1", "chan-a") == "2026-01-02T00:00:00Z"


def test_list_all_discord_messages_returns_all_allowed_messages(tmp_path: Path) -> None:
    store_path = tmp_path / "discord-inbox-store.json"
    _seed_store(store_path)
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


def test_has_pending_discord_messages_uses_list_all_messages(tmp_path: Path) -> None:
    class _RecordingStore:
        def __init__(self) -> None:
            self.list_all_messages_calls: list[list[str]] = []

        def list_all_messages(self, *, channel_ids: list[str], limit: int | None = None):
            self.list_all_messages_calls.append(channel_ids)
            return type(
                "_Result",
                (),
                {
                    "messages": [
                        _message(
                            "discord:1",
                            channel_id="chan-a",
                            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                            content="First",
                            author_id="user-a",
                            author_name="Alice",
                        )
                    ],
                    "next_cursor": "2026-01-01T00:00:00Z",
                },
            )()

        def get_session_cursor(self, session_id: str, channel_id: str) -> str | None:
            return None

        def advance_session_cursor(
            self, session_id: str, channel_id: str, cursor: str | None
        ) -> str | None:
            return cursor

        def read_raw(self) -> dict[str, object]:
            raise AssertionError("read_raw should not be used for normal list operations")

    service = DiscordInboxService(tmp_path / "discord-inbox-store.json")
    recording_store = _RecordingStore()
    service.store = recording_store

    response = service.has_pending_discord_messages(streams=[{"channel_id": "chan-a"}])

    assert recording_store.list_all_messages_calls == [["chan-a"]]
    assert response.pending is True
    assert response.next_cursor == "2026-01-01T00:00:00Z"


def test_list_unread_discord_messages_skips_messages_posted_by_same_session(
    tmp_path: Path,
) -> None:
    store_path = tmp_path / "discord-inbox-store.json"
    store = DiscordInboxStore(store_path)
    assert (
        store.add_messages_if_new(
            [
                _message(
                    "discord:1",
                    channel_id="chan-a",
                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    content="Self echo",
                    author_id="bot-user",
                    author_name="Bot",
                    raw={
                        "id": "discord:1",
                        "source": "discord_bot_api_status",
                        "session_id": "session-1",
                    },
                ),
                _message(
                    "discord:2",
                    channel_id="chan-a",
                    created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                    content="Human reply",
                    author_id="user-a",
                    author_name="Alice",
                    raw={"id": "discord:2", "source": "discord_bot_api"},
                ),
            ]
        )
        == 2
    )
    service = DiscordInboxService(store_path)

    response = service.list_unread_discord_messages(
        streams=[{"channel_id": "chan-a"}],
        session_id="session-1",
    )

    assert [message.message_id for message in response.messages] == ["discord:2"]
    assert response.next_cursor == "2026-01-02T00:00:00Z"
    assert service.store.get_session_cursor("session-1", "chan-a") == "2026-01-02T00:00:00Z"


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
    payload = store.read_raw()
    assert payload["messages"] == {}
