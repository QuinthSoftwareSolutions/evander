from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from evander_team_addon.models import (
    AgentRuntime,
    AgentTask,
    Deliverable,
    PersistentAgent,
    Project,
    Team,
    TeamDispatch,
    TeamDispatchStatus,
    TeamOutboxRecord,
    TeamState,
)
from evander_team_addon.repository import TeamStateConflictError, TeamStateRepository
from evander_team_addon.services.domain import dispatch_scope, task_assessment
from evander_team_addon.services.domain.project_status import project_blocking_sources
from evander_team_addon.services.domain.task_assessment import TaskAssessmentService
from evander_team_addon.services import read_service
from evander_team_addon.services.runtime_service import TeamRuntimeService
from evander_team_addon.sqlite_store import _json_values_equal

NOW = datetime(2026, 9, 22, 12, tzinfo=UTC)


def _read_state(task_count: int = 40) -> TeamState:
    agent = PersistentAgent(id="agent", team_id="team", role_id="role", session_id="session")
    return TeamState.model_construct(
        agents={agent.id: agent},
        teams={"team": Team(id="team", name="Team", manager_agent_id=agent.id)},
        projects={"project": Project(
            id="project", team_id="team", name="Project", lead_agent_id=agent.id,
        )},
        tasks={f"task-{i}": AgentTask(
            id=f"task-{i}", owner_agent_id=agent.id, project_id="project", priority=i,
            title=f"Task {i}", description="Work", created_by_actor="operator",
            created_at=NOW, updated_at=NOW,
        ) for i in range(task_count)},
    )


def test_task_page_enriches_only_requested_rows_and_shares_assessment(monkeypatch) -> None:
    state = _read_state()
    state.tasks["hidden"] = AgentTask(
        id="hidden", owner_agent_id="other", project_id="elsewhere", priority=999,
        title="Hidden", description="Work", created_by_actor="operator",
    )
    constructions = []
    original = read_service.TaskAssessmentService

    def construct(snapshot):
        constructions.append(snapshot)
        return original(snapshot)

    monkeypatch.setattr(read_service, "TaskAssessmentService", construct)
    service = read_service.TeamReadService(state=state, actor=state.agents["agent"])
    enriched = []
    original_row = service._task_row

    def row(task):
        enriched.append(task.id)
        return original_row(task)

    monkeypatch.setattr(service, "_task_row", row)
    page = service.list_tasks(limit=3, cursor="2")
    assert [row["id"] for row in page["records"]] == ["task-37", "task-36", "task-35"]
    assert enriched == ["task-37", "task-36", "task-35"]
    assert constructions == [state]
    assert page["total"] == page["total_count"] == 40
    assert page["offset"] == 2
    assert page["next_cursor"] == "5"
    assert page["omitted_count"] == 35
    assert page["has_more"] is True
    assert service.list_tasks(limit=1, offset=100)["records"] == []
    assert len(enriched) == 3


def test_project_page_enriches_only_requested_rows(monkeypatch) -> None:
    state = _read_state()
    for i in range(10):
        state.projects[f"project-{i}"] = Project(
            id=f"project-{i}", name=f"Project {i}", team_id="team", lead_agent_id="agent",
        )
    service = read_service.TeamReadService(state=state, actor=state.agents["agent"])
    enriched = []
    original = service._project_row

    def row(project):
        enriched.append(project.id)
        return original(project)

    monkeypatch.setattr(service, "_project_row", row)
    page = service.list_projects(limit=2, offset=3)
    assert enriched == ["project-2", "project-3"]
    assert page["total"] == 11
    assert page["next_cursor"] == "5"


def test_assessment_reuses_deliverable_index_for_all_tasks() -> None:
    class GuardedDeliverables(dict):
        allow_scan = True

        def values(self):
            assert self.allow_scan, "assessment rescanned all deliverables after indexing"
            return super().values()

    state = _read_state()
    deliverables = GuardedDeliverables({
        f"deliverable-{i}": Deliverable(
            id=f"deliverable-{i}", task_id=f"task-{i}", producer_agent_id="agent", summary="Done",
        ) for i in range(40)
    })
    state.deliverables = deliverables
    for row in deliverables.values():
        state.tasks[row.task_id].deliverable_ids = [row.id]
    expected = TaskAssessmentService(state).assess_all()
    service = TaskAssessmentService(state)
    deliverables.allow_scan = False
    assert service.assess_all() == expected


