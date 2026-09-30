from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from evander_team_addon.case_context_models import CaseContextSnapshot, CaseContextView
from evander_team_addon.code_index import protected_bindings, session_bindings
from evander_team_addon.models import TeamIdempotencyRecord, TeamOutboxRecord
from evander_team_addon.provider_completion import TeamAgentPromptCompletionFinalizeHook
from evander_team_addon.repository import TeamStateConflictError, TeamStateRepository
from evander_team_addon.services.chat_service import TeamChatService
from evander_team_addon.sqlite_store import TeamStateSqliteStore
from team_repository_support import _seed_hierarchy


def snapshot(snapshot_id="snapshot", **overrides):
    return CaseContextSnapshot.model_validate(
        {
            "id": snapshot_id,
            "request_id": snapshot_id,
            "request_digest": "request-digest",
            "directory_path": "root/a",
            "team_ids": ["team"],
            "case_ids": ["case"],
            "dispatch_id": "dispatch",
            "recipient_session_id": "recipient",
            "created_by_agent_id": "worker",
            "source_revision": 4,
            "view": CaseContextView(
                id="view", view_id="task-brief", title="Brief", sections=[{"name": "request"}]
            ),
            "content_digest": "evidence-digest",
            "context": {"text": "Frozen brief"},
            "evidence": {"request": [{"body": "Frozen evidence"}]},
            "created_at": datetime(2026, 1, 1, tzinfo=UTC),
            **overrides,
        }
    )


def test_archive_migration_and_older_writer_recovery_preserve_payloads(tmp_path):
    repo = TeamStateRepository(tmp_path)
    payloads = {f"s-{i:03}": snapshot(f"s-{i:03}").model_dump_json() for i in range(260)}
    with sqlite3.connect(repo.store.path) as conn:
        conn.executemany("INSERT INTO team_case_context_snapshots VALUES (?, ?)", payloads.items())
        for trigger in ("insert", "update", "delete"):
            conn.execute(f"DROP TRIGGER team_context_snapshot_{trigger}")
        for table in ("index", "cases", "pending"):
            conn.execute(f"DROP TABLE team_case_context_snapshot_{table}")
        conn.execute(
            "UPDATE team_state_metadata SET value = '80' WHERE key = 'sqlite_schema_version'"
        )

    migrated = TeamStateRepository(tmp_path)
    assert migrated.snapshots.ids_for_dispatch("dispatch") == sorted(payloads)
    assert migrated.snapshots.get("s-000").recipient_session_id == "recipient"
    assert migrated.snapshots.get("s-000").content_digest == "evidence-digest"
    with sqlite3.connect(repo.store.path) as conn:
        assert (
            dict(conn.execute("SELECT id, payload_json FROM team_case_context_snapshots"))
            == payloads
        )
        # An older binary can add or rewrite rows after rollback without knowing the index.
        updated = snapshot(
            "s-000", case_ids=["moved"], dispatch_id="older-dispatch"
        ).model_dump_json()
        conn.execute(
            "UPDATE team_case_context_snapshots SET payload_json = ? WHERE id = 's-000'", (updated,)
        )
        conn.execute(
            "INSERT INTO team_case_context_snapshots VALUES (?, ?)",
            ("older", snapshot("older", dispatch_id="older-dispatch").model_dump_json()),
        )
        conn.execute("DELETE FROM team_case_context_snapshots WHERE id = 's-001'")
    recovered = TeamStateRepository(tmp_path)
    assert recovered.snapshots.ids_for_dispatch("older-dispatch") == ["older", "s-000"]
    assert (
        recovered.snapshots.list_for_case("moved", directory_path="root")["records"][0]["id"]
        == "s-000"
    )
    assert recovered.snapshots.get("s-001") is None
    assert "s-000" not in recovered.snapshots.ids_for_dispatch("dispatch")
    with sqlite3.connect(repo.store.path) as conn:
        assert (
            conn.execute("SELECT count(*) FROM team_case_context_snapshot_pending").fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT payload_json FROM team_case_context_snapshots WHERE id = 's-000'"
            ).fetchone()[0]
            == updated
        )


