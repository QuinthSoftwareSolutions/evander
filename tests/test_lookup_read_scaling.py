from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from evander.messaging.teams_inbox_store import TeamsInboxMessage, TeamsInboxStore
from evander.model_usage.models import ModelUsageCorrelationContext
from evander.model_usage.store import ModelUsageStore
from read_scaling_probe import ReadProbe

NOW = datetime(2026, 10, 9, 10, tzinfo=UTC)


def message(cursor, message_id, *, channel="channel", created=NOW):
    return TeamsInboxMessage(
        cursor=cursor,
        message_id=message_id,
        team_id="team",
        channel_id=channel,
        author_id="author",
        content=f"Synthetic {cursor}",
        created_at=created,
    )


def teams_store(root, history=2000):
    store = TeamsInboxStore(root / "teams.db")
    with store._connect() as db:
        db.executemany(
            "INSERT INTO teams_inbox_messages VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                store._message_row(
                    message(f"old-{i}", f"unrelated-{i}", created=NOW + timedelta(seconds=i + 1))
                )
                for i in range(history)
            ],
        )
    return store


@pytest.mark.parametrize("target, expected", [(None, "other-channel"), ("/team/channel/", "b")])
def test_teams_message_lookup_seeks_id_and_preserves_target_and_tie_order(
    tmp_path: Path, target, expected
):
    store = teams_store(tmp_path)
    for item in (
        message("a", "wanted"),
        message("b", "wanted"),
        message(
            "other-channel", "wanted", channel="other", created=NOW + timedelta(microseconds=1)
        ),
    ):
        store.add_message_if_new(item)
    with ReadProbe() as probe:
        found = store.find_message(" wanted ", channel_target=target)
    assert found.cursor == expected
    assert found.content == f"Synthetic {expected}"
    assert probe.rows["teams_inbox_messages"] == 1
    assert probe.vm_steps < 500
    assert store.find_message("wanted", channel_target="team/missing") is None
    assert store.find_message(" ") is None


def context(
    dispatch_id,
    *,
    started=NOW,
    expires=NOW + timedelta(hours=1),
    harness="codex",
    session="session-one",
):
    return ModelUsageCorrelationContext(
        dispatch_id=dispatch_id,
        session_id=session,
        agent_harness=harness,
        execution_backend="container_worker_vm",
        started_at=started,
        expires_at=expires,
    )


def usage_store(root, history=2000):
    store = ModelUsageStore(root / "usage.db")
    with store._connect() as db:
        db.executemany(
            "INSERT INTO model_usage_correlation_contexts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                store._context_row(
                    context(
                        f"old-{i}",
                        harness="codex" if i % 2 else "copilot",
                        started=NOW - timedelta(seconds=i + 1),
                    )
                )
                for i in range(history)
            ],
        )
    return store


def test_active_model_context_lookup_seeks_latest_session_without_harness_sort(tmp_path: Path):
    store = usage_store(tmp_path)
    for item in (
        context("a", harness="copilot"),
        context("b", harness="codex"),
        context("expired", started=NOW + timedelta(seconds=1), expires=NOW + timedelta(seconds=1)),
        context("other-session", session="session-other", started=NOW + timedelta(seconds=2)),
    ):
        store.upsert_correlation_context(item)
    with ReadProbe() as probe:
        found = store.get_active_correlation_context(
            session_id=" session-one ", now_utc=NOW + timedelta(seconds=1)
        )
    assert found.dispatch_id == "b"
    assert probe.payload_rows["model_usage_correlation_contexts"] == 1
    assert probe.vm_steps < 500
    assert (
        store.get_active_correlation_context(
            session_id="session-one", agent_harness="copilot", now_utc=NOW
        ).dispatch_id
        == "a"
    )
    assert (
        store.get_active_correlation_context(
            session_id="session-one", dispatch_id="b", now_utc=NOW
        ).dispatch_id
        == "b"
    )
    assert (
        store.get_active_correlation_context(
            session_id="session-one", execution_backend="libvirt_vm", now_utc=NOW
        )
        is None
    )
    assert (
        store.get_active_correlation_context(
            session_id="session-other", dispatch_id="b", now_utc=NOW
        )
        is None
    )
    assert (
        store.get_active_correlation_context(
            session_id="session-one", now_utc=NOW + timedelta(hours=2)
        )
        is None
    )


@pytest.mark.parametrize("kind", ["teams", "usage"])
def test_new_lookup_indexes_are_added_on_existing_database_without_data_loss(tmp_path: Path, kind):
    if kind == "teams":
        store = teams_store(tmp_path)
        store.add_message_if_new(message("wanted", "wanted"))
        indexes = (
            "idx_teams_inbox_messages_id_created",
            "idx_teams_inbox_messages_id_target_created",
        )

        def read(current):
            return current.find_message("wanted").cursor

        expected = "wanted"
        constructor = TeamsInboxStore
    else:
        store = usage_store(tmp_path)
        store.upsert_correlation_context(context("wanted"))
        indexes = ("idx_model_usage_contexts_session_started",)

        def read(current):
            return current.get_active_correlation_context(
                session_id="session-one", now_utc=NOW
            ).dispatch_id

        expected = "wanted"
        constructor = ModelUsageStore
    with store._connect() as db:
        for index in indexes:
            db.execute(f"DROP INDEX {index}")
    reopened = constructor(store.path)
    with ReadProbe() as probe:
        assert read(reopened) == expected
    assert probe.vm_steps < 500
