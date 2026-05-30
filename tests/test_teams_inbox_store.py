from __future__ import annotations

import json
from datetime import datetime, timezone
from multiprocessing import get_context
from pathlib import Path

import pytest

from agent_sandbox.messaging.teams_inbox_store import TeamsInboxMessage, TeamsInboxStore


def _message(
    cursor: str,
    *,
    created_at: datetime,
    team_id: str = "team-a",
    channel_id: str = "channel-1",
    message_id: str = "msg-1",
    thread_id: str | None = "msg-1",
    author_id: str = "user-1",
    author_name: str = "Ada",
    content: str = "hello",
) -> TeamsInboxMessage:
    return TeamsInboxMessage(
        cursor=cursor,
        message_id=message_id,
        team_id=team_id,
        channel_id=channel_id,
        thread_id=thread_id,
        author_id=author_id,
        author_name=author_name,
        content=content,
        created_at=created_at,
        metadata={"source": "teams_graph_api", "author_name": author_name},
    )


def _process_add_teams_message(
    path: str,
    cursor: str,
    message_id: str,
    created_at_iso: str,
    barrier,
    errors,
) -> None:
    try:
        barrier.wait(timeout=5)
        store = TeamsInboxStore(Path(path))
        store.add_message_if_new(
            _message(
                cursor,
                created_at=datetime.fromisoformat(created_at_iso),
                message_id=message_id,
            )
        )
    except BaseException as exc:  # pragma: no cover - exercised in subprocesses
        errors.put(repr(exc))
        raise


def test_teams_inbox_store_dedupes_messages(tmp_path: Path) -> None:
    store = TeamsInboxStore(tmp_path / "runtime" / "teams-inbox-store.json")
    envelope = _message(
        "team-a/channel-1/msg-1",
        created_at=datetime(2026, 3, 30, tzinfo=timezone.utc),
    )

    assert store.add_message_if_new(envelope) is True
    assert store.add_message_if_new(envelope) is False

    payload = store.read_raw()
    assert list(payload["messages"].keys()) == ["team-a/channel-1/msg-1"]
    assert (tmp_path / "runtime" / "teams-inbox-store.db").exists()


def test_teams_inbox_store_persists_and_resumes_channel_cursor_state(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "teams-inbox-store.json"
    store = TeamsInboxStore(path)

    store.set_cursor(
        "team-a/channel-1",
        "team-a/channel-1/msg-2",
        created_at=datetime(2026, 3, 30, 11, 2, tzinfo=timezone.utc),
    )

    reloaded = TeamsInboxStore(path)
    cursor_state = reloaded.get_cursor_state("team-a/channel-1")
    assert cursor_state is not None
    assert cursor_state.cursor == "team-a/channel-1/msg-2"
    assert cursor_state.created_at == datetime(2026, 3, 30, 11, 2, tzinfo=timezone.utc)


def test_teams_inbox_store_compacts_old_messages_once_channel_cursor_has_advanced(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "teams-inbox-store.json"
    store = TeamsInboxStore(path)
    old_message = _message(
        "team-a/channel-1/msg-1",
        created_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
    )

    assert store.add_message_if_new(old_message) is True
    store.set_cursor(
        "team-a/channel-1",
        "team-a/channel-1/msg-9",
        created_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )

    payload = store.read_raw()
    assert payload["messages"] == {}


def test_teams_inbox_store_filters_by_target_timestamp_and_direct_messages(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "teams-inbox-store.json"
    store = TeamsInboxStore(path)
    first = _message(
        "team-a/channel-1/msg-1",
        message_id="msg-1",
        created_at=datetime(2026, 3, 30, 10, 0, tzinfo=timezone.utc),
    )
    second = _message(
        "team-a/channel-1/msg-2",
        message_id="msg-2",
        created_at=datetime(2026, 3, 30, 10, 1, tzinfo=timezone.utc),
    )
    other_channel = _message(
        "team-b/channel-2/msg-3",
        message_id="msg-3",
        team_id="team-b",
        channel_id="channel-2",
        created_at=datetime(2026, 3, 30, 10, 2, tzinfo=timezone.utc),
    )
    direct_message = _message(
        "chat:chat-1/msg-4",
        message_id="msg-4",
        team_id="",
        channel_id="",
        thread_id=None,
        created_at=datetime(2026, 3, 30, 10, 3, tzinfo=timezone.utc),
    ).model_copy(update={"chat_id": "chat-1"})
    for message in (first, second, other_channel, direct_message):
        assert store.add_message_if_new(message) is True

    assert [
        message.cursor
        for message in store.list_all_messages(
            channel_targets=["team-a/channel-1"],
            created_at_from=second.created_at,
        )
    ] == ["team-a/channel-1/msg-2"]
    assert [message.cursor for message in store.list_all_messages(direct_messages_only=True)] == [
        "chat:chat-1/msg-4"
    ]


def test_teams_inbox_store_timestamp_filter_includes_same_second_fractional_messages(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "teams-inbox-store.json"
    store = TeamsInboxStore(path)
    whole_second_cursor = datetime(2026, 1, 1, tzinfo=timezone.utc)
    later_same_second = _message(
        "team-a/channel-1/msg-fractional",
        message_id="msg-fractional",
        created_at=datetime(2026, 1, 1, 0, 0, 0, 500_000, tzinfo=timezone.utc),
    )

    assert store.add_message_if_new(later_same_second) is True

    assert [
        message.cursor
        for message in store.list_all_messages(
            channel_targets=["team-a/channel-1"],
            created_at_from=whole_second_cursor,
        )
    ] == ["team-a/channel-1/msg-fractional"]


def test_teams_inbox_store_preserves_concurrent_independent_updates(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "teams-inbox-store.json"
    context = get_context("spawn")
    start = context.Barrier(3)
    errors = context.Queue()
    processes = [
        context.Process(
            target=_process_add_teams_message,
            args=(
                str(path),
                "team-a/channel-1/msg-a",
                "msg-a",
                "2026-03-30T10:00:00+00:00",
                start,
                errors,
            ),
        ),
        context.Process(
            target=_process_add_teams_message,
            args=(
                str(path),
                "team-a/channel-1/msg-b",
                "msg-b",
                "2026-03-30T10:00:01+00:00",
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

    payload = TeamsInboxStore(path).read_raw()
    assert set(payload["messages"]) == {
        "team-a/channel-1/msg-a",
        "team-a/channel-1/msg-b",
    }


def test_teams_inbox_store_imports_legacy_json_when_sqlite_store_is_empty(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "teams-inbox-store.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    created_at = datetime(2026, 3, 30, 10, 0, tzinfo=timezone.utc)
    path.write_text(
        json.dumps(
            {
                "cursors": {
                    "team-a/channel-1": {
                        "cursor": "team-a/channel-1/msg-1",
                        "created_at": created_at.isoformat().replace("+00:00", "Z"),
                    }
                },
                "messages": {
                    "team-a/channel-1/msg-1": _message(
                        "team-a/channel-1/msg-1",
                        created_at=created_at,
                    ).model_dump(mode="json")
                },
            }
        ),
        encoding="utf-8",
    )

    store = TeamsInboxStore(path)

    assert store.get_cursor("team-a/channel-1") == "team-a/channel-1/msg-1"
    assert [message.cursor for message in store.list_all_messages()] == ["team-a/channel-1/msg-1"]


def test_teams_inbox_store_raises_on_invalid_legacy_json(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime" / "teams-inbox-store.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        TeamsInboxStore(path)
