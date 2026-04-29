from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agent_sandbox.messaging.discord_inbox_store import DiscordInboxStore
from agent_sandbox.models import DiscordMessageEnvelope


def _message(
    message_id: str,
    *,
    channel_id: str = "chan-1",
    created_at: datetime,
    content: str = "hello",
    author_id: str = "user-1",
    author_name: str = "Octocat",
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
        raw={"id": message_id, "channel_id": channel_id, "content": content},
    )


def test_discord_message_envelope_preserves_raw_metadata() -> None:
    envelope = _message(
        "msg-1",
        created_at=datetime(2026, 3, 21, tzinfo=timezone.utc),
        in_reply_to="msg-0",
    )

    assert envelope.raw["id"] == "msg-1"
    assert envelope.in_reply_to == "msg-0"
    assert envelope.model_dump(mode="json")["raw"]["content"] == "hello"


def test_discord_inbox_store_dedupes_messages(tmp_path: Path) -> None:
    store = DiscordInboxStore(tmp_path / "runtime" / "discord-inbox-store.json")
    envelope = _message("msg-1", created_at=datetime(2026, 3, 21, tzinfo=timezone.utc))

    assert store.add_message_if_new(envelope) is True
    assert store.add_message_if_new(envelope) is False

    payload = json.loads(
        (tmp_path / "runtime" / "discord-inbox-store.json").read_text(encoding="utf-8")
    )
    assert list(payload["messages"].keys()) == ["msg-1"]


def test_discord_inbox_store_persists_and_resumes_channel_cursors(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "discord-inbox-store.json"
    store = DiscordInboxStore(path)

    store.set_cursor("chan-1", "2026-03-21T00:00:00Z")

    reloaded = DiscordInboxStore(path)
    assert reloaded.get_cursor("chan-1") == "2026-03-21T00:00:00Z"


def test_discord_inbox_store_persists_session_read_cursors(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "discord-inbox-store.json"
    store = DiscordInboxStore(path)

    store.advance_session_cursor("session-1", "chan-1", "2026-03-21T00:00:00Z")
    store.advance_session_cursor("session-1", "chan-1", "2026-03-20T00:00:00Z")

    reloaded = DiscordInboxStore(path)
    assert reloaded.get_session_cursor("session-1", "chan-1") == "2026-03-21T00:00:00Z"


def test_discord_inbox_store_reports_unread_messages_per_session(tmp_path: Path) -> None:
    store = DiscordInboxStore(tmp_path / "runtime" / "discord-inbox-store.json")
    assert store.add_message_if_new(
        _message("msg-1", created_at=datetime(2026, 3, 21, tzinfo=timezone.utc))
    )

    assert store.has_unread_messages(session_id="session-1", channel_ids=["chan-1"]) is True

    store.advance_session_cursor("session-1", "chan-1", "2026-03-21T00:00:00Z")

    assert store.has_unread_messages(session_id="session-1", channel_ids=["chan-1"]) is False


def test_discord_inbox_store_lists_unread_messages_and_advances_session_cursors(
    tmp_path: Path,
) -> None:
    store = DiscordInboxStore(tmp_path / "runtime" / "discord-inbox-store.json")
    assert store.add_message_if_new(
        _message("msg-1", created_at=datetime(2026, 3, 21, 0, 0, 1, tzinfo=timezone.utc))
    )
    assert store.add_message_if_new(
        _message("msg-2", created_at=datetime(2026, 3, 21, 0, 0, 2, tzinfo=timezone.utc))
    )

    result = store.list_unread_messages(session_id="session-1", channel_ids=["chan-1"], limit=1)

    assert [message.message_id for message in result.messages] == ["msg-1"]
    assert result.next_cursor == "2026-03-21T00:00:01Z"
    assert store.get_session_cursor("session-1", "chan-1") == "2026-03-21T00:00:01Z"


def test_discord_inbox_store_compacts_old_messages_once_channel_cursor_has_advanced(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "discord-inbox-store.json"
    store = DiscordInboxStore(path)
    old_message = _message(
        "msg-1",
        created_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
    )

    assert store.add_message_if_new(old_message) is True
    store.set_cursor("chan-1", "2099-01-01T00:00:00Z")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["messages"] == {}


def test_discord_inbox_store_keeps_recent_messages_even_when_channel_cursor_has_advanced(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "discord-inbox-store.json"
    store = DiscordInboxStore(path)
    recent_message = _message(
        "msg-1",
        created_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )

    assert store.add_message_if_new(recent_message) is True
    store.set_cursor("chan-1", "2099-01-02T00:00:00Z")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert list(payload["messages"]) == ["msg-1"]
