from datetime import timedelta
from types import SimpleNamespace

import pytest

from evander.models import utc_now
from evander_team_addon.models import (
    AgentRuntime,
    AgentTask,
    PersistentAgent,
    Project,
    ProjectStatus,
    Team,
    TeamDispatch,
    TeamDispatchStatus,
    TeamState,
    TeamTaskState,
)
from evander_team_addon.services.commands.types import TeamCommandValidationError
from evander_team_addon.services.task_resource_scope import TaskResourceAuthority


@pytest.fixture
def resource_state():
    state = TeamState.empty()
    state.teams["team-a"] = Team(id="team-a", name="Test team", manager_agent_id="agent-a")
    state.agents["agent-a"] = PersistentAgent(
        id="agent-a", team_id="team-a", role_id="developer", session_id="session-a"
    )
    state.projects["project-a"] = Project(
        id="project-a", team_id="team-a", name="Synthetic project", lead_agent_id="agent-a"
    )
    state.tasks["task-a"] = AgentTask(
        id="task-a",
        owner_agent_id="agent-a",
        project_id="project-a",
        priority=1,
        title="Verify application",
        description="Use synthetic infrastructure",
        state="blocked",
        created_by_actor="system:test",
    )
    state.agent_runtimes["agent-a"] = AgentRuntime(
        agent_id="agent-a",
        active_dispatch_id="dispatch-a",
        wake_leased_until=utc_now() + timedelta(minutes=10),
    )
    state.dispatches["dispatch-a"] = TeamDispatch(
        id="dispatch-a",
        agent_id="agent-a",
        session_id="session-a",
        cause_kind="task",
        task_ids=["task-a"],
        project_ids=["project-a"],
        status="leased",
        leased_at=utc_now(),
        lease_expires_at=utc_now() + timedelta(minutes=10),
    )
    return state


def authority(state):
    return TaskResourceAuthority(SimpleNamespace(read=lambda: state))


def resolve(state):
    return authority(state).resolve(
        session_id="session-a", task_id="task-a", tool_name="test_fixtures_acquire"
    )


def test_blocked_task_can_acquire_without_broadening_project_authority(resource_state):
    scope = resolve(resource_state)
    assert (scope.agent_id, scope.project_id, scope.dispatch_id) == (
        "agent-a",
        "project-a",
        "dispatch-a",
    )
    assert resource_state.tasks["task-a"].state.value == "blocked"


@pytest.mark.parametrize(
    "change",
    [
        lambda s: setattr(s.dispatches["dispatch-a"], "task_ids", []),
        lambda s: setattr(s.dispatches["dispatch-a"], "project_ids", []),
        lambda s: setattr(s.dispatches["dispatch-a"], "session_id", "other-session"),
        lambda s: setattr(s.dispatches["dispatch-a"], "status", TeamDispatchStatus.COMPLETED),
        lambda s: setattr(
            s.dispatches["dispatch-a"], "lease_expires_at", utc_now() - timedelta(seconds=1)
        ),
        lambda s: setattr(
            s.agent_runtimes["agent-a"], "wake_leased_until", utc_now() - timedelta(seconds=1)
        ),
        lambda s: setattr(s.agent_runtimes["agent-a"], "active_dispatch_id", None),
        lambda s: setattr(s.tasks["task-a"], "owner_agent_id", "other-agent"),
        lambda s: setattr(s.tasks["task-a"], "state", TeamTaskState.CANCELLED),
        lambda s: setattr(s.tasks["task-a"], "state", TeamTaskState.ACCEPTED),
        lambda s: setattr(s.projects["project-a"], "team_id", "other-team"),
        lambda s: setattr(s.projects["project-a"], "status", ProjectStatus.ARCHIVED),
        lambda s: setattr(s.teams["team-a"], "archived", True),
    ],
)
def test_resource_authority_rejects_stale_or_foreign_scope(resource_state, change):
    change(resource_state)
    with pytest.raises(TeamCommandValidationError):
        resolve(resource_state)


def test_project_chat_visibility_does_not_grant_task_resource_authority(resource_state):
    resource_state.dispatches["dispatch-a"].cause_kind = "team_chat"
    resource_state.dispatches["dispatch-a"].task_ids = []
    with pytest.raises(TeamCommandValidationError):
        resolve(resource_state)


def test_idle_ownership_survives_dispatch_end_but_not_reassignment(resource_state):
    checker = authority(resource_state)
    scope = resolve(resource_state)
    resource_state.agent_runtimes["agent-a"].active_dispatch_id = None
    assert checker.owns(scope)
    resource_state.tasks["task-a"].owner_agent_id = "other-agent"
    assert not checker.owns(scope)


def test_sql_provisioning_failure_retains_exact_principal_for_retry_and_cleanup(
    tmp_path, monkeypatch
):
    from evander.worker.mssql import WorkerMssqlProvisioner, WorkerMssqlStore
    from evander.worker.models import WorkerMssqlDatabaseRequest

    store = WorkerMssqlStore(tmp_path / "sql.json")
    sql = WorkerMssqlProvisioner(enabled=True, store=store, admin_password="synthetic")

    def fail(_):
        raise RuntimeError("partial SQL failure")

    monkeypatch.setattr(sql, "_run_sql", fail)
    with pytest.raises(RuntimeError):
        sql.provision_database("fixture-owner", WorkerMssqlDatabaseRequest(logical_name="source"))
    intent = store.get("fixture-owner")
    assert intent and "source" in intent.databases
    monkeypatch.setattr(sql, "_run_sql", lambda _: None)
    record = sql.provision_database(
        "fixture-owner", WorkerMssqlDatabaseRequest(logical_name="source")
    )
    assert record.username == intent.login_name
    assert record.password == intent.password
    sql.drop_session("fixture-owner")
    assert store.get("fixture-owner") is None


def test_sql_password_is_sent_through_stdin_with_bounded_execution(tmp_path, monkeypatch):
    from evander.worker.mssql import WorkerMssqlProvisioner, WorkerMssqlStore
    from evander.worker.models import WorkerMssqlDatabaseRequest

    calls = []
    monkeypatch.setattr(
        "evander.worker.mssql.subprocess.run", lambda args, **kwargs: calls.append((args, kwargs))
    )
    sql = WorkerMssqlProvisioner(
        enabled=True,
        store=WorkerMssqlStore(tmp_path / "sql.json"),
        admin_password="synthetic-admin",
    )
    record = sql.provision_database(
        "lease-owner", WorkerMssqlDatabaseRequest(logical_name="source")
    )
    args, kwargs = calls[0]
    assert record.password not in " ".join(args) and "synthetic-admin" not in " ".join(args)
    assert record.password in kwargs["input"]
    assert kwargs["timeout"] == 45
