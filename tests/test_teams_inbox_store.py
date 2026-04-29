from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

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


def test_teams_inbox_store_dedupes_messages(tmp_path: Path) -> None:
    store = TeamsInboxStore(tmp_path / "runtime" / "teams-inbox-store.json")
    envelope = _message(
        "team-a/channel-1/msg-1",
        created_at=datetime(2026, 3, 30, tzinfo=timezone.utc),
    )

    assert store.add_message_if_new(envelope) is True
    assert store.add_message_if_new(envelope) is False

    payload = json.loads(
        (tmp_path / "runtime" / "teams-inbox-store.json").read_text(encoding="utf-8")
    )
    assert list(payload["messages"].keys()) == ["team-a/channel-1/msg-1"]


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

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["messages"] == {}
