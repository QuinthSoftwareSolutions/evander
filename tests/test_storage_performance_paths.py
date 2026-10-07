from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest

from evander.code_index.models import IndexSettings
from evander.code_index.store import IndexStore
from evander.messaging.teams_inbox_store import TeamsInboxStore, TeamsNotificationQueueEntry
from evander.model_usage.store import ModelUsageStore


def test_attribution_updates_read_only_requested_sessions_and_dirty_affected_teams(
    tmp_path, monkeypatch
):
    store = ModelUsageStore(tmp_path / "usage.db")
    with store._connect() as connection:
        connection.executemany(
            "INSERT INTO model_usage_cost_attributions VALUES (?, ?, NULL, NULL, 'stamp')",
            [(f"unrelated-{i}", "unrelated-team") for i in range(3000)]
            + [("changed-session", "old-team")],
        )
        connection.executemany(
            "INSERT INTO model_usage_cost_balances VALUES "
            "('team', ?, 'window', 'start', NULL, 'fingerprint', '{}', '{}', "
            "NULL, 'stamp', 'stamp')",
            [("old-team",), ("new-team",), ("unrelated-team",)],
        )

    original_connect = store._connect
    read_sessions = []

    @contextmanager
    def measured_connect():
        with original_connect() as connection:
            connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)

            def row_factory(cursor, row):
                if tuple(column[0] for column in cursor.description) == (
                    "session_id",
                    "team_id",
                    "project_id",
                    "agent_id",
                ):
                    read_sessions.append(row[0])
                return sqlite3.Row(cursor, row)

            connection.row_factory = row_factory
            yield connection

    monkeypatch.setattr(store, "_connect", measured_connect)
    assert (
        store.upsert_cost_attributions(
            [
                {"session_id": "changed-session", "team_id": "discarded-team"},
                {"session_id": "changed-session", "team_id": "new-team"},
            ]
        )
        == 1
    )
    assert read_sessions == ["changed-session"]
    with original_connect() as connection:
        teams = dict(
            connection.execute("SELECT dimension_id, dirty_reason FROM model_usage_cost_balances")
        )
        assert teams == {
            "old-team": "session_attribution_changed",
            "new-team": "session_attribution_changed",
            "unrelated-team": None,
        }
        assert (
            connection.execute(
                "SELECT team_id FROM model_usage_cost_attributions WHERE session_id='changed-session'"
            ).fetchone()[0]
            == "new-team"
        )

    # A reconciliation can exceed SQLite's older parameter limit without losing rows.
    large_batch = [{"session_id": f"unrelated-{i}", "team_id": "new-team"} for i in range(1100)]
    read_sessions.clear()
    assert store.upsert_cost_attributions(large_batch) == 1100
    assert len(read_sessions) == 1100
    assert set(read_sessions) == {f"unrelated-{i}" for i in range(1100)}
    assert store.upsert_cost_attributions(large_batch) == 0