def test_history_pages_preserve_scope_time_order_and_do_not_load_payloads(tmp_path, monkeypatch):
    repo = TeamStateRepository(tmp_path)
    instant = datetime(2026, 1, 1, tzinfo=UTC)

    def stage(state):
        for index in range(23):
            repo.snapshots.stage(
                state, snapshot(f"s-{index:02}", created_at=instant + timedelta(microseconds=index))
            )
        repo.snapshots.stage(state, snapshot("out-of-scope", directory_path="root/ab"))
        repo.snapshots.stage(state, snapshot("other-case", case_ids=["other"]))
        repo.snapshots.stage(
            state, snapshot("offset", created_at="2026-01-01T01:00:00.000023+01:00")
        )

    repo.update(stage)
    original = repo.store._read_connection

    def guarded():
        conn = original()
        conn.set_authorizer(
            lambda action, table, *_: (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_READ and table == "team_case_context_snapshots"
                else sqlite3.SQLITE_OK
            )
        )
        return conn

    monkeypatch.setattr(repo.store, "_read_connection", guarded)
    first = repo.snapshots.list_for_case("case", directory_path="root/a")
    assert [r["id"] for r in first["records"]] == ["offset"] + [
        f"s-{i:02}" for i in range(22, 3, -1)
    ]
    assert first["next_cursor"] == 20
    last = repo.snapshots.list_for_case("case", directory_path="root/a", cursor=20)
    assert [r["id"] for r in last["records"]] == ["s-03", "s-02", "s-01", "s-00"]
    assert last["next_cursor"] is None


