"""Recovery must scale with its working records, not retained dispatch history."""

from __future__ import annotations

import sqlite3
import json
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


def _finished_task_history(repo, count, task_id="task"):
    with sqlite3.connect(repo.store.path) as connection:
        connection.row_factory = sqlite3.Row
        template = dict(
            connection.execute("SELECT * FROM team_tasks WHERE id=?", (task_id,)).fetchone()
        )
        team_id = connection.execute(
            "SELECT team_id FROM team_projects WHERE id=?", (template["project_id"],)
        ).fetchone()[0]
        columns = tuple(template)
        records = []
        for index in range(count):
            row = {
                **template,
                "id": f"old-{task_id}-{index:06d}",
                "state": "finished",
                "case_id": f"old-case-{task_id}-{index:06d}",
                "parent_task_id": None,
                "work_lineage_id": None,
            }
            records.append(tuple(row[column] for column in columns))
        connection.executemany(
            "INSERT INTO team_tasks("
            + ",".join(columns)
            + ") VALUES("
            + ",".join("?" for _ in columns)
            + ")",
            records,
        )
        keys = [f"old-{task_id}-{index:06d}" for index in range(count)]
        instant = "2026-01-01T00:00:00+00:00"
        connection.executemany(
            "INSERT INTO team_deliverables(id,task_id,producer_agent_id,summary,state,created_at) VALUES (?,?,?,'Finished result','accepted',?)",
            ((f"d-{key}", key, template["owner_agent_id"], instant) for key in keys),
        )
        connection.executemany(
            "INSERT INTO team_task_deliverables(task_id,position,deliverable_id) VALUES (?,0,?)",
            ((key, f"d-{key}") for key in keys),
        )
        connection.executemany(
            "INSERT INTO team_review_requests(id,deliverable_id,reviewer_agent_id,state,created_at,decision_at) VALUES (?,?,'lead','accepted',?,?)",
            ((f"r-{key}", f"d-{key}", instant, instant) for key in keys),
        )
        connection.executemany(
            "INSERT INTO team_deliverable_reviews(deliverable_id,position,review_request_id) VALUES (?,0,?)",
            ((f"d-{key}", f"r-{key}") for key in keys),
        )
        connection.executemany(
            "INSERT INTO team_cases(id,team_id,project_id,title,description,status,priority,tags_json,metadata_json,task_ids_json,created_at,updated_at) VALUES (?,?,?,'Finished case','','closed','normal','[]','{}',?,?,?)",
            (
                (
                    f"old-case-{task_id}-{index:06d}",
                    team_id,
                    template["project_id"],
                    json.dumps([key]),
                    instant,
                    instant,
                )
                for index, key in enumerate(keys)
            ),
        )
        connection.executemany(
            "INSERT INTO team_release_packages(id,project_id,state,created_at,updated_at) VALUES (?,?,'accepted',?,?)",
            ((f"release-{key}", template["project_id"], instant, instant) for key in keys),
        )
        connection.executemany(
            "INSERT INTO team_release_deliverables(package_id,position,deliverable_id) VALUES (?,0,?)",
            ((f"release-{key}", f"d-{key}") for key in keys),
        )
        connection.execute("ANALYZE")


def _watch_lifecycle_hydration(monkeypatch):
    from evander_team_addon.lifecycle_scope import TeamLifecycleScope
    from collections import Counter

    counts = Counter()
    load = TeamLifecycleScope._load

    def measured(self, field, ids):
        rows = load(self, field, ids)
        counts[field] += len(rows)
        return rows

    monkeypatch.setattr(TeamLifecycleScope, "_load", measured)
    return counts


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
    _finished_task_history(repo, history_count)
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
    task_counts = []
    read_tasks = TeamStateSqliteStore._read_tasks

    def count_tasks(connection, payload, **kwargs):
        read_tasks(connection, payload, **kwargs)
        task_counts.append(len(payload["tasks"]))

    def count_dispatches(connection, payload, **kwargs):
        read_dispatches(connection, payload, **kwargs)
        decoded.append(len(payload["dispatches"]))

    with monkeypatch.context() as patch:
        hydration = _watch_lifecycle_hydration(patch)
        patch.setattr(TeamStateSqliteStore, "_read_dispatches", staticmethod(count_dispatches))
        patch.setattr(TeamStateSqliteStore, "_read_tasks", staticmethod(count_tasks))
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
        assert sum(task_counts) < 20
        for field in ("deliverables", "review_requests", "cases", "release_packages"):
            assert hydration[field] < 20, (field, hydration)
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


