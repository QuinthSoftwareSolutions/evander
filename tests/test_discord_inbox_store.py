from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from multiprocessing import get_context
from pathlib import Path

import pytest

from evander.messaging.discord_inbox_store import (
    DiscordInboxStore,
    discord_inbox_db_path,
)
from evander.models import DiscordMessageEnvelope


def _parse_cursor(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


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


def _process_add_discord_message(
    path: str,
    message_id: str,
    created_at_iso: str,
    barrier,
    errors,
) -> None:
    try:
        barrier.wait(timeout=5)
        store = DiscordInboxStore(Path(path))
        store.add_message_if_new(
            _message(
                message_id,
                created_at=datetime.fromisoformat(created_at_iso),
            )
        )
    except BaseException as exc:  # pragma: no cover - exercised in subprocesses
        errors.put(repr(exc))
        raise


def test_discord_message_envelope_preserves_raw_metadata() -> None:
    envelope = _message(
        "msg-1",
        created_at=datetime(2026, 3, 21, tzinfo=timezone.utc),
        in_reply_to="msg-0",
    )

    assert envelope.raw["id"] == "msg-1"
    assert envelope.in_reply_to == "msg-0"
    assert envelope.model_dump(mode="json")["raw"]["content"] == "hello"


@pytest.mark.parametrize(
    ("input_path", "expected_path"),
    [
        ("runtime/discord-inbox-store.db", "runtime/discord-inbox-store.db"),
        ("runtime/discord-inbox-store.sqlite", "runtime/discord-inbox-store.sqlite"),
        (
            "runtime/discord-inbox-store.sqlite3",
            "runtime/discord-inbox-store.sqlite3",
        ),
    ],
)
def test_discord_inbox_db_path(tmp_path: Path, input_path: str, expected_path: str) -> None:
    path = tmp_path / input_path

    assert discord_inbox_db_path(path) == tmp_path / expected_path


def test_discord_inbox_store_uses_sqlite_db_path(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "discord-inbox-store.db"

    store = DiscordInboxStore(path)

    assert store.path == path
    assert store.path.exists()


def test_discord_inbox_store_dedupes_messages(tmp_path: Path) -> None:
    store = DiscordInboxStore(tmp_path / "runtime" / "discord-inbox-store.json")
    envelope = _message("msg-1", created_at=datetime(2026, 3, 21, tzinfo=timezone.utc))

    assert store.add_message_if_new(envelope) is True
    assert store.add_message_if_new(envelope) is False

    payload = store.read_raw()
    assert list(payload["messages"].keys()) == ["msg-1"]
    assert store.path.exists()


def test_discord_inbox_store_batches_multiple_messages(tmp_path: Path) -> None:
    store = DiscordInboxStore(tmp_path / "runtime" / "discord-inbox-store.json")
    first = _message("msg-1", created_at=datetime(2026, 3, 21, 0, 0, 1, tzinfo=timezone.utc))
    second = _message("msg-2", created_at=datetime(2026, 3, 21, 0, 0, 2, tzinfo=timezone.utc))

    assert store.add_messages_if_new([first, second]) == 2
    assert store.add_messages_if_new([first, second]) == 0
    assert [
        message.message_id for message in store.list_all_messages(channel_ids=["chan-1"]).messages
    ] == [
        "msg-1",
        "msg-2",
    ]


def test_discord_inbox_store_persists_and_resumes_channel_cursors(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "discord-inbox-store.json"
    store = DiscordInboxStore(path)

    store.set_cursor("chan-1", "2026-03-21T00:00:00Z")

    reloaded = DiscordInboxStore(path)
    assert reloaded.get_cursor("chan-1") == "2026-03-21T00:00:00Z"


def test_discord_inbox_store_deletes_channel_cursor_when_cleared(tmp_path: Path) -> None:
    store = DiscordInboxStore(tmp_path / "runtime" / "discord-inbox-store.json")

    store.set_cursor("chan-1", "2026-03-21T00:00:00Z")
    store.set_cursor("chan-1", None)

    assert store.get_cursor("chan-1") is None
    assert store.read_raw()["cursors"] == {}


def test_discord_inbox_store_persists_session_read_cursors(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "discord-inbox-store.json"
    store = DiscordInboxStore(path)

    store.advance_session_cursor("session-1", "chan-1", "2026-03-21T00:00:00Z")
    store.advance_session_cursor("session-1", "chan-1", "2026-03-20T00:00:00Z")

    reloaded = DiscordInboxStore(path)
    assert reloaded.get_session_cursor("session-1", "chan-1") == "2026-03-21T00:00:00Z"


def test_discord_inbox_store_advances_session_cursor_monotonically(
    tmp_path: Path,
) -> None:
    store = DiscordInboxStore(tmp_path / "runtime" / "discord-inbox-store.json")

    assert store.advance_session_cursor("session-1", "chan-1", "2026-03-21T00:00:00Z")
    assert (
        store.advance_session_cursor("session-1", "chan-1", "2026-03-20T00:00:00Z")
        == "2026-03-21T00:00:00Z"
    )


def test_discord_inbox_store_fast_forwards_session_cursor_to_latest_message(
    tmp_path: Path,
) -> None:
    store = DiscordInboxStore(tmp_path / "runtime" / "discord-inbox-store.json")
    latest_message_at = datetime.now(timezone.utc) - timedelta(days=1)
    assert (
        store.add_messages_if_new(
            [
                _message("msg-1", created_at=latest_message_at - timedelta(days=1)),
                _message("msg-2", created_at=latest_message_at),
            ]
        )
        == 2
    )

    before = datetime.now(timezone.utc)
    advanced = store.fast_forward_session_cursors_to_latest("session-1", ["chan-1"])
    after = datetime.now(timezone.utc)

    cursor = advanced["chan-1"]
    parsed_cursor = _parse_cursor(cursor)
    assert before <= parsed_cursor <= after
    assert parsed_cursor >= latest_message_at
    assert store.get_session_cursor("session-1", "chan-1") == cursor
    assert store.has_unread_messages(session_id="session-1", channel_ids=["chan-1"]) is False


def test_discord_inbox_store_fast_forwards_session_cursor_without_cached_messages(
    tmp_path: Path,
) -> None:
    store = DiscordInboxStore(tmp_path / "runtime" / "discord-inbox-store.json")

    before = datetime.now(timezone.utc)
    advanced = store.fast_forward_session_cursors_to_latest("session-1", ["chan-1"])
    after = datetime.now(timezone.utc)

    cursor = advanced["chan-1"]
    parsed_cursor = _parse_cursor(cursor)
    assert before <= parsed_cursor <= after
    assert store.get_session_cursor("session-1", "chan-1") == cursor

    assert store.add_message_if_new(_message("old-msg", created_at=before - timedelta(seconds=1)))
    assert store.has_unread_messages(session_id="session-1", channel_ids=["chan-1"]) is False

    assert store.add_message_if_new(_message("new-msg", created_at=after + timedelta(seconds=1)))
    assert store.has_unread_messages(session_id="session-1", channel_ids=["chan-1"]) is True


def test_discord_inbox_store_fast_forwards_to_later_channel_cursor(
    tmp_path: Path,
) -> None:
    store = DiscordInboxStore(tmp_path / "runtime" / "discord-inbox-store.json")
    store.set_cursor("chan-1", "2099-01-01T00:00:00Z")

    advanced = store.fast_forward_session_cursors_to_latest("session-1", ["chan-1"])

    assert advanced == {"chan-1": "2099-01-01T00:00:00Z"}
    assert store.get_session_cursor("session-1", "chan-1") == "2099-01-01T00:00:00Z"


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


def test_discord_inbox_store_lists_all_messages_in_created_order(
    tmp_path: Path,
) -> None:
    store = DiscordInboxStore(tmp_path / "runtime" / "discord-inbox-store.json")
    messages = [
        _message(
            "msg-b",
            channel_id="chan-b",
            created_at=datetime(2026, 3, 21, 0, 0, 2, tzinfo=timezone.utc),
        ),
        _message(
            "msg-a",
            channel_id="chan-a",
            created_at=datetime(2026, 3, 21, 0, 0, 1, tzinfo=timezone.utc),
        ),
        _message(
            "msg-c",
            channel_id="chan-a",
            created_at=datetime(2026, 3, 21, 0, 0, 2, tzinfo=timezone.utc),
        ),
    ]
    for message in messages:
        assert store.add_message_if_new(message) is True

    result = store.list_all_messages(channel_ids=["chan-a", "chan-b"], limit=2)

    assert [message.message_id for message in result.messages] == ["msg-a", "msg-b"]
    assert result.next_cursor == "2026-03-21T00:00:02Z"


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

    payload = store.read_raw()
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

    payload = store.read_raw()
    assert list(payload["messages"]) == ["msg-1"]


def test_discord_inbox_store_preserves_concurrent_independent_updates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "discord-inbox-store.json"
    context = get_context("spawn")
    start = context.Barrier(3)
    errors = context.Queue()
    processes = [
        context.Process(
            target=_process_add_discord_message,
            args=(
                str(path),
                "msg-a",
                "2026-03-21T00:00:00+00:00",
                start,
                errors,
            ),
        ),
        context.Process(
            target=_process_add_discord_message,
            args=(
                str(path),
                "msg-b",
                "2026-03-21T00:00:01+00:00",
                start,
                errors,
            ),
        ),
    ]

    for process in processes:
        process.start()
    start.wait(timeout=5)
    for process in processes:
        process.join(timeout=5)

    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(timeout=2)
        assert process.exitcode == 0
    assert errors.empty()

    payload = DiscordInboxStore(path).read_raw()
    assert set(payload["messages"]) == {"msg-a", "msg-b"}


def test_discord_inbox_store_ignores_retired_json_input(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "discord-inbox-store.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc).replace(microsecond=0)
    cursor = created_at.isoformat().replace("+00:00", "Z")
    path.write_text(
        json.dumps(
            {
                "cursors": {
                    "chan-1": cursor,
                },
                "messages": {
                    "msg-1": _message(
                        "msg-1",
                        created_at=created_at,
                    ).model_dump(mode="json"),
                    "msg-bad": {"message_id": "msg-bad"},
                },
                "session_reads": {
                    "session-1": {
                        "chan-1": cursor,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    store = DiscordInboxStore(path)

    assert store.path.exists()
    assert store.get_cursor("chan-1") is None
    assert store.list_all_messages(channel_ids=["chan-1"]).messages == []
    assert store.get_session_cursor("session-1", "chan-1") is None


def test_discord_inbox_store_persists_public_thread_bindings(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "discord-inbox-store.json"
    observed_at = datetime(2026, 8, 17, 8, 0, tzinfo=timezone.utc)
    store = DiscordInboxStore(path)

    assert store.register_thread_parent("parent-1", observed_at=observed_at) is True
    assert store.register_thread_parent("parent-1", observed_at=observed_at) is False
    assert store.upsert_thread_binding(
        thread_id="thread-1",
        parent_channel_id="parent-1",
        guild_id="guild-1",
        thread_type=11,
        archived=False,
        locked=False,
        observed_at=observed_at,
    ) is True

    binding = DiscordInboxStore(path).get_thread_binding("thread-1")
    assert binding is not None
    assert binding.parent_channel_id == "parent-1"
    assert binding.archived is False

    store.mark_missing_parent_threads_archived(
        parent_channel_id="parent-1",
        active_thread_ids=[],
        observed_at=observed_at,
    )
    assert store.list_thread_bindings(
        parent_channel_ids=["parent-1"], include_archived=False
    ) == []
    assert store.get_thread_binding("thread-1").archived is True
