"""Recovery must scale with its working records, not retained dispatch history."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from evander.config import GatewayConfig
from evander_team_addon.models import TeamDispatchActivity
from evander_team_addon.provider_completion import TeamAgentPromptCompletionFinalizeHook
from evander_team_addon.repository import TeamStateConflictError
from evander_team_addon.services.runtime_service import TeamRuntimeService
from evander_team_addon.sqlite_store import TeamStateSqliteStore
from team_runtime_support import _seed_team


def _history(repo, count):
    with sqlite3.connect(repo.store.path) as connection:
        connection.executemany(
            """INSERT INTO team_dispatches
            (id,agent_id,session_id,cause_kind,status,created_at,activation_completed_at,
             completed_at,native_thread_observed_at)
            VALUES (?,'backend','session-backend','scheduled_wake','completed',
              '2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00',
              '2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')""",
            ((f"history-{i:06d}",) for i in range(count)),
        )
        connection.execute("ANALYZE")


@pytest.mark.parametrize("history_count", [5000, 50000])
def test_actual_history_candidate_pages_use_bounded_index_ranges(
    tmp_path, monkeypatch, history_count
):
    repo = _seed_team(tmp_path)
    _history(repo, history_count)
    connect = repo.store._read_connection
    statements, instruction_counts = [], []

    def bounded_connection():
        connection = connect()
        count = [0]
        instruction_counts.append(count)

        def progress():
            count[0] += 100
            return int(count[0] > 50000)

        connection.set_progress_handler(progress, 100)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(repo.store, "_read_connection", bounded_connection)
    first = repo.lifecycle_candidates(kind="history", limit=50)
    assert [item.dispatch_id for item in first.candidates] == [
        f"history-{i:06d}" for i in range(50)
    ]
    second = repo.lifecycle_candidates(kind="history", after=first.next_cursor, limit=50)
    assert [item.dispatch_id for item in second.candidates] == [
        f"history-{i:06d}" for i in range(50, 100)
    ]
    assert max(count[0] for count in instruction_counts) < 50000
    with sqlite3.connect(repo.store.path) as connection:
        for statement in statements:
            if "WITH ids AS" not in statement:
                continue
            plan = [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + statement)]
            assert any(
                "SEARCH team_dispatches USING INDEX idx_team_dispatches_receipt_recovery (id>?)"
                in item
                for item in plan
            )
            assert not any("TEMP B-TREE" in item for item in plan)


@pytest.mark.parametrize("history_count", [1000, 10000])
def test_real_completion_hook_activates_and_finishes_without_rebuilding_state(
    tmp_path, monkeypatch, history_count
):
    repo = _seed_team(tmp_path)
    _, leases = TeamRuntimeService(repo).lease_due_agents(agent_id="backend", defer_activation=True)
    dispatch_id = leases[0]["dispatch_id"]
    _history(repo, history_count)
    record = SimpleNamespace(
        execution_id="delivered",
        effect_type="effect:agent:enqueue-prompt",
        status="succeeded",
        finished_at=datetime.now(UTC),
        outputs={"dispatch_id": dispatch_id, "session_id": "session-backend", "queued": False},
    )
    worker = SimpleNamespace(
        config=GatewayConfig(runtime_dir=tmp_path),
        execution_store=SimpleNamespace(
            list_prompt_records_for_dispatches=lambda ids: [record] if dispatch_id in ids else []
        ),
    )
    decoded = []
    read_dispatches = TeamStateSqliteStore._read_dispatches

    def count_dispatches(connection, payload, **kwargs):
        read_dispatches(connection, payload, **kwargs)
        decoded.append(len(payload["dispatches"]))

    with monkeypatch.context() as patch:
        patch.setattr(TeamStateSqliteStore, "_read_dispatches", staticmethod(count_dispatches))
        patch.setattr(
            TeamStateSqliteStore, "_read_state", lambda *_a, **_k: pytest.fail("full state read")
        )
        patch.setattr(
            TeamStateSqliteStore,
            "_read_state_payload",
            lambda *_a, **_k: pytest.fail("full collection read"),
        )
        hook = TeamAgentPromptCompletionFinalizeHook()
        assert hook.on_post_completion_dispatch(worker=worker) == 2
        assert sum(decoded) < 20
        revision = repo.read_revision()
        assert hook.on_post_completion_dispatch(worker=worker) == 0
        assert repo.read_revision() == revision
    state = repo.read()
    assert state.dispatches[dispatch_id].status == "completed"
    assert state.agent_runtimes["backend"].active_dispatch_id is None
    assert state.agent_runtimes["backend"].no_progress_count == 1
    assert len(state.dispatches) == history_count + 1
    assert (
        len(
            repo.list_dispatch_activity(dispatch_id=dispatch_id, tool_name="team_dispatch_finished")
        )
        == 1
    )


@pytest.mark.parametrize("conflict", ["revision", "activity"])
def test_scoped_finalization_rejects_concurrent_evidence_and_recovers_once(
    tmp_path, monkeypatch, conflict
):
    from evander_team_addon.lifecycle_scope import TeamLifecycleRepository
    from evander_team_addon.services.prepared_finalization import DispatchActivityConflictError

    repo = _seed_team(tmp_path)
    _, leases = TeamRuntimeService(repo).lease_due_agents(agent_id="backend")
    dispatch_id = leases[0]["dispatch_id"]
    scoped = TeamLifecycleRepository(repo, dispatch_ids=[dispatch_id])
    commit = scoped.commit_prepared_state_with_command_transaction
    injected = False

    def concurrent_change(**kwargs):
        nonlocal injected
        if not injected:
            injected = True
            if conflict == "revision":
                repo.update(lambda state: setattr(state.projects["project"], "name", "New name"))
            else:
                repo.append_dispatch_activity(
                    TeamDispatchActivity(
                        id="late",
                        tool_name="team_report_progress",
                        caller_session_id="session-backend",
                        caller_identity="agent:backend",
                        agent_id="backend",
                        dispatch_id=dispatch_id,
                        status="success",
                        progress_classification="progress",
                    )
                )
        return commit(**kwargs)

    monkeypatch.setattr(scoped, "commit_prepared_state_with_command_transaction", concurrent_change)
    with pytest.raises(
        TeamStateConflictError if conflict == "revision" else DispatchActivityConflictError
    ):
        TeamRuntimeService(scoped).finalize_agent_dispatch_from_activity(
            session_id="session-backend",
            dispatch_id=dispatch_id,
        )
    assert repo.read().dispatches[dispatch_id].status == "leased"
    assert not repo.list_dispatch_activity(
        dispatch_id=dispatch_id, tool_name="team_dispatch_finished"
    )
    _, result = TeamRuntimeService(scoped).finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id=dispatch_id,
    )
    assert result["finalized"]
    assert ("late" in result["activity_ids"]) is (conflict == "activity")
    assert (
        len(
            repo.list_dispatch_activity(dispatch_id=dispatch_id, tool_name="team_dispatch_finished")
        )
        == 1
    )


def test_index_only_upgrade_preserves_records_revision_and_statistics(tmp_path, monkeypatch):
    from evander_team_addon.sqlite_schema import migrate_schema
    from evander_team_addon.sqlite_schema_sql import SQLITE_SCHEMA_VERSION

    repo = _seed_team(tmp_path)
    with sqlite3.connect(repo.store.path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("ANALYZE")
        statistics = [
            tuple(row) for row in connection.execute("SELECT * FROM sqlite_stat1 ORDER BY tbl,idx")
        ]
        before = [tuple(row) for row in connection.execute("SELECT * FROM team_tasks ORDER BY id")]
        revision = connection.execute(
            "SELECT value FROM team_state_metadata WHERE key='revision'"
        ).fetchone()[0]
        connection.execute("DROP INDEX idx_team_dispatches_terminal_time")
        connection.execute(
            "UPDATE team_state_metadata SET value='82' WHERE key='sqlite_schema_version'"
        )
        migrate_schema(
            connection,
            read_payload=lambda *_a: pytest.fail("index upgrade reconstructed state"),
            write_state=lambda *_a: pytest.fail("index upgrade rewrote state"),
        )
        assert [
            tuple(row) for row in connection.execute("SELECT * FROM team_tasks ORDER BY id")
        ] == before
        # Dropping the one index removes only its own statistics row.
        assert [
            tuple(row) for row in connection.execute("SELECT * FROM sqlite_stat1 ORDER BY tbl,idx")
        ] == [row for row in statistics if row[1] != "idx_team_dispatches_terminal_time"]
        assert (
            connection.execute(
                "SELECT value FROM team_state_metadata WHERE key='revision'"
            ).fetchone()[0]
            == revision
        )
        assert connection.execute(
            "SELECT value FROM team_state_metadata WHERE key='sqlite_schema_version'"
        ).fetchone()[0] == str(SQLITE_SCHEMA_VERSION)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='idx_team_dispatches_terminal_time'"
        ).fetchone()


def test_lazy_reference_reads_reject_changed_ownership_revision(tmp_path):
    from evander_team_addon.lifecycle_scope import TeamLifecycleRepository

    repo = _seed_team(tmp_path)
    scope = TeamLifecycleRepository(repo).read()
    repo.update(lambda state: setattr(state.agents["backend"], "session_id", "replacement"))
    with pytest.raises(TeamStateConflictError):
        scope.agents["backend"]


def test_command_snapshot_stays_coherent_while_a_writer_commits(tmp_path):
    from evander_team_addon.lifecycle_scope import TeamLifecycleRepository

    repo = _seed_team(tmp_path)
    scoped = TeamLifecycleRepository(repo)
    with scoped.command_snapshot() as snapshot:
        repo.update(lambda state: setattr(state.agents["backend"], "session_id", "replacement"))
        assert snapshot.agents["backend"].session_id == "session-backend"
        with pytest.raises(TeamStateConflictError):
            scoped.commit_prepared_state_with_command_transaction(
                previous_payload={"revision": snapshot.revision},
                candidate=snapshot,
                command_writer=lambda _tx: None,
            )
    assert repo.read().agents["backend"].session_id == "replacement"


@pytest.mark.parametrize("correction", ["candidate", "human"])
def test_correction_selectors_retry_a_concurrent_revision_change(tmp_path, monkeypatch, correction):
    from evander_team_addon.services import candidate_correction_reconciliation_service as candidate
    from evander_team_addon.services import human_correction_reconciliation_service as human

    repo = _seed_team(tmp_path)
    module, selector, service = (
        (
            candidate,
            "_candidate_release_ids_for_reconciliation",
            candidate.CandidateCorrectionReconciliationService,
        )
        if correction == "candidate"
        else (human, "_human_correction_cases", human.HumanCorrectionReconciliationService)
    )
    original = getattr(module, selector)
    calls = []

    def concurrent_writer(state):
        calls.append(state.revision)
        if len(calls) == 1:
            repo.update(lambda latest: setattr(latest.projects["project"], "name", "New name"))
        return original(state)

    monkeypatch.setattr(module, selector, concurrent_writer)
    result = service(repo).reconcile()
    assert result == {"changed": False, "revision": calls[0] + 1, "events": []}
    assert calls == [calls[0], calls[0] + 1]


def test_completion_reuses_the_superiors_existing_blocker_inbox(tmp_path):
    from evander_team_addon.lifecycle_scope import TeamLifecycleRepository
    from evander_team_addon.models import AgentInboxItem, TeamBlocker

    repo = _seed_team(tmp_path)
    _, leases = TeamRuntimeService(repo).lease_due_agents(agent_id="backend")
    dispatch_id = leases[0]["dispatch_id"]

    def seed(state):
        state.blockers["blocker"] = TeamBlocker(
            id="blocker",
            team_id="team",
            project_id="project",
            task_id="task",
            source_kind="explicit",
            source_id="work",
            owner_agent_id="backend",
            route="owned",
            state="open",
            summary="Work blocked",
            details="Needs supervisor",
            active_dispatch_id=dispatch_id,
        )
        state.dispatches[dispatch_id].blocker_ids = ["blocker"]
        state.inbox["superior-inbox"] = AgentInboxItem(
            id="superior-inbox",
            recipient_agent_id="lead",
            task_id="task",
            item_type="status_request",
            payload={"blocker_id": "blocker", "kind": "blocker_escalated"},
        )

    repo.update(seed)
    scoped = TeamLifecycleRepository(repo, dispatch_ids=[dispatch_id])
    TeamRuntimeService(scoped).finalize_agent_dispatch_from_activity(
        session_id="session-backend",
        dispatch_id=dispatch_id,
    )
    state = repo.read()
    assert state.blockers["blocker"].owner_agent_id == "lead"
    matching = [
        item.id
        for item in state.inbox.values()
        if item.recipient_agent_id == "lead"
        and item.state == "pending"
        and item.payload.get("blocker_id") == "blocker"
    ]
    assert matching == ["superior-inbox"]


def test_retained_thread_context_uses_headers_and_preserves_staged_tie_order(tmp_path, monkeypatch):
    from evander_team_addon.lifecycle_scope import TeamLifecycleRepository
    from evander_team_addon.models import AgentContextClearingPolicy, TeamDispatch
    from evander_team_addon.services.case_context_dispatch import previous_context_snapshot
    from test_team_snapshot_history import snapshot

    repo = _seed_team(tmp_path)
    _history(repo, 10000)
    instant = datetime(2026, 1, 2, tzinfo=UTC)

    def seed(state):
        runtime = state.agent_runtimes["backend"]
        runtime.last_thread_id = "retained"
        runtime.context_clearing_policy = AgentContextClearingPolicy.OFF
        for key in ("first", "tie", "current"):
            state.dispatches[key] = TeamDispatch(
                id=key,
                agent_id="backend",
                session_id="session-backend",
                cause_kind="scheduled_wake",
                status="completed" if key != "current" else "leased",
                created_at=instant,
                completed_at=instant if key != "current" else None,
                native_thread_id="retained",
                leased_at=instant,
                lease_expires_at=datetime(2027, 1, 1, tzinfo=UTC),
            )
        repo.snapshots.stage(state, snapshot("a-baseline", dispatch_id="first"))
        repo.snapshots.stage(state, snapshot("z-baseline", dispatch_id="tie"))

    repo.update(seed)
    decoded = []
    read_dispatches = TeamStateSqliteStore._read_dispatches

    def count_dispatches(connection, payload, **kwargs):
        read_dispatches(connection, payload, **kwargs)
        decoded.extend(payload["dispatches"])

    monkeypatch.setattr(TeamStateSqliteStore, "_read_dispatches", staticmethod(count_dispatches))
    scoped = TeamLifecycleRepository(repo, dispatch_ids=["current"])
    with scoped.command_snapshot() as state:
        current = state.dispatches["current"]
        assert previous_context_snapshot(scoped, state, current) == "z-baseline"
        assert decoded == ["current"]
        repo.snapshots.stage(state, snapshot("zz-staged", dispatch_id="first"))
        assert previous_context_snapshot(scoped, state, current) == "zz-staged"


@pytest.mark.parametrize(
    "older,newer",
    [
        ("2026-01-01T00:00:00.999900+00:00", "2026-01-01T00:00:01.000100+00:00"),
        ("2026-01-01T07:00:00.123400+07:00", "2026-01-01T00:00:00.123450+00:00"),
    ],
)
def test_scoped_latest_attempt_preserves_exact_datetime_order(tmp_path, older, newer):
    from evander_team_addon.lifecycle_scope import TeamLifecycleRepository
    from evander_team_addon.models import TeamDispatch
    from evander_team_addon.services.agent_leasing import (
        _latest_terminal_dispatch_for_agent,
        _latest_delivered_wake_work_attempt_for_agent,
    )

    repo = _seed_team(tmp_path)

    def seed(state):
        for key, timestamp in (("z-older", older), ("a-newer", newer)):
            instant = datetime.fromisoformat(timestamp)
            state.dispatches[key] = TeamDispatch(
                id=key,
                agent_id="backend",
                session_id="session-backend",
                cause_kind="scheduled_wake",
                status="completed",
                created_at=instant,
                leased_at=instant,
                completed_at=instant,
                activation_completed_at=instant,
                wake_work_fingerprint="same-work",
            )

    repo.update(seed)
    scope = TeamLifecycleRepository(repo).read()
    assert _latest_terminal_dispatch_for_agent(scope, agent_id="backend").id == "a-newer"
    assert _latest_delivered_wake_work_attempt_for_agent(scope, agent_id="backend").id == "a-newer"