def test_project_blockers_assess_only_project_tasks_with_shared_snapshot(monkeypatch) -> None:
    state = _read_state(2)
    state.tasks["task-1"].project_id = "elsewhere"
    service = TaskAssessmentService(state)
    assessed = []
    original = service.assess

    def assess(task_id):
        assessed.append(task_id)
        return original(task_id)

    monkeypatch.setattr(service, "assess", assess)
    project_blocking_sources(state, "project", assessment_service=service)
    assert assessed == ["task-0"]


def test_idle_agent_does_not_construct_global_assessment(monkeypatch) -> None:
    state = _read_state()
    idle = PersistentAgent(id="idle", team_id="team", role_id="role", session_id="idle-session")

    def fail(_state):
        raise AssertionError("idle agent should not rebuild the task graph")

    monkeypatch.setattr(dispatch_scope, "TaskAssessmentService", fail)
    assert dispatch_scope.actionable_dispatch_tasks(state, agent=idle) == []


@pytest.mark.parametrize("command", [False, True])
def test_update_refreshes_stale_baseline_before_callback_and_command_writes(
    tmp_path, monkeypatch, command,
) -> None:
    repository = TeamStateRepository(tmp_path)
    repository.update(lambda state: state.agents.update({
        "agent": PersistentAgent(id="agent", session_id="session"),
    }) or state)
    other = TeamStateRepository(tmp_path)
    read = repository.store._read_state
    reads = []
    callbacks = []
    outbox = TeamOutboxRecord(
        id="outbox", intent_type="team.test", aggregate_type="agent", aggregate_id="agent",
    )

    def snapshot(connection):
        state = read(connection)
        reads.append(state.revision)
        if len(reads) == 1:
            # An independent writer can commit while reconstruction holds only
            # its WAL read snapshot. The first baseline is now deliberately old.
            other.update(lambda current: setattr(
                current.agents["agent"], "display_name", "Concurrent update",
            ) or current)
        return state

    def mutate(state, tx=None):
        callbacks.append(state.revision)
        assert state.agents["agent"].display_name == "Concurrent update"
        state.agents["agent"].display_name = "Final update"
        if tx is not None:
            tx.enqueue_outbox_record(outbox)
        return state

    monkeypatch.setattr(repository.store, "_read_state", snapshot)
    result = (
        repository.update_with_command_transaction(mutate)
        if command else repository.update(mutate)
    )
    assert reads == [1, 2]
    assert callbacks == [2]
    assert result.revision == 3
    assert other.read().agents["agent"].display_name == "Final update"
    assert [row.id for row in repository.list_outbox_records()] == (["outbox"] if command else [])


def test_explicit_stale_revision_rejects_before_callback(tmp_path, monkeypatch) -> None:
    repository = TeamStateRepository(tmp_path)
    other = TeamStateRepository(tmp_path)
    read = repository.store._read_state
    called = []
    refreshed = []

    def snapshot(connection):
        state = read(connection)
        if not refreshed:
            refreshed.append(True)
            other.update(lambda current: current.agents.update({
                "agent": PersistentAgent(id="agent", session_id="session"),
            }) or current)
        return state

    monkeypatch.setattr(repository.store, "_read_state", snapshot)
    with pytest.raises(TeamStateConflictError):
        repository.update(lambda state: called.append(True) or state, expected_revision=0)
    assert called == []


@pytest.mark.parametrize("before,after", [
    ({"value": True}, {"value": 1}),
    ({"value": 1}, {"value": 1.0}),
    ({"value": -0.0}, {"value": 0.0}),
    ({"value": float("nan")}, {"value": float("nan")}),
    ({"a": [1, "text", None], "b": False}, {"b": False, "a": [1, "text", None]}),
])
def test_record_comparison_preserves_canonical_json_semantics(before, after) -> None:
    assert _json_values_equal(before, after) == (
        json.dumps(before, sort_keys=True) == json.dumps(after, sort_keys=True)
    )