@pytest.mark.parametrize("prepared", [False, True])
def test_snapshot_state_journal_and_outbox_commit_or_rollback_together(tmp_path, prepared):
    repo = _seed_hierarchy(tmp_path)
    initial = repo.read()
    captured = snapshot()

    def command(tx):
        tx.store_idempotency_record(
            TeamIdempotencyRecord(
                id="command",
                tool_name="capture",
                caller_identity="worker",
                scope_kind="case",
                scope_id="case",
                request_id="request",
                normalized_payload_digest="digest",
                created_domain_ids=[captured.id],
            )
        )
        tx.enqueue_outbox_record(
            TeamOutboxRecord(
                id="event", intent_type="capture", payload={"snapshot_id": captured.id}
            )
        )

    def mutate(state):
        state.teams["team"].name = "Captured"
        repo.snapshots.stage(state, captured)

    def commit():
        if prepared:
            state = repo.read()
            baseline = state.model_dump(mode="json")
            mutate(state)
            return repo.commit_prepared_state_with_command_transaction(
                previous_payload=baseline, candidate=state, command_writer=command
            )

        def transaction(state, tx):
            mutate(state)
            command(tx)

        return repo.update_with_command_transaction(transaction)

    with sqlite3.connect(repo.store.path) as conn:
        conn.execute(
            "CREATE TRIGGER reject_history BEFORE INSERT ON team_case_context_snapshot_index "
            "BEGIN SELECT RAISE(ABORT, 'history failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="history failure"):
        commit()
    assert repo.read() == initial
    assert repo.snapshots.get(captured.id) is None
    assert repo.list_outbox_records() == []
    assert (
        repo.get_idempotency_record(
            tool_name="capture",
            caller_identity="worker",
            scope_kind="case",
            scope_id="case",
            request_id="request",
        )
        is None
    )
    with sqlite3.connect(repo.store.path) as conn:
        conn.execute("DROP TRIGGER reject_history")
    saved = commit()
    assert saved.revision == initial.revision + 1
    assert saved.teams["team"].name == "Captured"
    assert not saved._case_context_snapshot_writes
    assert repo.snapshots.get(captured.id) == captured
    assert repo.list_outbox_records()[0].payload["snapshot_id"] == captured.id
    assert repo.get_idempotency_record(
        tool_name="capture",
        caller_identity="worker",
        scope_kind="case",
        scope_id="case",
        request_id="request",
    ).created_domain_ids == [captured.id]


def test_stale_preparation_discards_staged_history_and_replan_commits_once(tmp_path):
    repo = _seed_hierarchy(tmp_path)
    candidate = repo.read()
    baseline = candidate.model_dump(mode="json")
    repo.snapshots.stage(candidate, snapshot())
    repo.update(lambda state: setattr(state.teams["team"], "name", "Concurrent update"))
    with pytest.raises(TeamStateConflictError):
        repo.commit_prepared_state_with_command_transaction(
            previous_payload=baseline,
            candidate=candidate,
            command_writer=lambda _tx: pytest.fail("stale command ran"),
        )
    assert repo.snapshots.get("snapshot") is None
    assert not repo.read()._case_context_snapshot_writes
    committed = repo.update_prepared(lambda state: repo.snapshots.stage(state, snapshot()))
    assert repo.snapshots.ids_for_dispatch("dispatch") == ["snapshot"]
    assert committed.teams["team"].name == "Concurrent update"
    assert repo.update(lambda state: None).revision == committed.revision


def test_operational_reads_mutations_wake_completion_and_bindings_never_read_archive(
    tmp_path, monkeypatch
):
    repo = _seed_hierarchy(tmp_path)
    repo.update(lambda state: repo.snapshots.stage(state, snapshot()))
    for name in ("_read_connection", "_connect"):
        original = getattr(TeamStateSqliteStore, name)

        def guarded(self, original=original):
            conn = original(self)
            conn.set_authorizer(
                lambda action, table, *_: (
                    sqlite3.SQLITE_DENY
                    if action == sqlite3.SQLITE_READ and table == "team_case_context_snapshots"
                    else sqlite3.SQLITE_OK
                )
            )
            return conn

        monkeypatch.setattr(TeamStateSqliteStore, name, guarded)
    state = repo.read()
    assert "case_context_snapshots" not in state.model_dump(mode="json")
    repo.update(lambda s: setattr(s.teams["team"], "name", "Operational change"))
    assert repo.read_projection(collections=["teams"]).teams["team"].name == "Operational change"
    _, claims = TeamChatService(repo).claim_pending_chat_message_dispatches()
    assert claims == []
    worker = SimpleNamespace(worker_config=SimpleNamespace(runtime_dir=tmp_path))
    assert (
        TeamAgentPromptCompletionFinalizeHook()._reconcile_selected_completion(
            worker=worker, repo=repo, dispatch_ids=set(), records=[]
        )
        == 0
    )
    assert session_bindings(tmp_path, "session-worker") == set()
    assert protected_bindings(tmp_path) == set()
    with pytest.raises(ValueError, match="Unknown"):
        repo.read_projection(collections=["case_context_snapshots"])


def test_operational_replacement_preserves_archive_and_legacy_import_is_atomic(tmp_path):
    repo = _seed_hierarchy(tmp_path)
    captured = snapshot()
    repo.update(lambda state: repo.snapshots.stage(state, captured))
    payload = repo.store.read_object()
    payload["teams"]["team"]["name"] = "Replaced operational state"
    repo.store.write_object(payload)
    assert repo.snapshots.get(captured.id) == captured
    payload["teams"]["team"]["name"] = "Invalid replacement"
    payload["case_context_snapshots"] = {
        captured.id: captured.model_copy(update={"content_digest": "changed"}).model_dump(
            mode="json"
        )
    }
    with pytest.raises(ValueError, match="immutable"):
        repo.store.write_object(payload)
    assert repo.read().teams["team"].name == "Replaced operational state"
    assert repo.snapshots.get(captured.id) == captured
    imported = snapshot("imported", recipient_session_id="original-recipient")
    payload["case_context_snapshots"] = {imported.id: imported.model_dump(mode="json")}
    repo.store.write_object(payload)
    assert repo.snapshots.get(imported.id).recipient_session_id == "original-recipient"
    assert repo.snapshots.ids_for_dispatch("dispatch") == ["imported", "snapshot"]


def test_team_configuration_copy_keeps_original_history_without_copying_grants(tmp_path):
    from evander_team_addon.services.admin_service import TeamAdminService

    repo = _seed_hierarchy(tmp_path)
    captured = snapshot()
    source = repo.update(lambda state: repo.snapshots.stage(state, captured))
    _, result = TeamAdminService(repo).copy_team(
        source_team_id="team",
        team_id="copy",
        name="Copied team",
        agent_session_ids={
            agent.id: f"copy-{agent.session_id}" for agent in source.agents.values()
        },
        expected_revision=source.revision,
    )
    assert result["team_id"] == "copy"
    assert repo.snapshots.ids_for_dispatch("dispatch") == [captured.id]
    assert repo.snapshots.get(captured.id).recipient_session_id == "recipient"
    with sqlite3.connect(repo.store.path) as connection:
        assert (
            connection.execute("SELECT count(*) FROM team_case_context_snapshots").fetchone()[0]
            == 1
        )


def test_baseline_selection_uses_completion_time_then_snapshot_id_without_evidence(
    tmp_path, monkeypatch
):
    repo = TeamStateRepository(tmp_path)
    now = datetime(2026, 1, 2, tzinfo=UTC)

    def stage(state):
        repo.snapshots.stage(state, snapshot("older-z", dispatch_id="older", created_at=now))
        repo.snapshots.stage(state, snapshot("newer-a", dispatch_id="newer"))
        repo.snapshots.stage(state, snapshot("newer-z", dispatch_id="newer"))
        repo.snapshots.stage(state, snapshot("unrelated-z", dispatch_id="unrelated"))

    state = repo.update(stage)
    original = repo.store._read_connection

    def guarded():
        connection = original()
        connection.set_authorizer(
            lambda action, table, *_: (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_READ and table == "team_case_context_snapshots"
                else sqlite3.SQLITE_OK
            )
        )
        return connection

    monkeypatch.setattr(repo.store, "_read_connection", guarded)
    assert (
        repo.snapshots.previous_id({"older": now - timedelta(hours=1), "newer": now}, state=state)
        == "newer-z"
    )
    repo.snapshots.stage(state, snapshot("staged", dispatch_id="current"))
    assert (
        repo.snapshots.previous_id({"newer": now, "current": now + timedelta(hours=1)}, state=state)
        == "staged"
    )