@pytest.mark.parametrize("active_count,age_seconds", [(1200, 1800), (0, None), (1, 0)])
def test_notification_status_aggregates_queue_without_reading_payloads(
    tmp_path, monkeypatch, active_count, age_seconds
):
    store = TeamsInboxStore(tmp_path / "teams.db")
    now = datetime(2026, 10, 1, 12, tzinfo=UTC)
    oldest = now - timedelta(minutes=30) if active_count > 1 else now + timedelta(minutes=1)
    states = ("pending", "retry", "leased")
    rows = [
        store._notification_queue_row(
            TeamsNotificationQueueEntry(
                idempotency_key=f"active-{i}",
                notification_kind="message",
                subscription_id="subscription",
                tenant_id="tenant",
                resource="resource",
                state=states[i % 3],
                received_at=oldest + timedelta(seconds=i),
                next_attempt_at=now,
            )
        )
        for i in range(active_count)
    ]
    for state, received_at in (
        ("completed", now - timedelta(days=1)),
        ("failed", now + timedelta(hours=2)),
    ):
        rows.append(
            store._notification_queue_row(
                TeamsNotificationQueueEntry(
                    idempotency_key=state,
                    notification_kind="message",
                    subscription_id="subscription",
                    tenant_id="tenant",
                    resource="resource",
                    state=state,
                    received_at=received_at,
                    next_attempt_at=now,
                )
            )
        )
    with store._connect() as connection:
        connection.executemany(
            "INSERT INTO teams_inbox_notification_queue VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    original_connect = store._connect
    plans = []

    def measured_connect():
        connection = original_connect()

        def authorize(action, table, column, *_):
            if (
                action == sqlite3.SQLITE_READ
                and table == "teams_inbox_notification_queue"
                and column == "payload_json"
            ):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorize)
        for sql in (
            "SELECT COUNT(*), MIN(received_at) FROM teams_inbox_notification_queue "
            "WHERE state IN ('pending', 'retry', 'leased')",
            "SELECT MAX(received_at) FROM teams_inbox_notification_queue",
        ):
            plans.append([row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + sql)])
        return connection

    monkeypatch.setattr(store, "_connect", measured_connect)
    status = store.notification_status(enabled=True, renewal_margin=timedelta(hours=1), now=now)
    assert status["queue_depth"] == active_count
    assert status["oldest_queue_age_seconds"] == age_seconds
    assert datetime.fromisoformat(status["last_notification_at"]) == now + timedelta(hours=2)
    assert all(any("COVERING INDEX" in detail for detail in plan) for plan in plans)


@pytest.mark.parametrize("missing_hashes", [0, 1100])
def test_chunk_hits_bounds_materialization_and_preserves_hash_rank_and_snapshot_scope(
    tmp_path, monkeypatch, missing_hashes
):
    store = IndexStore(tmp_path)
    build = store.enqueue("repo", "root", "a" * 40, IndexSettings(), {})
    other = store.enqueue("repo", "root", "b" * 40, IndexSettings(), {})
    with store.connect(write=True) as connection:
        connection.executemany(
            "INSERT INTO contents VALUES (?, ?)",
            [("high", "high text"), ("low", "low text"), ("last", "last text")],
        )
        connection.executemany(
            "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?)",
            [(f"low-{i}", build["id"], f"low/{i:04}.py", 1, 1, "low") for i in range(2500)]
            + [
                ("high-b", build["id"], "high/b.py", 1, 1, "high"),
                ("high-a2", build["id"], "high/a.py", 2, 2, "high"),
                ("high-a1", build["id"], "high/a.py", 1, 1, "high"),
                ("last", build["id"], "last.py", 1, 1, "last"),
                ("other", other["id"], "0-first.py", 1, 1, "high"),
            ],
        )
    original_connect = store.connect
    hydrated = []
    loaded_text = []

    @contextmanager
    def measured_connect(**kwargs):
        with original_connect(**kwargs) as connection:
            connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)

            def count_content_read(value):
                loaded_text.append(value)
                return value

            connection.create_function("count_content_read", 1, count_content_read)
            connection.execute(
                "CREATE TEMP VIEW contents AS "
                "SELECT hash, count_content_read(text) AS text FROM main.contents"
            )

            def row_factory(cursor, row):
                if "text" in {column[0] for column in cursor.description}:
                    hydrated.append(row[0])
                return sqlite3.Row(cursor, row)

            connection.row_factory = row_factory
            yield connection

    monkeypatch.setattr(store, "connect", measured_connect)
    # Repeated hashes keep their last supplied rank; missing hashes consume no slots.
    hashes = ["low", *[f"missing-{i}" for i in range(missing_hashes)], "high", "low", "last"]
    hits = store.chunk_hits(build["id"], hashes)
    assert [hit["id"] for hit in hits[:3]] == ["high-a1", "high-a2", "high-b"]
    assert [hit["id"] for hit in hits[3:]] == [f"low-{i}" for i in range(997)]
    assert len(hydrated) == len(hits) == 1000
    assert len(loaded_text) == 1000
    assert all(hit["snapshot"] == build["id"] for hit in hits)
    assert hits[0]["text"] == "high text"
    assert store.chunk_hits(build["id"], []) == []
