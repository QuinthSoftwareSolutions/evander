from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from evander.messaging.discord_inbox_store import DiscordInboxStore
from evander.model_usage.models import ModelInferenceUsageEvent
from evander.model_usage.store import ModelUsageStore
from evander.models import DiscordMessageEnvelope


def _messages(count, *, channel_id="channel", first=None):
    first = first or datetime(2099, 1, 1, tzinfo=UTC)
    return [
        DiscordMessageEnvelope(
            message_id=f"{channel_id}-{index:04}",
            channel_id=channel_id,
            author_id="user",
            author_name="User",
            content=f"message {index}",
            created_at=first + timedelta(microseconds=index),
        )
        for index in range(count)
    ]


@pytest.mark.parametrize("unread", [False, True])
def test_discord_pages_decode_only_returned_valid_messages(tmp_path, monkeypatch, unread):
    store = DiscordInboxStore(tmp_path / "discord.db")
    store.add_messages_if_new(_messages(500))
    # A corrupt first unread row must not consume a page slot.
    with store._connect() as connection:
        connection.execute(
            "UPDATE discord_inbox_messages SET payload_json = 'invalid' WHERE message_id = ?",
            ("channel-0201" if unread else "channel-0000",),
        )
    if unread:
        store.advance_session_cursor("session", "channel", "2099-01-01T01:00:00.000200+01:00")

    decoded = []
    validate = store._validate_message
    instruction_count = 0
    connect = store._connect

    def count_instruction():
        nonlocal instruction_count
        instruction_count += 1
        return 0

    def measured_connect():
        connection = connect()
        connection.set_progress_handler(count_instruction, 1)
        return connection

    def measured_validate(payload):
        decoded.append(payload)
        return validate(payload)

    monkeypatch.setattr(store, "_validate_message", measured_validate)
    monkeypatch.setattr(store, "_connect", measured_connect)
    if unread:
        page = store.list_unread_messages(session_id="session", channel_ids=["channel"], limit=5)
        expected_start = 202
        assert store.get_session_cursor("session", "channel") == page.next_cursor
        assert instruction_count < 1000
    else:
        page = store.list_all_messages(channel_ids=["channel"], limit=5)
        expected_start = 1
    assert [item.message_id for item in page.messages] == [
        f"channel-{index:04}" for index in range(expected_start, expected_start + 5)
    ]
    assert len(decoded) == 6


def test_discord_unread_page_keeps_channel_cursors_timestamp_ties_and_isolation(tmp_path):
    store = DiscordInboxStore(tmp_path / "discord.db")
    first = datetime(2099, 1, 1, tzinfo=UTC)
    store.add_messages_if_new(
        _messages(4, channel_id="one", first=first)
        + _messages(4, channel_id="two", first=first)
        + _messages(4, channel_id="private", first=first)
    )
    store.advance_session_cursor("session", "one", "2099-01-01T00:00:00.000001Z")
    store.advance_session_cursor("session", "two", "2099-01-01T00:00:00Z")
    store.advance_session_cursor("other-session", "one", "2099-01-01T00:00:00.000003Z")
    # A late arrival exactly at the already-read timestamp remains read.
    store.add_message_if_new(
        _messages(1, channel_id="one", first=first + timedelta(microseconds=1))[0].model_copy(
            update={"message_id": "late-equal"}
        )
    )
    page = store.list_unread_messages(session_id="session", channel_ids=["one", "two"], limit=3)
    assert [item.message_id for item in page.messages] == ["two-0001", "one-0002", "two-0002"]
    assert store.get_session_cursor("session", "one") == "2099-01-01T00:00:00.000002Z"
    assert store.get_session_cursor("session", "two") == "2099-01-01T00:00:00.000002Z"
    assert store.get_session_cursor("other-session", "one") == "2099-01-01T00:00:00.000003Z"
    assert store.get_session_cursor("session", "private") is None


@pytest.mark.parametrize("unread", [False, True])
@pytest.mark.parametrize("limit, expected", [(0, []), (-1, ["channel-0000", "channel-0001"])])
def test_discord_pages_preserve_zero_and_negative_limit_slices(tmp_path, unread, limit, expected):
    store = DiscordInboxStore(tmp_path / "discord.db")
    store.add_messages_if_new(_messages(3))
    if unread:
        result = store.list_unread_messages(
            session_id="session", channel_ids=["channel"], limit=limit
        )
    else:
        result = store.list_all_messages(channel_ids=["channel"], limit=limit)
    assert [message.message_id for message in result.messages] == expected


def test_discord_unread_probe_uses_two_queries_for_many_channels(tmp_path, monkeypatch):
    store = DiscordInboxStore(tmp_path / "discord.db")
    channels = [f"channel-{index}" for index in range(240)]
    store.add_messages_if_new(_messages(1, channel_id=channels[-1]))
    store.add_messages_if_new(_messages(1, channel_id="private"))
    with store._connect() as connection:
        connection.executemany(
            "INSERT INTO discord_inbox_session_reads VALUES (?, ?, ?)",
            [("session", channel, "2099-01-01T01:00:00+01:00") for channel in channels],
        )
    statements = []
    connect = store._connect

    def measured_connect():
        connection = connect()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(store, "_connect", measured_connect)
    assert store.has_unread_messages(session_id="session", channel_ids=channels) is False
    assert (
        len([statement for statement in statements if statement.lstrip().startswith("SELECT")]) == 2
    )
    statements.clear()
    assert store.has_unread_messages(session_id="other-session", channel_ids=channels) is True
    assert (
        len([statement for statement in statements if statement.lstrip().startswith("SELECT")]) == 2
    )
    statements.clear()
    assert store.has_unread_messages(session_id="session", channel_ids=[]) is False
    assert statements == []


def test_discord_unread_probe_preserves_short_circuit_before_invalid_cursor(tmp_path):
    store = DiscordInboxStore(tmp_path / "discord.db")
    store.add_messages_if_new(_messages(1, channel_id="unread"))
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO discord_inbox_session_reads VALUES ('session', 'broken', 'invalid')"
        )
    assert store.has_unread_messages(session_id="session", channel_ids=["unread", "broken"])
    with pytest.raises(ValueError):
        store.has_unread_messages(session_id="session", channel_ids=["broken", "unread"])


@pytest.mark.parametrize("field", ["session_id", "dispatch_id"])
def test_cost_snapshot_batches_deduplicated_identity_lookups(tmp_path, field):
    store = ModelUsageStore(tmp_path / "usage.db")
    for identity, value in (("first", "id-0"), ("last", "id-800"), ("private", "unrequested")):
        store.upsert_event(
            ModelInferenceUsageEvent(
                event_id=identity,
                occurred_at=datetime(2099, 1, 1, tzinfo=UTC),
                provider="openai",
                provider_endpoint_identifier="test",
                request_status="success",
                normalized_usage_status="available",
                **{field: value},
            )
        )
    with store.cost_report_snapshot() as snapshot:
        statements = []
        snapshot.connection.set_trace_callback(statements.append)
        values = [f"id-{index}" for index in range(801)]
        assert snapshot.event_ids_for(field=field, values=iter(values + values)) == {
            "first",
            "last",
        }
        assert len(statements) == 3
        statements.clear()
        assert snapshot.event_ids_for(field=field, values=[]) == set()
        assert statements == []
        with pytest.raises(ValueError, match="unsupported cost report lookup"):
            snapshot.event_ids_for(field="directory_path", values=values)
        assert statements == []
