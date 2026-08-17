from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from evander.models import TeamsMessageEnvelope
from evander.repositories.records import SqliteTeamsRecordsRepository


def _message(cursor: str, *, message_id: str | None = None) -> TeamsMessageEnvelope:
    return TeamsMessageEnvelope(
        cursor=cursor,
        message_id=message_id or cursor.rsplit("/", 1)[-1],
        author_id="user-1",
        team_id="team-a",
        channel_id="channel-1",
        content="hello",
        created_at=datetime(2026, 5, 26, 10, 0, tzinfo=UTC),
        metadata={"source": "teams_graph_api"},
    )


def test_sqlite_teams_records_repository_put_many_round_trips(tmp_path: Path) -> None:
    repo = SqliteTeamsRecordsRepository(tmp_path / "teams_messages.db")
    first = _message("team-a/channel-1/msg-1")
    second = _message("team-a/channel-1/msg-2")

    repo.put_many({first.cursor: first, second.cursor: second})
    updated = second.model_copy(update={"content": "updated"})
    repo.put(second.cursor, updated)

    assert repo.get(first.cursor) == first
    assert repo.get(second.cursor) == updated
    assert set(repo.read_all()) == {first.cursor, second.cursor}