def test_manager_fallback_keeps_undismissed_failure_from_an_older_batch(tmp_path):
    from datetime import timedelta
    from evander_team_addon.lifecycle_scope import TeamLifecycleRepository
    from evander_team_addon.models import AgentActionRecord
    from evander_team_addon.services.domain.manager_fallback import (
        _manager_fallback_summary_payload,
    )

    repo = _seed_team(tmp_path)
    instant = datetime(2026, 1, 1, tzinfo=UTC)

    def seed(state):
        for key, success, offset in (("unresolved", False, 0), ("later-success", True, 1)):
            state.action_records[key] = AgentActionRecord(
                id=key,
                agent_id="backend",
                dispatch_id=key,
                success=success,
                error=None if success else "Needs supervisor attention",
                created_at=instant + timedelta(seconds=offset),
            )

    repo.update(seed)
    for repository in (repo, TeamLifecycleRepository(repo)):
        state = repository.read()
        summary = _manager_fallback_summary_payload(
            state, team=state.teams["team"], manager=state.agents["manager"], now=instant
        )
        assert summary["summary_counts"]["failed_action_count"] == 1
        assert summary["latest_failed_actions"][0]["id"] == "unresolved"


def test_later_keyed_task_assessment_refreshes_related_rework_evidence(tmp_path):
    from evander_team_addon.lifecycle_scope import TeamLifecycleRepository
    from evander_team_addon.services.domain.task_assessment import (
        TaskAssessmentService,
        TaskAssessmentBoundaryError,
        require_task_resolved,
    )

    repo = _seed_team(tmp_path)
    _finished_task_history(repo, 1)
    with sqlite3.connect(repo.store.path) as connection:
        connection.execute(
            "UPDATE team_deliverables SET rework_requested_at='2026-02-01T00:00:00+00:00', rework_requested_by_agent_id='lead', rework_reason='Replace accepted result'"
        )
    for repository in (repo, TeamLifecycleRepository(repo)):
        state = repository.read()
        assessment = TaskAssessmentService(state)
        # Construct the index before the historical task enters the working set.
        assert assessment.assess("task").resolution_status != "resolved"
        assert assessment.assess("old-task-000000").resolution_status != "resolved"
        with pytest.raises(TaskAssessmentBoundaryError) as error:
            require_task_resolved(state, "old-task-000000", boundary="release")
        assert "accepted_deliverable_requires_replacement" in str(error.value.correction_context)


@pytest.mark.parametrize("history_count", [1000, 10000])
@pytest.mark.parametrize("modern", [False, True])
def test_empty_legacy_lookup_ignores_other_agents_scopes_and_older_attempts(
    tmp_path, monkeypatch, history_count, modern
):
    from evander_team_addon.lifecycle_scope import TeamLifecycleRepository
    from evander_team_addon.models import AgentInboxItem

    repo = _seed_team(tmp_path)
    repo.update(
        lambda state: state.inbox.__setitem__(
            "old-lead",
            AgentInboxItem(
                id="old-lead",
                recipient_agent_id="lead",
                item_type="operator_directive",
                state="handled",
            ),
        )
    )
    _history(repo, history_count)
    with sqlite3.connect(repo.store.path) as connection:
        connection.execute("UPDATE team_dispatches SET agent_id='lead',session_id='session-lead'")
        connection.execute(
            "INSERT INTO team_dispatch_inbox SELECT id,0,'old-lead' FROM team_dispatches"
        )
        if modern:
            connection.execute(
                "INSERT INTO team_dispatches(id,agent_id,session_id,cause_kind,status,created_at,leased_at,completed_at,wake_work_fingerprint) VALUES ('modern','backend','session-backend','scheduled_wake','completed','2026-02-01T00:00:00+00:00','2026-02-01T00:00:00+00:00','2026-02-01T00:00:00+00:00','fingerprint')"
            )
            connection.executemany(
                "INSERT INTO team_dispatches(id,agent_id,session_id,cause_kind,status,created_at,leased_at,completed_at) VALUES (?,'backend','session-backend','scheduled_wake','completed','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')",
                ((f"empty-{i}",) for i in range(history_count)),
            )
        connection.execute("ANALYZE")
    counts, statements = [], []
    connect = repo.store._read_connection

    def measured():
        connection = connect()
        count = [0]
        counts.append(count)

        def progress():
            count[0] += 100
            return int(count[0] > 50000)

        connection.set_progress_handler(progress, 100)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(repo.store, "_read_connection", measured)
    with TeamLifecycleRepository(repo).command_snapshot() as state:
        records = state.latest_wake_work_records("backend")
        assert [row.id for row in records] == (["modern"] if modern else [])
    assert max(count[0] for count in counts) < 10000


