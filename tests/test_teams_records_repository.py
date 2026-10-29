from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from evander.models import TeamsMessageEnvelope
from evander.repositories.records import JsonTeamsRecordsRepository, SqliteTeamsRecordsRepository
from evander.teams_outbound import TeamsOutboundReceipt, TeamsOutboundEditState, TeamsPendingEdit


@pytest.mark.parametrize("repository", [JsonTeamsRecordsRepository, SqliteTeamsRecordsRepository])
def test_image_authority_and_pending_source_survive_restart_without_inbox_authority(
    tmp_path, repository
):
    from datetime import timedelta
    from evander.teams_outbound import TeamsImageSlot, TeamsImageVersion

    path = tmp_path / "records"
    repo = repository(path)
    now = datetime.now(UTC)
    image = TeamsImageVersion(
        artifact_id="comm_" + "a" * 32,
        sha256="b" * 64,
        source_url="https://media.example/image?token=synthetic",
        expires_at=now + timedelta(hours=1),
        renew_at=now + timedelta(minutes=30),
    )
    receipt = TeamsOutboundReceipt(
        channel_target="team-a/channel-1",
        message_id="same-id",
        session_id="session-a",
        author={"id": "sender"},
        image_slot=TeamsImageSlot(before="", after=""),
    )
    state = TeamsOutboundEditState(
        text_html="<p>First</p>",
        body_html="<p>First</p>",
        image=image,
        pending=TeamsPendingEdit(text_html="<p>Next</p>", body_html="<p>Next</p>", image=image),
    )
    repo.create_outbound_receipt(receipt, state)
    repo.put("team-a/channel-1/same-id", _message("team-a/channel-1/same-id"))
    restarted = repository(path)
    assert restarted.get_outbound_edit_state("team-a/channel-1", "same-id") == state
    assert (
        restarted.get_outbound_receipt("team-a/channel-1", "same-id").image_slot
        == receipt.image_slot
    )
    assert restarted.get_outbound_receipt("team-b/channel-2", "same-id") is None
    assert path.stat().st_mode & 0o777 == 0o600


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


@pytest.mark.parametrize("repository", [JsonTeamsRecordsRepository, SqliteTeamsRecordsRepository])
def test_outbound_authority_is_write_once_and_survives_inbox_replacement(tmp_path, repository):
    path = tmp_path / "teams"
    repo = repository(path)
    receipt = TeamsOutboundReceipt(
        channel_target="team-a/channel-1",
        message_id="same-id",
        session_id="session-a",
        thread_id="same-id",
        author={"id": "sender"},
    )
    state = TeamsOutboundEditState(text_html="<p>Original</p>", body_html="<p>Original</p>")
    repo.create_outbound_receipt(receipt, state)
    other = receipt.model_copy(
        update={"channel_target": "team-b/channel-2", "session_id": "session-b"}
    )
    repo.create_outbound_receipt(other, state)
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        repo.create_outbound_receipt(receipt.model_copy(update={"session_id": "intruder"}), state)
    # A refresh may replace sender metadata or the entire inbound cache. Neither
    # creates, changes, or removes durable send authority.
    refreshed = _message("team-a/channel-1/same-id", message_id="same-id")
    repo.put_many({refreshed.cursor: refreshed})
    legacy = _message("team-a/channel-1/legacy")
    repo.put(legacy.cursor, legacy)
    assert repository(path).get(legacy.cursor) == legacy
    assert repository(path).get_outbound_receipt("team-a/channel-1", "legacy") is None
    if repository is JsonTeamsRecordsRepository:
        path.write_text("{}")
    else:
        with repo._connect() as connection:
            connection.execute("DELETE FROM teams_message_records")
    restarted = repository(path)
    assert restarted.get_outbound_receipt("team-a/channel-1", "same-id") == receipt
    assert restarted.get_outbound_receipt("team-b/channel-2", "same-id") == other
    assert restarted.get_outbound_receipt("team-a/channel-1", "legacy") is None
    pending = state.model_copy(
        update={"pending": TeamsPendingEdit(text_html="<p>Next</p>", body_html="<p>Next</p>")}
    )
    with restarted.lock_outbound_message(receipt.channel_target, receipt.message_id):
        restarted.save_outbound_edit_state(receipt.channel_target, receipt.message_id, pending)
    assert (
        repository(path).get_outbound_edit_state(receipt.channel_target, receipt.message_id)
        == pending
    )
    assert (
        repository(path).get_outbound_receipt(receipt.channel_target, receipt.message_id) == receipt
    )


@pytest.mark.parametrize("repository", [JsonTeamsRecordsRepository, SqliteTeamsRecordsRepository])
def test_outbound_message_lock_serializes_independent_processes(tmp_path, repository):
    path = tmp_path / "teams"
    repo = repository(path)
    repo.create_outbound_receipt(
        TeamsOutboundReceipt(
            channel_target="team/channel",
            message_id="message",
            session_id="session",
            author={"id": "sender"},
        ),
        TeamsOutboundEditState(text_html="", body_html=""),
    )
    script = """
import sys,time
from pathlib import Path
from evander.repositories import records
repo = getattr(records, sys.argv[1])(Path(sys.argv[2]))
for _ in range(3):
    with repo.lock_outbound_message('team/channel', 'message'):
        state = repo.get_outbound_edit_state('team/channel', 'message')
        time.sleep(.02)
        repo.save_outbound_edit_state('team/channel', 'message', state.model_copy(update={'revision': state.revision + 1}))
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", script, repository.__name__, str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(3)
    ]
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, (stdout, stderr)
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()
    assert repo.get_outbound_edit_state("team/channel", "message").revision == 10