def _leased_repository(tmp_path) -> TeamStateRepository:
    repository = TeamStateRepository(tmp_path)
    state = TeamState(
        agents={"agent": PersistentAgent(id="agent", session_id="session")},
        agent_runtimes={"agent": AgentRuntime(
            agent_id="agent", active_dispatch_id="dispatch", wake_leased_until=NOW,
        )},
        dispatches={"dispatch": TeamDispatch(
            id="dispatch", agent_id="agent", session_id="session", cause_kind="task",
            status=TeamDispatchStatus.LEASED, leased_at=NOW - timedelta(minutes=10),
            lease_expires_at=NOW,
        )},
    )
    repository.store.write_object(state.model_dump(mode="json"))
    return repository


def test_maintenance_lease_renewal_does_not_read_historical_state(tmp_path, monkeypatch) -> None:
    repository = _leased_repository(tmp_path)
    statements = []
    connect = repository.store._connect

    def traced_connect():
        connection = connect()
        connection.set_trace_callback(statements.append)
        return connection

    def fail(*args, **kwargs):
        raise AssertionError("lease renewal must not reconstruct complete Team state")

    monkeypatch.setattr(repository.store, "_connect", traced_connect)
    monkeypatch.setattr(repository.store, "_read_state", fail)
    state, renewed = TeamRuntimeService(repository).renew_agent_dispatch_lease(
        agent_id="agent", dispatch_id="dispatch", now=NOW, include_state=False,
    )
    assert renewed and state is None
    assert not any("DELETE " in statement or "INSERT " in statement for statement in statements)
    persisted = TeamStateRepository(tmp_path).read()
    assert persisted.revision == 1
    assert persisted.dispatches["dispatch"].lease_expires_at == NOW + timedelta(seconds=900)
    assert persisted.agent_runtimes["agent"].wake_leased_until == NOW + timedelta(seconds=900)
    assert persisted.agent_runtimes["agent"].updated_at == NOW


@pytest.mark.parametrize("disposition", ["terminal", "replaced", "not_due", "wrong_agent"])
def test_narrow_lease_renewal_never_revives_or_updates_unrelated_dispatch(
    tmp_path, disposition,
) -> None:
    repository = _leased_repository(tmp_path)

    def mutate(state):
        if disposition == "terminal":
            state.dispatches["dispatch"].status = TeamDispatchStatus.COMPLETED
            state.dispatches["dispatch"].completed_at = NOW
            state.agent_runtimes["agent"].active_dispatch_id = None
        elif disposition == "replaced":
            state.agent_runtimes["agent"].active_dispatch_id = None
        elif disposition == "not_due":
            state.dispatches["dispatch"].lease_expires_at = NOW + timedelta(hours=1)
            state.agent_runtimes["agent"].wake_leased_until = NOW + timedelta(hours=1)
        return state

    before = repository.update(mutate)
    returned, renewed = TeamRuntimeService(repository).renew_agent_dispatch_lease(
        agent_id="other" if disposition == "wrong_agent" else "agent",
        dispatch_id="dispatch", now=NOW,
    )
    assert not renewed
    assert returned == before
    assert repository.read() == before


def test_narrow_lease_renewal_preserves_snapshot_revision_conflict(tmp_path, monkeypatch) -> None:
    repository = _leased_repository(tmp_path)
    other = TeamStateRepository(tmp_path)
    renew = repository.renew_dispatch_lease

    def race(**kwargs):
        other.update(lambda state: setattr(state.agents["agent"], "display_name", "Other") or state)
        return renew(**kwargs)

    monkeypatch.setattr(repository, "renew_dispatch_lease", race)
    with pytest.raises(TeamStateConflictError):
        TeamRuntimeService(repository).renew_agent_dispatch_lease(
            agent_id="agent", dispatch_id="dispatch", now=NOW,
        )
    assert other.read().dispatches["dispatch"].lease_expires_at == NOW


def test_precomputed_payload_assessments_do_not_rebuild_indexes(monkeypatch) -> None:
    state = _read_state()
    assessments = TaskAssessmentService(state).assess_all()

    def fail(*args, **kwargs):
        raise AssertionError("precomputed assessment must serve child and blocker projections")

    monkeypatch.setattr(task_assessment, "TaskAssessmentService", fail)
    monkeypatch.setattr(TeamState, "child_task_ids", fail)
    for task in state.tasks.values():
        payload = task_assessment.task_payload_with_assessment(
            state, task, assessment=assessments[task.id], assessments=assessments,
        )
        assert payload["child_task_ids"] == []
        assert payload["inherited_blockers"] == []