@pytest.mark.parametrize("activity_kind", ["inbox", "review", "ephemeral"])
def test_terminal_activity_still_arms_idle_context_rotation(tmp_path, activity_kind):
    from datetime import timedelta
    from evander_team_addon.lifecycle_scope import TeamLifecycleRepository
    from evander_team_addon.models import (
        AgentInboxItem,
        AgentContextClearingPolicy,
        TeamTaskState,
        Deliverable,
        ReviewRequest,
        EphemeralAssignment,
    )
    from evander_team_addon.services.domain.context_rotation import (
        _maybe_mark_idle_context_rotation_due,
    )

    repo = _seed_team(tmp_path)
    rotated = datetime(2026, 1, 1, tzinfo=UTC)
    now = rotated + timedelta(minutes=2)

    def seed(state):
        task = state.tasks["task"]
        task.state = TeamTaskState.FINISHED
        task.created_at = task.updated_at = rotated - timedelta(days=1)
        runtime = state.agent_runtimes["backend"]
        runtime.last_context_rotated_at = rotated
        runtime.context_clearing_policy = AgentContextClearingPolicy.IDLE
        runtime.context_clearing_idle_grace_seconds = 3600
        runtime.last_progress_at = None
        if activity_kind == "review":
            task.deliverable_ids = ["accepted"]
            state.deliverables["accepted"] = Deliverable(
                id="accepted",
                task_id=task.id,
                producer_agent_id="lead",
                summary="Result",
                state="accepted",
                review_request_ids=["decision"],
            )
            state.review_requests["decision"] = ReviewRequest(
                id="decision",
                deliverable_id="accepted",
                reviewer_agent_id="backend",
                state="accepted",
                created_at=rotated - timedelta(days=1),
                decision_at=rotated + timedelta(minutes=1),
            )
            return
        if activity_kind == "ephemeral":
            state.ephemeral_assignments["cleaned"] = EphemeralAssignment(
                id="cleaned",
                parent_agent_id="backend",
                project_id="project",
                task_id=task.id,
                session_id="ephemeral-session",
                title="Finished work",
                prompt="Work",
                state="cleaned_up",
                created_at=rotated - timedelta(days=1),
                updated_at=rotated + timedelta(minutes=1),
            )
            return
        state.inbox["handled"] = AgentInboxItem(
            id="handled",
            recipient_agent_id="backend",
            item_type="progress_report",
            state="handled",
            created_at=rotated + timedelta(minutes=1),
            updated_at=rotated + timedelta(minutes=1),
        )

    repo.update(seed)
    for repository in (repo, TeamLifecycleRepository(repo)):
        state = repository.read()
        _maybe_mark_idle_context_rotation_due(
            state, agent_id="backend", now=now, result={"events": []}
        )
        assert state.agent_runtimes["backend"].context_idle_since_at == now


@pytest.mark.parametrize("history_count", [1000, 10000])
@pytest.mark.parametrize("lineage", [False, True])
def test_real_wake_planning_does_not_scan_unrelated_lineage_history(
    tmp_path, monkeypatch, history_count, lineage
):
    from evander_team_addon.lifecycle_scope import TeamLifecycleRepository
    from evander_team_addon.models import (
        AgentInboxItem,
        AgentContextClearingPolicy,
        TeamDispatch,
        TeamWorkLineage,
    )

    repo = _seed_team(tmp_path)
    _history(repo, history_count)

    def seed(state):
        if lineage:
            state.work_lineages["new-lineage"] = TeamWorkLineage(id="new-lineage")
            state.tasks["task"].work_lineage_id = "new-lineage"
            state.agent_runtimes[
                "backend"
            ].context_clearing_policy = AgentContextClearingPolicy.WORK_LINEAGE
        state.inbox["new-work"] = AgentInboxItem(
            id="new-work",
            recipient_agent_id="backend",
            task_id="task",
            item_type="operator_directive",
            payload={"body": "Continue task"},
        )
        # A real scope belonging to another agent must not make the backend's
        # empty legacy lookup walk all its fingerprint-free headers.
        state.inbox["old-lead-work"] = AgentInboxItem(
            id="old-lead-work",
            recipient_agent_id="lead",
            item_type="operator_directive",
            state="handled",
        )
        instant = datetime(2026, 1, 1, tzinfo=UTC)
        state.dispatches["unrelated-agent"] = TeamDispatch(
            id="unrelated-agent",
            agent_id="lead",
            session_id="session-lead",
            cause_kind="scheduled_wake",
            status="completed",
            inbox_item_ids=["old-lead-work"],
            created_at=instant,
            leased_at=instant,
            completed_at=instant,
            activation_completed_at=instant,
        )

    repo.update(seed)
    _finished_task_history(repo, history_count)
    hydration = _watch_lifecycle_hydration(monkeypatch)
    connect = repo.store._read_connection
    counts, statements = [], []

    def bounded_connection():
        connection = connect()
        count = [0]
        counts.append(count)

        def progress():
            count[0] += 100
            return int(count[0] > 50000)

        connection.set_progress_handler(progress, 100)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(repo.store, "_read_connection", bounded_connection)
    _, leases = TeamRuntimeService(TeamLifecycleRepository(repo)).lease_due_agents(
        agent_id="backend"
    )
    assert len(leases) == 1
    for field in ("tasks", "deliverables", "review_requests", "cases", "release_packages"):
        assert hydration[field] < 30, (field, hydration)
    assert max(count[0] for count in counts) < 50000
    lineage_queries = [
        sql
        for sql in statements
        if "SELECT id FROM team_dispatches WHERE agent_id=" in sql and "work_lineage_id IS " in sql
    ]
    assert bool(lineage_queries) is lineage
    with sqlite3.connect(repo.store.path) as connection:
        for sql in lineage_queries:
            plan = [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + sql)]
            assert any(
                "idx_team_dispatches_lineage_attempt (agent_id=? AND work_lineage_id=?)" in row
                for row in plan
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
